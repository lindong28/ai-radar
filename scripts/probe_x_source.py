#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from airadar import db  # noqa: E402
from airadar.audit.receipts import (  # noqa: E402
    X_PROBE_RECEIPT_CODE_PATHS,
    atomic_json,
    canonical_payload_sha256,
    validate_x_offline_proof_receipt,
    validate_x_probe_receipt,
)
from airadar.fetcher import runner  # noqa: E402
from airadar.runtime_env import read_value  # noqa: E402
from airadar.sources.loader import load_sources  # noqa: E402
from airadar.sources.sync import load_enabled_sources_from_db, sync_to_db  # noqa: E402
from airadar.sources.x_state import validate_x_runtime_meta  # noqa: E402

CONFIG = ROOT / "data/sources.toml"
CONTRACT = ROOT / "tests/fixtures/aihot_sources.json"
CODE_PATHS = X_PROBE_RECEIPT_CODE_PATHS


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_probe_target(source: str, db_path: Path) -> Path:
    if source != "x_openai":
        raise ValueError("bounded validation permits only --source x_openai")
    resolved = db_path.expanduser().resolve()
    if resolved == db.DEFAULT_DB_PATH.resolve():
        raise ValueError("refusing default DB path")
    if resolved.exists():
        raise ValueError(f"refusing existing DB path: {resolved}")
    return resolved


def _offline_proof() -> dict[str, object]:
    path = ROOT / "artifacts/x-pagination-offline-receipt.json"
    if not path.is_file():
        return {"status": "not_evaluated", "receipt_path_relative_to_repository_root": None, "receipt_sha256": None}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"status": "not_evaluated", "receipt_path_relative_to_repository_root": None, "receipt_sha256": None}
    try:
        validate_x_offline_proof_receipt(payload, repository_root=ROOT)
    except ValueError:
        return {"status": "not_evaluated", "receipt_path_relative_to_repository_root": None, "receipt_sha256": None}
    return {
        "status": "verified",
        "receipt_path_relative_to_repository_root": path.relative_to(ROOT).as_posix(),
        "receipt_sha256": _hash(path),
    }


def _base(source: str, token_present: bool, captured_at: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "artifact_type": "x_single_source_probe",
        "status": "failed",
        "captured_at": captured_at,
        "phase": "setup",
        "probe_scope": {"identity_requests_max": 1, "timeline_requests_max": 1, "lookback_minutes": 20, "max_results": 5, "backfill": False},
        "source": source,
        "token_present": token_present,
        "state_scope": None,
        "request_counts": {
            "identity_http_request_count": 0,
            "timeline_http_request_count": 0,
        },
        "http_responses": {
            "identity_http_status_code": None,
            "timeline_http_status_code": None,
        },
        "config_sha256": _hash(CONFIG), "contract_sha256": _hash(CONTRACT),
        "code_sha256_by_path_relative_to_repository_root": {
            path: _hash(ROOT / path) for path in CODE_PATHS
        },
        "database_state_after_probe": None,
        "live_validation": {
            "identity_connectivity_verified": False,
            "timeline_connectivity_verified": False,
            "terminal_checkpoint_verified": False,
            "live_post_retrieval_verified": False,
            "fetched_item_count": 0,
            "inserted_item_count": 0,
        },
        "offline_proof": _offline_proof(),
        "failures": [],
        "recovery": None,
    }


def _finish(output: Path, report: dict[str, object]) -> dict[str, object]:
    report["report_payload_sha256"] = canonical_payload_sha256(report)
    validate_x_probe_receipt(
        report,
        config_path=CONFIG,
        contract_path=CONTRACT,
        repository_root=ROOT,
    )
    atomic_json(output, report)
    return report


