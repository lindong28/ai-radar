#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from airadar import db  # noqa: E402
from airadar.audit.completeness_oracle import enumerate_feed, enumerate_web  # noqa: E402
from airadar.audit.receipts import (  # noqa: E402
    NON_X_RECEIPT_CODE_PATHS,
    atomic_json,
    canonical_item_set,
    canonical_payload_sha256,
    validate_non_x_receipt,
)
from airadar.fetcher import runner  # noqa: E402
from airadar.fetcher.http_client import FeedResponse, fetch_document  # noqa: E402
from airadar.fetcher.rss import parse_feed  # noqa: E402
from airadar.fetcher.web import parse_web_source  # noqa: E402
from airadar.sources.contract import load_source_contract  # noqa: E402
from airadar.sources.loader import SourceConfig, load_sources  # noqa: E402
from airadar.sources.sync import load_enabled_sources_from_db, sync_to_db  # noqa: E402

CONTRACT = ROOT / "tests/fixtures/aihot_sources.json"
CODE_PATHS = NON_X_RECEIPT_CODE_PATHS


@dataclass(frozen=True)
class Captured:
    source: SourceConfig
    response: FeedResponse
    meta_update: object | None
    attempts: int


class CaptureFailure(RuntimeError):
    def __init__(self, cause: Exception, attempts: int) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.attempts = attempts


