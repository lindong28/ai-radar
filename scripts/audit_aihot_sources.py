#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from airadar.audit.receipts import (  # noqa: E402
    OBSERVATION_RECEIPT_CODE_PATHS,
    atomic_json,
    canonical_payload_sha256,
    reconcile_observed_sources,
    validate_archived_observation_receipt,
    validate_observation_receipt,
)
from airadar.egress import selector_httpx_client  # noqa: E402

ENDPOINT = "https://aihot.virxact.com/api/v1/items"
QUERY: dict[str, str | int] = {"mode": "all", "by": "timeline", "window": "7d", "limit": 100}


class ObservationTraversalFailure(RuntimeError):
    def __init__(self, cause: Exception, traversal: dict[str, object]) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.traversal = traversal


def _traversal_result(
    items: list[dict[str, object]],
    page_manifest: list[dict[str, object]],
    *,
    terminal_reached: bool,
) -> dict[str, object]:
    sources: dict[str, list[str]] = {}
    for item in items:
        source = item.get("source")
        links = item.get("links")
        nested_name = source.get("name") if isinstance(source, dict) else None
        nested_url = links.get("original") if isinstance(links, dict) else None
        name = str(item.get("sourceName") or nested_name or source or "").strip()
        url = str(item.get("url") or item.get("originalUrl") or nested_url or "").strip()
        if not name:
            raise ValueError("malformed AIHOT item: source name must be non-empty")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("malformed AIHOT item: original URL must be an absolute HTTP(S) URL")
        sources.setdefault(name, []).append(url)
    return {
        "pagination": {
            "terminal_reached": terminal_reached,
            "page_count": len(page_manifest),
            "pages": page_manifest,
        },
        "observed_item_count": len(items),
        "observed_source_count": len(sources),
        "observed_source_item_urls": {
            key: sorted(set(value)) for key, value in sorted(sources.items())
        },
    }


def _verify_observation_index(
    artifacts_dir: Path,
    entries: object,
    *,
    contract_path: Path,
) -> list[dict[str, object]]:
    if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
        raise ValueError("observation index integrity failure: observations must be a list")
    verified: list[dict[str, object]] = []
    root = artifacts_dir.resolve()
    for entry in entries:
        if set(entry) != {
            "artifact_sha256",
            "artifact_path_relative_to_artifacts_dir",
        }:
            raise ValueError("observation index integrity failure: invalid entry fields")
        relative = entry.get("artifact_path_relative_to_artifacts_dir")
        expected_sha = entry.get("artifact_sha256")
        if not isinstance(relative, str) or not isinstance(expected_sha, str):
            raise ValueError("observation index integrity failure: invalid entry")
        artifact = (artifacts_dir / relative).resolve()
        if artifact.parent != (root / "observations") or not artifact.is_file():
            raise ValueError("observation index integrity failure: missing artifact")
        if hashlib.sha256(artifact.read_bytes()).hexdigest() != expected_sha:
            raise ValueError("observation index integrity failure: artifact hash mismatch")
        receipt = validate_archived_observation_receipt(
            json.loads(artifact.read_text(encoding="utf-8"))
        )
        if receipt["status"] != "success":
            raise ValueError("observation index integrity failure: indexed artifact is not successful")
        verified.append(entry)
    return verified


def persist_successful_observation(
    report: dict[str, object],
    *,
    artifacts_dir: Path,
    output_path: Path,
    contract_path: Path,
) -> Path:
    report = validate_observation_receipt(
        report, contract_path=contract_path, repository_root=ROOT
    )
    if report.get("status") != "success":
        raise ValueError("only complete terminal observations may be persisted")
    captured_at = report.get("captured_at")
    raw_sha = report.get("report_payload_sha256")
    if not isinstance(captured_at, str):
        raise ValueError("captured_at must be a UTC timestamp")
    try:
        captured_time = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("captured_at must be a UTC timestamp") from exc
    if captured_time.tzinfo is None or captured_time.utcoffset() != datetime.now(UTC).utcoffset():
        raise ValueError("captured_at must be a UTC timestamp")
    if not isinstance(raw_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", raw_sha):
        raise ValueError("sha256 must be a lowercase SHA-256 digest")

    observations_dir = artifacts_dir / "observations"
    index_path = observations_dir / "index.json"
    existing_entries: list[dict[str, object]] = []
    if index_path.exists():
        index_payload = json.loads(index_path.read_text(encoding="utf-8"))
        if not isinstance(index_payload, dict):
            raise ValueError("observation index integrity failure: invalid root")
        if set(index_payload) != {"schema_version", "artifact_type", "path_base", "observations"} or index_payload.get("schema_version") != 1 or index_payload.get("artifact_type") != "aihot_observation_index" or index_payload.get("path_base") != "artifacts_dir":
            raise ValueError("observation index integrity failure: invalid schema")
        existing_entries = _verify_observation_index(
            artifacts_dir,
            index_payload.get("observations"),
            contract_path=contract_path,
        )
    filename = f"{uuid.uuid4()}.json"
    daily_path = observations_dir / filename
    if daily_path.exists():
        raise FileExistsError(f"append-only observation already exists: {daily_path}")

    atomic_json(daily_path, report)
    try:
        daily_sha = hashlib.sha256(daily_path.read_bytes()).hexdigest()
        sources = report.get("observed_source_item_urls")
        if not isinstance(sources, dict):
            raise ValueError("sources must be an object")
        entry: dict[str, object] = {
            "artifact_sha256": daily_sha,
            "artifact_path_relative_to_artifacts_dir": daily_path.relative_to(artifacts_dir).as_posix(),
        }
        atomic_json(output_path, report)
        atomic_json(index_path, {
            "schema_version": 1,
            "artifact_type": "aihot_observation_index",
            "path_base": "artifacts_dir",
            "observations": [*existing_entries, entry],
        })
    except Exception:
        daily_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)
        raise
    return daily_path