def probe(source_slug: str, db_path: Path, output: Path) -> dict[str, object]:
    resolved_db = validate_probe_target(source_slug, db_path)
    captured_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    token_present = bool(read_value("X_BEARER_TOKEN").strip())
    report = _base(source_slug, token_present, captured_at)
    if not token_present:
        report.update(
            phase="token_check",
            failures=[{
                "reason": "missing_token",
                "error_class": "MissingConfiguration",
                "http_status_code": None,
            }],
            recovery="configure_X_BEARER_TOKEN_then_rerun",
        )
        return _finish(output, report)
    offline = report["offline_proof"]
    assert isinstance(offline, dict)
    if offline.get("status") != "verified":
        report.update(
            phase="offline_proof_validation",
            failures=[{
                "reason": "offline_pagination_proof_not_verified",
                "error_class": "MissingEvidence",
                "http_status_code": None,
            }],
            recovery="produce_a_valid_offline_pagination_test_receipt_then_rerun",
        )
        return _finish(output, report)

    selected = next(source for source in load_sources(CONFIG) if source.slug == source_slug)
    db.migrate(resolved_db)
    conn = db.get_conn(resolved_db)
    try:
        sync_to_db([selected], conn)
        summaries: list[runner.SourceFetchSummary] = []
        for phase in ("identity_request", "timeline_request"):
            current = load_enabled_sources_from_db(conn)[0]
            summary = runner.fetch_source(conn, current)
            summaries.append(summary)
            counts = report["request_counts"]
            responses = report["http_responses"]
            live = report["live_validation"]
            assert isinstance(counts, dict) and isinstance(responses, dict) and isinstance(live, dict)
            request_key = "identity_http_request_count" if phase == "identity_request" else "timeline_http_request_count"
            status_key = "identity_http_status_code" if phase == "identity_request" else "timeline_http_status_code"
            counts[request_key] = int(counts[request_key]) + 1
            responses[status_key] = summary.http_status_code
            if isinstance(summary.http_status_code, int) and 200 <= summary.http_status_code < 300:
                connectivity_key = (
                    "identity_connectivity_verified"
                    if phase == "identity_request"
                    else "timeline_connectivity_verified"
                )
                live[connectivity_key] = True
            if summary.error:
                reason = "authentication_rejected" if summary.http_status_code == 401 else "http_or_runtime_failure"
                meta = dict(load_enabled_sources_from_db(conn)[0].meta)
                meta.update(
                    x_reference_status="blocked", x_reference_attempted_at=captured_at,
                    x_reference_reason=reason, x_reference_recovery="replace_or_confirm_token_then_rerun_single_source_probe",
                )
                conn.execute("UPDATE sources SET meta_json=? WHERE id=?", (json.dumps(meta, ensure_ascii=False, sort_keys=True, separators=(",", ":")), source_slug))
                conn.commit()
                reread = load_enabled_sources_from_db(conn)[0]
                report.update(
                    phase=phase,
                    database_state_after_probe=validate_x_runtime_meta(reread.meta, context=source_slug),
                    failures=[{
                        "reason": reason,
                        "error_class": "SourceFetchError",
                        "http_status_code": summary.http_status_code,
                    }],
                    recovery="replace_or_confirm_token_then_rerun_single_source_probe",
                )
                return _finish(output, report)
            if summary.http_status_code is None or not 200 <= summary.http_status_code < 300:
                report.update(
                    phase=phase,
                    database_state_after_probe=validate_x_runtime_meta(
                        load_enabled_sources_from_db(conn)[0].meta,
                        context=source_slug,
                    ),
                    failures=[{
                        "reason": "missing_success_http_status",
                        "error_class": "EvidenceError",
                        "http_status_code": summary.http_status_code,
                    }],
                    recovery="repair_http_status_propagation_then_rerun_single_source_probe",
                )
                return _finish(output, report)
            if phase == "timeline_request":
                live["fetched_item_count"] = summary.fetched
                live["inserted_item_count"] = summary.inserted
                live["live_post_retrieval_verified"] = summary.fetched > 0
            if phase == "identity_request" and load_enabled_sources_from_db(conn)[0].meta.get("x_cursor_state") == "identity_pending":
                report.update(
                    phase=phase,
                    database_state_after_probe=validate_x_runtime_meta(
                        load_enabled_sources_from_db(conn)[0].meta,
                        context=source_slug,
                    ),
                    failures=[{
                        "reason": "identity_state_did_not_advance",
                        "error_class": "StateTransitionError",
                        "http_status_code": summary.http_status_code,
                    }],
                    recovery="inspect_identity_response_then_rerun",
                )
                return _finish(output, report)

        final = load_enabled_sources_from_db(conn)[0]
        state = validate_x_runtime_meta(final.meta, context=source_slug)
        live = report["live_validation"]
        assert isinstance(live, dict)
        state_scope = "terminal_checkpoint" if state["x_cursor_state"] == "checkpointed" else "draining_connectivity"
        live["terminal_checkpoint_verified"] = state_scope == "terminal_checkpoint"
        report.update(
            status="success",
            phase="complete",
            state_scope=state_scope,
            database_state_after_probe=state,
            failures=[],
            recovery=None,
        )
        return _finish(output, report)
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = probe(args.source, args.db, args.output)
    except Exception as exc:
        captured_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        report = _base(args.source, bool(read_value("X_BEARER_TOKEN").strip()), captured_at)
        report.update(
            phase="setup",
            failures=[{
                "reason": "local_setup_failure",
                "error_class": type(exc).__name__,
                "http_status_code": None,
            }],
            recovery="fix_local_setup_then_rerun",
        )
        _finish(args.output, report)
    return 0 if report["status"] == "success" else 2


if __name__ == "__main__":
    raise SystemExit(main())