def ensure_new_db_path(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved == db.DEFAULT_DB_PATH.resolve():
        raise ValueError("refusing default production DB path")
    if resolved.exists():
        raise ValueError(f"refusing existing DB path: {resolved}")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def sets_equal(expected: set[tuple[str, str]], actual: set[tuple[str, str]], source: str) -> None:
    if expected != actual:
        missing = sorted(expected - actual)[:5]
        extra = sorted(actual - expected)[:5]
        raise ValueError(f"independent completeness mismatch for {source}: missing={missing}, extra={extra}")


def _item_set(items: list[object]) -> set[tuple[str, str]]:
    return {(str(item.url), str(item.title)) for item in items}  # type: ignore[attr-defined]


def _persisted_set(conn: sqlite3.Connection, slug: str) -> set[tuple[str, str]]:
    return {(str(row[0]), str(row[1])) for row in conn.execute("SELECT url,title FROM items WHERE source_id=?", (slug,))}


def _capture(source: SourceConfig) -> Captured:
    capture = runner._SourceFeedMetaCapture()
    accept = "text/html, application/json;q=0.9, */*;q=0.1" if source.kind == "web" else "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"
    attempts = 0
    while True:
        attempts += 1
        try:
            response = fetch_document(source, capture, accept=accept, timeout=45)
            return Captured(source, response, capture.meta_update, attempts)
        except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            if attempts >= 2:
                raise CaptureFailure(exc, attempts) from exc
        except httpx.HTTPStatusError as exc:
            if attempts >= 2 or exc.response.status_code < 500:
                raise CaptureFailure(exc, attempts) from exc


def _parse(captured: Captured) -> tuple[list[object], set[tuple[str, str]]]:
    source, response = captured.source, captured.response
    if response.not_modified:
        return [], set()
    if source.kind == "web":
        items = parse_web_source(source, response)
        expected = enumerate_web(source.slug, response.body, response.final_url or source.url)
    else:
        items = parse_feed(source, response.body)
        expected = enumerate_feed(source.slug, source.url, response.body)
    sets_equal(expected, _item_set(items), source.slug)
    return items, expected


def _apply(conn: sqlite3.Connection, captured: Captured, items: list[object]) -> runner.SourceFetchSummary:
    result = runner._SourceFeedResult(source=captured.source, response=captured.response, items=items, meta_update=captured.meta_update)  # type: ignore[arg-type]
    summary = runner._apply_source_feed_result(conn, result)
    if summary.error:
        raise ValueError(summary.error)
    return summary


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _structured_failure(
    source_id: str | None,
    stage: str,
    exc: Exception,
    *,
    request_attempt_count: int = 0,
    http_status_code: int | None = None,
) -> dict[str, object]:
    cause = exc.cause if isinstance(exc, CaptureFailure) else exc
    attempts = exc.attempts if isinstance(exc, CaptureFailure) else request_attempt_count
    status = cause.response.status_code if isinstance(cause, httpx.HTTPStatusError) else http_status_code
    return {
        "source_id": source_id,
        "error_class": type(cause).__name__,
        "error_message": str(cause),
        "request_attempt_count": attempts,
        "http_status_code": status,
    }


def _set_evidence(items: set[tuple[str, str]]) -> dict[str, object]:
    return canonical_item_set(items)


def _seal_and_write(report: dict[str, object], output: Path, config: Path) -> dict[str, object]:
    report["report_payload_sha256"] = canonical_payload_sha256(report)
    validate_non_x_receipt(
        report,
        config_path=config,
        contract_path=CONTRACT,
        repository_root=ROOT,
    )
    atomic_json(output, report)
    return report


def audit(config: Path, db_path: Path, output: Path) -> dict[str, object]:
    resolved_db = ensure_new_db_path(db_path)
    sources = [source for source in load_sources(config) if source.enabled and source.kind in {"feed", "web"}]
    contract_rows = [
        row for row in load_source_contract(CONTRACT)["sources"]
        if row["kind"] in {"feed", "web"}
    ]
    expected_membership = {row["slug"]: row["kind"] for row in contract_rows}
    captured_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    report: dict[str, object] = {
        "schema_version": 1,
        "artifact_type": "non_x_retrieval",
        "status": "failed",
        "captured_at": captured_at,
        "phase": "setup",
        "probe_scope": {"kinds": ["feed", "web"], "live_round_count": 2, "immutable_replay_round_count": 1},
        "database_path_absolute": str(resolved_db),
        "config_sha256": _hash(config),
        "contract_sha256": _hash(CONTRACT),
        "code_sha256_by_path_relative_to_repository_root": {
            path: _hash(ROOT / path) for path in CODE_PATHS
        },
        "source_counts": {
            "contract_non_x_source_count": len(expected_membership),
            "contract_feed_source_count": sum(kind == "feed" for kind in expected_membership.values()),
            "contract_web_source_count": sum(kind == "web" for kind in expected_membership.values()),
            "configured_non_x_source_count": len(sources),
            "attempted_source_count": 0,
            "successful_source_count": 0,
            "failed_source_count": 0,
        },
        "sources": {},
        "failures": [],
        "recovery": "fix_structured_failures_then_rerun_with_a_new_database",
    }
    configured_membership = {source.slug: source.kind for source in sources}
    if configured_membership != expected_membership:
        report.update(
            phase="membership",
            failures=[{
                "source_id": None,
                "error_class": "ValueError",
                "error_message": "incomplete non-X source membership",
                "request_attempt_count": 0,
                "http_status_code": None,
            }],
        )
        return _seal_and_write(report, output, config)
    db.migrate(resolved_db)
    conn = db.get_conn(resolved_db)
    active = Path(conn.execute("PRAGMA database_list").fetchone()[2]).resolve()
    print(f"AI_RADAR_DB={resolved_db}", flush=True)
    if active != resolved_db:
        raise ValueError(f"active DB mismatch: {active}")
    sync_to_db(sources, conn)

    evidence: dict[str, object] = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(_capture, source): source for source in sources}
        captured_by_slug: dict[str, Captured] = {}
        for future in as_completed(futures):
            source = futures[future]
            try:
                captured = future.result()
            except Exception as exc:
                failure = _structured_failure(source.slug, "first_live", exc, request_attempt_count=1)
                report["failures"].append(failure)  # type: ignore[union-attr]
            else:
                captured_by_slug[captured.source.slug] = captured

    first_sets: dict[str, set[tuple[str, str]]] = {}
    first_rounds: dict[str, dict[str, object]] = {}
    replay_rounds: dict[str, dict[str, object]] = {}
    for source in sources:
        if source.slug not in captured_by_slug:
            continue
        try:
            captured = captured_by_slug[source.slug]
            items, expected = _parse(captured)
            production = _item_set(items)
            summary = _apply(conn, captured, items)
            persisted = _persisted_set(conn, source.slug)
            sets_equal(expected, persisted, source.slug)
            sets_equal(expected, production, source.slug)
            if summary.inserted != len(expected):
                raise ValueError(f"first live inserted-item count mismatch for {source.slug}")
            first_sets[source.slug] = expected
            first_rounds[source.slug] = {
                "request_attempt_count": captured.attempts,
                "request_url": source.url,
                "final_response_url": captured.response.final_url or source.url,
                "http_status_code": captured.response.status_code,
                "response_body_sha256": hashlib.sha256(captured.response.body).hexdigest(),
                "oracle_canonical_item_set": _set_evidence(expected),
                "production_canonical_item_set": _set_evidence(production),
                "persisted_canonical_item_set": _set_evidence(persisted),
                "inserted_item_count": summary.inserted,
            }
        except Exception as exc:
            captured = captured_by_slug[source.slug]
            report["failures"].append(_structured_failure(  # type: ignore[union-attr]
                source.slug,
                "first_live",
                exc,
                request_attempt_count=captured.attempts,
                http_status_code=captured.response.status_code,
            ))

    if report["failures"]:
        first_evidence = {
            source.slug: {"kind": source.kind, "first_live": first_rounds[source.slug]}
            for source in sources if source.slug in first_rounds
        }
        failure_ids = {failure["source_id"] for failure in report["failures"]}  # type: ignore[index,union-attr]
        counts = report["source_counts"]
        assert isinstance(counts, dict)
        counts.update(
            attempted_source_count=len(first_evidence) + len(failure_ids),
            successful_source_count=len(first_evidence),
            failed_source_count=len(failure_ids),
        )
        report.update(phase="first_live", sources=first_evidence)
        conn.close()
        return _seal_and_write(report, output, config)

    for source in sources:
        captured = captured_by_slug[source.slug]
        try:
            items, expected = _parse(captured)
            replay = _apply(conn, captured, items)
            replay_persisted = _persisted_set(conn, source.slug)
            if replay.inserted != 0 or replay_persisted != first_sets[source.slug]:
                raise ValueError(f"immutable replay dedup failed for {source.slug}")
            replay_rounds[source.slug] = {
                "request_attempt_count": 0,
                "response_body_sha256": hashlib.sha256(captured.response.body).hexdigest(),
                "oracle_canonical_item_set": _set_evidence(first_sets[source.slug]),
                "production_canonical_item_set": _set_evidence(_item_set(items)),
                "persisted_canonical_item_set": _set_evidence(replay_persisted),
                "inserted_item_count": replay.inserted,
            }
        except Exception as exc:
            report["failures"].append(_structured_failure(source.slug, "immutable_replay", exc))  # type: ignore[union-attr]

    if report["failures"]:
        counts = report["source_counts"]
        assert isinstance(counts, dict)
        replay_evidence = {
            source.slug: {
                "kind": source.kind,
                "first_live": first_rounds[source.slug],
                "immutable_replay": replay_rounds[source.slug],
            }
            for source in sources if source.slug in replay_rounds
        }
        failure_ids = {failure["source_id"] for failure in report["failures"]}  # type: ignore[index,union-attr]
        counts.update(
            attempted_source_count=len(replay_evidence) + len(failure_ids),
            successful_source_count=len(replay_evidence),
            failed_source_count=len(failure_ids),
        )
        report.update(phase="immutable_replay", sources=replay_evidence)
        conn.close()
        return _seal_and_write(report, output, config)

    refreshed = {source.slug: source for source in load_enabled_sources_from_db(conn)}
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(_capture, refreshed[source.slug]): source for source in sources}
        second_captured = {}
        for future in as_completed(futures):
            source = futures[future]
            try:
                captured = future.result()
            except Exception as exc:
                failure = _structured_failure(source.slug, "second_live", exc, request_attempt_count=1)
                report["failures"].append(failure)  # type: ignore[union-attr]
            else:
                second_captured[captured.source.slug] = captured
    for source in sources:
        if source.slug not in second_captured:
            continue
        try:
            captured = second_captured[source.slug]
            before = _persisted_set(conn, source.slug)
            if captured.response.not_modified:
                summary = _apply(conn, captured, [])
                after = _persisted_set(conn, source.slug)
                if summary.inserted != 0 or after != before:
                    raise ValueError(f"304 preservation failed for {source.slug}")
                expected = after
                production = after
            else:
                items, expected = _parse(captured)
                production = _item_set(items)
                if not first_sets[source.slug] <= expected:
                    raise ValueError(f"second live set dropped first-round items for {source.slug}")
                summary = _apply(conn, captured, items)
                after = _persisted_set(conn, source.slug)
                sets_equal(expected, production, source.slug)
                sets_equal(expected, after, source.slug)
            delta = expected - first_sets[source.slug]
            if summary.inserted != len(delta):
                raise ValueError(f"second live delta mismatch for {source.slug}")
            evidence[source.slug] = {
                "kind": source.kind,
                "first_live": first_rounds[source.slug],
                "immutable_replay": replay_rounds[source.slug],
                "second_live": {
                    "request_attempt_count": captured.attempts,
                    "request_url": source.url,
                    "final_response_url": captured.response.final_url or source.url,
                    "http_status_code": captured.response.status_code,
                    "response_body_sha256": hashlib.sha256(captured.response.body).hexdigest(),
                    "oracle_canonical_item_set": _set_evidence(expected),
                    "production_canonical_item_set": _set_evidence(production),
                    "persisted_canonical_item_set": _set_evidence(after),
                    "inserted_item_count": summary.inserted,
                },
            }
        except Exception as exc:
            captured = second_captured[source.slug]
            report["failures"].append(_structured_failure(  # type: ignore[union-attr]
                source.slug,
                "second_live",
                exc,
                request_attempt_count=captured.attempts,
                http_status_code=captured.response.status_code,
            ))
    if report["failures"]:
        counts = report["source_counts"]
        assert isinstance(counts, dict)
        counts.update(
            attempted_source_count=len(sources),
            successful_source_count=len(evidence),
            failed_source_count=len(report["failures"]),
        )
        report.update(phase="second_live", sources=evidence)
        conn.close()
        return _seal_and_write(report, output, config)
    conn.close()
    report.update(status="success", phase="complete", sources=evidence, recovery=None)
    counts = report["source_counts"]
    assert isinstance(counts, dict)
    counts.update(attempted_source_count=len(sources), successful_source_count=len(sources), failed_source_count=0)
    return _seal_and_write(report, output, config)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = audit(args.config.resolve(), args.db, args.output)
    except Exception as exc:
        captured_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        config_path = args.config.resolve()
        try:
            configured_sources = [
                source for source in load_sources(config_path)
                if source.enabled and source.kind in {"feed", "web"}
            ]
        except Exception:
            configured_sources = []
        contract_rows = [
            row for row in load_source_contract(CONTRACT)["sources"]
            if row["kind"] in {"feed", "web"}
        ]
        expected_membership = {row["slug"]: row["kind"] for row in contract_rows}
        report = {
            "schema_version": 1,
            "artifact_type": "non_x_retrieval",
            "status": "failed",
            "captured_at": captured_at,
            "phase": "setup",
            "probe_scope": {"kinds": ["feed", "web"], "live_round_count": 2, "immutable_replay_round_count": 1},
            "database_path_absolute": str(args.db.resolve()),
            "config_sha256": _hash(config_path) if config_path.is_file() else None,
            "contract_sha256": _hash(CONTRACT),
            "code_sha256_by_path_relative_to_repository_root": {
                path: _hash(ROOT / path) for path in CODE_PATHS
            },
            "source_counts": {
                "contract_non_x_source_count": len(expected_membership),
                "contract_feed_source_count": sum(kind == "feed" for kind in expected_membership.values()),
                "contract_web_source_count": sum(kind == "web" for kind in expected_membership.values()),
                "configured_non_x_source_count": len(configured_sources),
                "attempted_source_count": 0,
                "successful_source_count": 0,
                "failed_source_count": 0,
            },
            "sources": {},
            "failures": [_structured_failure(None, "setup", exc)],
            "recovery": "fix_setup_failure_then_rerun_with_a_new_database",
        }
        report["report_payload_sha256"] = canonical_payload_sha256(report)
        validate_non_x_receipt(
            report,
            config_path=config_path,
            contract_path=CONTRACT,
            repository_root=ROOT,
        )
        atomic_json(args.output, report)
    return 0 if report["status"] == "success" else 2


if __name__ == "__main__":
    raise SystemExit(main())