def traverse_pages(
    fetch: Callable[[str | None], tuple[dict[str, object], bytes]],
    *,
    initial_seen: set[str] | None = None,
) -> dict[str, object]:
    seen = set(initial_seen or ())
    cursor: str | None = None
    pages = 0
    items: list[dict[str, object]] = []
    page_manifest: list[dict[str, object]] = []
    while True:
        try:
            page, raw_response_body = fetch(cursor)
        except Exception as exc:
            raise ObservationTraversalFailure(
                exc,
                _traversal_result(items, page_manifest, terminal_reached=False),
            ) from exc
        if not isinstance(raw_response_body, bytes):
            raise ValueError("malformed AIHOT response: raw response body must be bytes")
        data = page.get("data", page.get("items"))
        if not isinstance(data, list):
            raise ValueError("malformed AIHOT page: data must be a list")
        if any(not isinstance(item, dict) for item in data):
            raise ValueError("malformed AIHOT item")
        items.extend(data)  # type: ignore[arg-type]
        pages += 1
        page_meta = page.get("page", page)
        if not isinstance(page_meta, dict):
            raise ValueError("malformed AIHOT page: page metadata must be an object")
        has_more = page_meta.get("hasMore")
        next_cursor = page_meta.get("nextCursor")
        page_manifest.append({
            "request_cursor": cursor,
            "response_next_cursor": next_cursor,
            "response_has_more": has_more,
            "response_item_count": len(data),
            "raw_response_body_sha256": hashlib.sha256(raw_response_body).hexdigest(),
        })
        if has_more is False:
            break
        if not isinstance(next_cursor, str) or not next_cursor or next_cursor in seen:
            raise ValueError("missing or repeated AIHOT cursor")
        seen.add(next_cursor)
        cursor = next_cursor
    return _traversal_result(items, page_manifest, terminal_reached=True)


def reconcile_sources(observed: dict[str, list[str]], contract_path: Path) -> dict[str, object]:
    return cast(dict[str, object], reconcile_observed_sources(observed, contract_path))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--contract", type=Path, default=ROOT / "tests/fixtures/aihot_sources.json")
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite existing observation evidence: {args.output}")

    def fetch(cursor: str | None) -> tuple[dict[str, object], bytes]:
        params = dict(QUERY)
        if cursor:
            params["cursor"] = cursor
        with selector_httpx_client(
            callsite_id="scripts.audit_aihot_sources",
            request_url=ENDPOINT,
            timeout=30,
            follow_redirects=True,
        ) as client:
            response = client.get(ENDPOINT, params=params)
        response.raise_for_status()
        raw_response_body = response.content
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("malformed AIHOT response")
        return payload, raw_response_body

    captured_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    traversal_failure: ObservationTraversalFailure | None = None
    try:
        traversal = traverse_pages(fetch)
    except ObservationTraversalFailure as exc:
        traversal_failure = exc
        traversal = exc.traversal
    except Exception as exc:
        traversal_failure = ObservationTraversalFailure(
            exc,
            _traversal_result([], [], terminal_reached=False),
        )
        traversal = traversal_failure.traversal
    reconciliation = reconcile_sources(traversal["observed_source_item_urls"], args.contract)  # type: ignore[arg-type]
    reconciled = not reconciliation["ambiguous"] and not reconciliation["unmapped"] and not reconciliation["conflicting"]
    success = traversal_failure is None and reconciled
    if traversal_failure is not None:
        failure = {
            "error_class": type(traversal_failure.cause).__name__,
            "error_message": str(traversal_failure.cause),
        }
    elif not reconciled:
        failure = {
            "error_class": "ReconciliationError",
            "error_message": "observation contains ambiguous, unmapped, or conflicting sources",
        }
    else:
        failure = None
    report: dict[str, object] = {
        "schema_version": 1,
        "artifact_type": "aihot_observation",
        "status": "success" if success else "failed",
        "captured_at": captured_at,
        "endpoint": ENDPOINT,
        "query": QUERY,
        "contract_sha256": hashlib.sha256(args.contract.read_bytes()).hexdigest(),
        "code_sha256_by_path_relative_to_repository_root": {
            path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
            for path in OBSERVATION_RECEIPT_CODE_PATHS
        },
        **traversal,
        "reconciliation": reconciliation,
        "failure": failure,
    }
    report["report_payload_sha256"] = canonical_payload_sha256(report)
    validate_observation_receipt(report, contract_path=args.contract, repository_root=ROOT)
    persisted_reconciliation = report["reconciliation"]
    assert isinstance(persisted_reconciliation, dict)
    if report["status"] == "failed":
        atomic_json(args.output, report)
        return 2
    persist_successful_observation(
        report,
        artifacts_dir=args.output.parent,
        output_path=args.output,
        contract_path=args.contract,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
