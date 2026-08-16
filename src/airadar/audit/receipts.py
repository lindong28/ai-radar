from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
AIHOT_ENDPOINT = "https://aihot.virxact.com/api/v1/items"
AIHOT_QUERY = {"mode": "all", "by": "timeline", "window": "7d", "limit": 100}
X_PROBE_SCOPE = {
    "identity_requests_max": 1,
    "timeline_requests_max": 1,
    "lookback_minutes": 20,
    "max_results": 5,
    "backfill": False,
}
X_OFFLINE_PROOF_NODE_IDS = (
    "tests/test_x_api.py::test_fetch_x_timeline_uses_one_bounded_cold_start_page_and_persists_cursor",
    "tests/test_x_api.py::test_fetch_x_timeline_drains_one_saved_page_per_round_then_advances_since_id",
    "tests/test_x_api.py::test_time_checkpoint_pagination_keeps_one_start_boundary",
    "tests/test_x_api.py::test_fetch_x_timeline_rejects_non_advancing_pagination_token",
)
X_OFFLINE_PROOF_TEST_PATHS = ("tests/test_x_api.py",)
X_OFFLINE_PROOF_PRODUCTION_PATHS = (
    "src/airadar/fetcher/x_api.py",
    "src/airadar/fetcher/runner.py",
    "src/airadar/sources/x_state.py",
)
OBSERVATION_RECEIPT_CODE_PATHS = (
    "src/airadar/audit/receipts.py",
    "src/airadar/sources/contract.py",
    "scripts/audit_aihot_sources.py",
)
NON_X_RECEIPT_CODE_PATHS = (
    "src/airadar/audit/receipts.py",
    "src/airadar/audit/completeness_oracle.py",
    "src/airadar/fetcher/http_client.py",
    "src/airadar/fetcher/rss.py",
    "src/airadar/fetcher/feed_rules.py",
    "src/airadar/fetcher/web.py",
    "src/airadar/fetcher/runner.py",
    "src/airadar/fetcher/dedup.py",
    "src/airadar/sources/contract.py",
    "src/airadar/sources/loader.py",
    "scripts/audit_non_x_retrieval.py",
)
X_PROBE_RECEIPT_CODE_PATHS = (
    "src/airadar/audit/receipts.py",
    "src/airadar/fetcher/x_api.py",
    "src/airadar/fetcher/runner.py",
    "src/airadar/sources/x_state.py",
    "src/airadar/sources/sync.py",
    "scripts/probe_x_source.py",
)


def canonical_payload_sha256(payload: dict[str, object], *, omit: str = "report_payload_sha256") -> str:
    canonical = {key: value for key, value in payload.items() if key != omit}
    body = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(body).hexdigest()


def canonical_item_set(items: Iterable[tuple[str, str]]) -> dict[str, object]:
    rows = [{"url": url, "title": title} for url, title in sorted(set(items))]
    return {"item_count": len(rows), "canonical_items": rows}


def _exact(value: object, fields: set[str], *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        actual = set(value) if isinstance(value, dict) else set()
        raise ValueError(f"invalid {label} fields: missing={sorted(fields-actual)} extra={sorted(actual-fields)}")
    return value


def _version(payload: dict[str, Any], artifact_type: str) -> None:
    if payload["schema_version"] != 1 or payload["artifact_type"] != artifact_type:
        raise ValueError(f"invalid {artifact_type} schema_version or artifact_type")


def _timestamp(value: object, label: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"invalid {label} timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid {label} timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"invalid {label} timestamp")


def _sha(value: object, label: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise ValueError(f"invalid {label} SHA-256")
    return value


def _verify_current_hash(value: object, path: Path | None, label: str) -> None:
    expected = _sha(value, label)
    if path is not None and hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError(f"{label} does not match current {label}")


def _verify_code_hashes(value: object, root: Path | None, *, label: str = "code hashes") -> None:
    if not isinstance(value, dict) or not value:
        raise ValueError(f"{label} must be a non-empty object")
    resolved_root = root.resolve() if root is not None else None
    for relative, digest in value.items():
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
            raise ValueError(f"invalid {label} path")
        expected = _sha(digest, f"{label} {relative}")
        if resolved_root is not None:
            path = (resolved_root / relative).resolve()
            if not path.is_relative_to(resolved_root) or not path.is_file():
                raise ValueError(f"{label} path is unavailable: {relative}")
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError(f"{label} does not match current file: {relative}")


def _verify_exact_code_hashes(
    value: object,
    root: Path | None,
    required_paths: tuple[str, ...],
    *,
    label: str = "code hashes",
) -> None:
    if not isinstance(value, dict) or set(value) != set(required_paths):
        raise ValueError(f"{label} code paths do not match the required set")
    _verify_code_hashes(value, root, label=label)


def _x_identity(urls: list[str]) -> str | None:
    for url in urls:
        match = re.match(r"https?://(?:www\.)?(?:x|twitter)\.com/([^/]+)/status/", url, re.I)
        if match:
            return f"x:{match.group(1).casefold()}"
    return None


def reconcile_observed_sources(observed: dict[str, list[str]], contract_path: Path) -> dict[str, list[object]]:
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    rows = payload.get("sources", []) if isinstance(payload, dict) else []
    identities = {
        str(row["derived_aihot_identity"]): row
        for row in rows
        if isinstance(row, dict) and row.get("ai_radar_main_timeline_member")
    }
    alias_map: dict[str, list[str]] = {}
    for row in identities.values():
        aliases = [row.get("name"), *row.get("aihot_aliases", [])]
        for alias in aliases:
            if alias:
                alias_map.setdefault(str(alias).casefold(), []).append(str(row["derived_aihot_identity"]))
    result: dict[str, list[object]] = {
        "matched": [], "renamed": [], "excluded_wechat": [],
        "ambiguous": [], "unmapped": [], "conflicting": [],
    }
    for name, urls in observed.items():
        if name.startswith("公众号：") and any(url.startswith("https://mp.weixin.qq.com/") for url in urls):
            result["excluded_wechat"].append(name)
            continue
        url_identity = _x_identity(urls)
        display_match = re.search(r"@([A-Za-z0-9_]{1,15})", name)
        display_identity = f"x:{display_match.group(1).casefold()}" if display_match else None
        if display_identity and not url_identity:
            result["conflicting"].append({
                "display_identity": display_identity, "source": name, "url_identity": None,
            })
            continue
        if url_identity and display_identity and url_identity != display_identity:
            result["conflicting"].append({
                "display_identity": display_identity, "source": name, "url_identity": url_identity,
            })
            continue
        if url_identity and url_identity in identities:
            row = identities[url_identity]
            identity_aliases = {
                str(alias).casefold()
                for alias in [row.get("name"), *row.get("aihot_aliases", [])]
                if alias
            }
            bucket = "matched" if name.casefold() in identity_aliases else "renamed"
            result[bucket].append({"source": name, "identity": url_identity})
            continue
        candidates = alias_map.get(name.casefold(), [])
        if not url_identity and any(identity.startswith("x:") for identity in candidates):
            result["conflicting"].append({
                "display_identity": candidates[0] if len(candidates) == 1 else None,
                "source": name,
                "url_identity": None,
            })
            continue
        if len(candidates) == 1:
            result["matched"].append({"source": name, "identity": candidates[0]})
        elif len(candidates) > 1:
            result["ambiguous"].append({"source": name, "identities": candidates})
        else:
            result["unmapped"].append(name)
    return result


def _validate_observation_receipt(
    payload: object,
    *,
    contract_path: Path | None = None,
    repository_root: Path | None = None,
    require_current_code: bool,
) -> dict[str, Any]:
    report = _exact(payload, {
        "schema_version", "artifact_type", "status", "captured_at", "endpoint", "query",
        "contract_sha256", "code_sha256_by_path_relative_to_repository_root",
        "pagination", "reconciliation", "observed_source_item_urls",
        "observed_source_count", "observed_item_count", "failure", "report_payload_sha256",
    }, label="AIHOT observation")
    _version(report, "aihot_observation")
    _timestamp(report["captured_at"], "captured_at")
    if contract_path is None:
        _sha(report["contract_sha256"], "contract")
    else:
        from ..sources.contract import load_source_contract

        load_source_contract(contract_path)
        _verify_current_hash(report["contract_sha256"], contract_path, "contract")
    if require_current_code and repository_root is None:
        raise ValueError("repository root is required to validate current observation code hashes")
    _verify_exact_code_hashes(
        report["code_sha256_by_path_relative_to_repository_root"],
        repository_root if require_current_code else None,
        OBSERVATION_RECEIPT_CODE_PATHS,
    )
    if report["status"] not in {"success", "failed"}:
        raise ValueError("invalid AIHOT observation status")
    if report["endpoint"] != AIHOT_ENDPOINT:
        raise ValueError("invalid AIHOT observation endpoint")
    if report["query"] != AIHOT_QUERY:
        raise ValueError("invalid AIHOT observation query")

    pagination = _exact(report["pagination"], {"terminal_reached", "page_count", "pages"}, label="pagination")
    pages = pagination["pages"]
    if not isinstance(pages, list) or pagination["page_count"] != len(pages):
        raise ValueError("invalid pagination page count")
    if report["status"] == "success" and not pages:
        raise ValueError("successful observation requires at least one page")
    expected_cursor: str | None = None
    observed_item_count = 0
    for index, page in enumerate(pages):
        row = _exact(page, {
            "request_cursor", "response_next_cursor", "response_has_more",
            "response_item_count", "raw_response_body_sha256",
        }, label="page manifest")
        if row["request_cursor"] != expected_cursor:
            raise ValueError("invalid pagination cursor chain")
        if not isinstance(row["response_has_more"], bool):
            raise ValueError("invalid page has-more value")
        if not isinstance(row["response_item_count"], int) or row["response_item_count"] < 0:
            raise ValueError("invalid page item count")
        _sha(row["raw_response_body_sha256"], "raw response body")
        is_last = index == len(pages) - 1
        if not is_last and (not row["response_has_more"] or not isinstance(row["response_next_cursor"], str) or not row["response_next_cursor"]):
            raise ValueError("invalid non-terminal page")
        if row["response_has_more"] is False and row["response_next_cursor"] is not None:
            raise ValueError("invalid terminal page")
        if row["response_has_more"] is True and (not isinstance(row["response_next_cursor"], str) or not row["response_next_cursor"]):
            raise ValueError("invalid non-terminal page")
        expected_cursor = row["response_next_cursor"]
        observed_item_count += row["response_item_count"]
    derived_terminal = bool(pages) and pages[-1]["response_has_more"] is False
    if pagination["terminal_reached"] is not derived_terminal:
        raise ValueError("pagination terminal flag contradicts the page chain")

    sources = report["observed_source_item_urls"]
    if not isinstance(sources, dict) or any(
        not isinstance(name, str)
        or not name.strip()
        or not isinstance(urls, list)
        or not urls
        or any(
            not isinstance(url, str)
            or urlparse(url).scheme not in {"http", "https"}
            or not urlparse(url).netloc
            for url in urls
        )
        for name, urls in sources.items()
    ):
        raise ValueError("invalid observed source item URLs")
    if report["observed_source_count"] != len(sources) or report["observed_item_count"] != observed_item_count:
        raise ValueError("invalid observed source/item count")
    reconciliation = _exact(report["reconciliation"], {
        "matched", "renamed", "excluded_wechat", "ambiguous", "unmapped", "conflicting",
    }, label="reconciliation")
    if contract_path is not None:
        expected_reconciliation = reconcile_observed_sources(sources, contract_path)
        if reconciliation != expected_reconciliation:
            raise ValueError("reconciliation does not match its contract and observed sources")
    reconciled = not reconciliation["ambiguous"] and not reconciliation["unmapped"] and not reconciliation["conflicting"]
    semantic_success = derived_terminal and reconciled
    if report["status"] != ("success" if semantic_success else "failed"):
        raise ValueError("observation status contradicts traversal or reconciliation")
    if semantic_success:
        if report["failure"] is not None:
            raise ValueError("successful observation cannot contain failure details")
    else:
        failure = _exact(report["failure"], {"error_class", "error_message"}, label="observation failure")
        if any(not isinstance(failure[key], str) or not failure[key] for key in failure):
            raise ValueError("failed observation needs structured failure details")
    if report["report_payload_sha256"] != canonical_payload_sha256(report):
        raise ValueError("invalid observation payload hash")
    return report


def validate_observation_receipt(
    payload: object,
    *,
    contract_path: Path | None = None,
    repository_root: Path | None = None,
) -> dict[str, Any]:
    if contract_path is None:
        raise ValueError("current contract path is required to validate observation reconciliation")
    return _validate_observation_receipt(
        payload,
        contract_path=contract_path,
        repository_root=repository_root,
        require_current_code=True,
    )


def validate_archived_observation_receipt(
    payload: object,
    *,
    contract_path: Path | None = None,
) -> dict[str, Any]:
    """Validate immutable observation evidence without binding it to the current checkout."""
    return _validate_observation_receipt(
        payload,
        contract_path=contract_path,
        repository_root=None,
        require_current_code=False,
    )


def _validate_item_set(value: object, label: str) -> set[tuple[str, str]]:
    row = _exact(value, {"item_count", "canonical_items"}, label=label)
    items = row["canonical_items"]
    if not isinstance(items, list):
        raise ValueError(f"invalid {label} canonical items")
    values: set[tuple[str, str]] = set()
    for item in items:
        entry = _exact(item, {"url", "title"}, label=f"{label} item")
        if not isinstance(entry["url"], str) or not isinstance(entry["title"], str):
            raise ValueError(f"invalid {label} item value")
        values.add((entry["url"], entry["title"]))
    if row != canonical_item_set(values):
        raise ValueError(f"invalid {label} canonical count/order")
    return values


def _validate_live_round(value: object, label: str, *, first: bool) -> set[tuple[str, str]]:
    row = _exact(value, {
        "request_attempt_count", "request_url", "final_response_url", "http_status_code",
        "response_body_sha256", "oracle_canonical_item_set", "production_canonical_item_set",
        "persisted_canonical_item_set", "inserted_item_count",
    }, label=label)
    if not isinstance(row["request_attempt_count"], int) or row["request_attempt_count"] < 1:
        raise ValueError(f"{label} needs a real request attempt")
    if not all(isinstance(row[key], str) and row[key] for key in ("request_url", "final_response_url")):
        raise ValueError(f"invalid {label} URL")
    status = row["http_status_code"]
    if not isinstance(status, int) or not 200 <= status < 400 or (first and status == 304):
        raise ValueError(f"invalid {label} HTTP status code")
    _sha(row["response_body_sha256"], f"{label} response body")
    sets = [
        _validate_item_set(row[key], f"{label} {key}")
        for key in ("oracle_canonical_item_set", "production_canonical_item_set", "persisted_canonical_item_set")
    ]
    if sets[0] != sets[1] or sets[0] != sets[2]:
        raise ValueError(f"{label} canonical sets differ")
    if first and not sets[0]:
        raise ValueError(f"{label} canonical set must be non-empty")
    if not isinstance(row["inserted_item_count"], int) or row["inserted_item_count"] < 0:
        raise ValueError(f"invalid {label} inserted item count")
    return sets[0]


def _validate_replay(value: object, first: set[tuple[str, str]], first_response_sha: str) -> None:
    row = _exact(value, {
        "request_attempt_count", "response_body_sha256", "oracle_canonical_item_set",
        "production_canonical_item_set", "persisted_canonical_item_set", "inserted_item_count",
    }, label="immutable replay")
    if row["request_attempt_count"] != 0 or row["inserted_item_count"] != 0:
        raise ValueError("immutable replay must issue zero requests and insert zero items")
    if _sha(row["response_body_sha256"], "immutable replay response body") != first_response_sha:
        raise ValueError("immutable replay response body must equal first live response body")
    sets = [_validate_item_set(row[key], f"immutable replay {key}") for key in (
        "oracle_canonical_item_set", "production_canonical_item_set", "persisted_canonical_item_set",
    )]
    if any(items != first for items in sets):
        raise ValueError("immutable replay canonical sets differ from first live")


def _expected_non_x(contract_path: Path, config_path: Path) -> tuple[dict[str, str], dict[str, str]]:
    from ..sources.contract import load_source_contract
    from ..sources.loader import load_sources

    contract = load_source_contract(contract_path)
    expected = {row["slug"]: row["kind"] for row in contract["sources"] if row["kind"] in {"feed", "web"}}
    configured = (
        {source.slug: source.kind for source in load_sources(config_path) if source.kind in {"feed", "web"}}
        if config_path.is_file()
        else {}
    )
    return expected, configured


def _validate_non_x_failure(value: object, phase: str, expected: dict[str, str]) -> dict[str, Any]:
    row = _exact(value, {
        "source_id", "error_class", "error_message", "request_attempt_count", "http_status_code",
    }, label="non-X failure")
    source_id = row["source_id"]
    if phase in {"setup", "membership"}:
        if source_id is not None:
            raise ValueError("setup/membership failure source_id must be null")
    elif not isinstance(source_id, str) or source_id not in expected:
        raise ValueError("live/replay failure requires a valid source_id")
    if not all(isinstance(row[key], str) and row[key] for key in ("error_class", "error_message")):
        raise ValueError("invalid non-X structured failure")
    attempts = row["request_attempt_count"]
    if not isinstance(attempts, int) or attempts < 0:
        raise ValueError("invalid non-X failure request attempts")
    status = row["http_status_code"]
    if status is not None and not isinstance(status, int):
        raise ValueError("invalid non-X failure HTTP status code")
    if phase == "immutable_replay" and (attempts != 0 or status is not None):
        raise ValueError("immutable replay failure cannot claim an HTTP request")
    if phase in {"first_live", "second_live"} and attempts < 1:
        raise ValueError("live failure needs a real request attempt")
    return row


def validate_non_x_receipt(
    payload: object,
    *,
    config_path: Path | None = None,
    contract_path: Path | None = None,
    repository_root: Path | None = None,
) -> dict[str, Any]:
    report = _exact(payload, {
        "schema_version", "artifact_type", "status", "captured_at", "phase", "probe_scope",
        "database_path_absolute", "config_sha256", "contract_sha256",
        "code_sha256_by_path_relative_to_repository_root", "source_counts", "sources",
        "failures", "recovery", "report_payload_sha256",
    }, label="non-X receipt")
    _version(report, "non_x_retrieval")
    _timestamp(report["captured_at"], "captured_at")
    if report["status"] not in {"success", "failed"}:
        raise ValueError("invalid non-X receipt status")
    if config_path is None or contract_path is None:
        raise ValueError("current config and contract paths are required for non-X validation")
    if repository_root is None:
        raise ValueError("repository root is required to validate current non-X code hashes")
    _verify_current_hash(report["contract_sha256"], contract_path, "contract")
    _verify_exact_code_hashes(
        report["code_sha256_by_path_relative_to_repository_root"],
        repository_root,
        NON_X_RECEIPT_CODE_PATHS,
    )
    expected, configured = _expected_non_x(contract_path, config_path)
    if report["probe_scope"] != {"kinds": ["feed", "web"], "live_round_count": 2, "immutable_replay_round_count": 1}:
        raise ValueError("invalid non-X probe scope")
    phase = report["phase"]
    if phase not in {"setup", "membership", "first_live", "immutable_replay", "second_live", "complete"}:
        raise ValueError("invalid non-X phase")
    if report["config_sha256"] is None:
        if report["status"] != "failed" or phase != "setup" or config_path.exists():
            raise ValueError("config SHA-256 may be unavailable only for a missing-config setup failure")
    else:
        _verify_current_hash(report["config_sha256"], config_path, "config")
    if not isinstance(report["database_path_absolute"], str) or not Path(report["database_path_absolute"]).is_absolute():
        raise ValueError("invalid absolute database path")
    counts = _exact(report["source_counts"], {
        "contract_non_x_source_count", "contract_feed_source_count", "contract_web_source_count",
        "configured_non_x_source_count", "attempted_source_count", "successful_source_count", "failed_source_count",
    }, label="source counts")
    if any(not isinstance(value, int) or value < 0 for value in counts.values()):
        raise ValueError("invalid non-X source count")
    expected_counts = {
        "contract_non_x_source_count": len(expected),
        "contract_feed_source_count": sum(kind == "feed" for kind in expected.values()),
        "contract_web_source_count": sum(kind == "web" for kind in expected.values()),
        "configured_non_x_source_count": len(configured),
    }
    if {key: counts[key] for key in expected_counts} != expected_counts:
        raise ValueError("non-X source counts do not match current contract/config membership")
    sources = report["sources"]
    failures = report["failures"]
    if not isinstance(sources, dict) or not isinstance(failures, list):
        raise ValueError("invalid non-X evidence collections")
    if any(slug not in expected for slug in sources):
        raise ValueError("non-X source membership contains an identity outside the current contract")
    parsed_failures = [_validate_non_x_failure(value, phase, expected) for value in failures]
    failure_ids = {row["source_id"] for row in parsed_failures if row["source_id"] is not None}
    if set(sources) & failure_ids:
        raise ValueError("non-X successful source evidence and failure IDs overlap")

    required_fields = {
        "first_live": {"kind", "first_live"},
        "immutable_replay": {"kind", "first_live", "immutable_replay"},
        "second_live": {"kind", "first_live", "immutable_replay", "second_live"},
        "complete": {"kind", "first_live", "immutable_replay", "second_live"},
    }
    for slug, evidence in sources.items():
        row = _exact(evidence, required_fields.get(phase, set()), label=f"non-X source {slug}")
        if row["kind"] != expected[slug]:
            raise ValueError(f"non-X source {slug} kind contradicts current contract")
        first = _validate_live_round(row["first_live"], f"{slug} first live", first=True)
        if row["first_live"]["inserted_item_count"] != len(first):
            raise ValueError("first live inserted item count does not match canonical set")
        if "immutable_replay" in row:
            _validate_replay(row["immutable_replay"], first, row["first_live"]["response_body_sha256"])
        if "second_live" in row:
            second = _validate_live_round(row["second_live"], f"{slug} second live", first=False)
            if not first <= second or row["second_live"]["inserted_item_count"] != len(second - first):
                raise ValueError("second live delta does not close")
            if row["second_live"]["http_status_code"] == 304 and (second != first or row["second_live"]["inserted_item_count"] != 0):
                raise ValueError("304 second live must preserve the first canonical set")

    success = report["status"] == "success"
    if success:
        if phase != "complete" or set(sources) != set(expected) or configured != expected:
            raise ValueError("successful non-X receipt must contain exact current contract membership")
        expected_total = len(expected)
        if counts["attempted_source_count"] != expected_total or counts["successful_source_count"] != expected_total or counts["failed_source_count"] != 0:
            raise ValueError("successful non-X receipt source counts must close over current membership")
        if failures or report["recovery"] is not None:
            raise ValueError("successful non-X receipt cannot contain failures or recovery")
    else:
        if phase == "complete" or not failures or not isinstance(report["recovery"], str) or not report["recovery"]:
            raise ValueError("failed non-X receipt needs non-complete phase, structured failures, and recovery")
        if phase in {"setup", "membership"}:
            if sources or counts["attempted_source_count"] or counts["successful_source_count"] or counts["failed_source_count"]:
                raise ValueError("setup/membership failure counts must be zero")
        elif (
            counts["successful_source_count"] != len(sources)
            or counts["failed_source_count"] != len(failure_ids)
            or counts["attempted_source_count"] != len(sources) + len(failure_ids)
        ):
            raise ValueError("failed non-X phase counts do not close over sources and failures")
    if report["report_payload_sha256"] != canonical_payload_sha256(report):
        raise ValueError("invalid non-X payload hash")
    return report


def validate_x_offline_proof_receipt(payload: object, *, repository_root: Path | None = None) -> dict[str, Any]:
    report = _exact(payload, {
        "schema_version", "artifact_type", "status", "captured_at", "pytest_node_ids", "pytest_exit_code",
        "pytest_stdout_sha256", "pytest_stderr_sha256",
        "test_code_sha256_by_path_relative_to_repository_root",
        "production_code_sha256_by_path_relative_to_repository_root", "report_payload_sha256",
    }, label="X offline proof")
    _version(report, "x_pagination_offline_test")
    _timestamp(report["captured_at"], "captured_at")
    if report["status"] != "success" or report["pytest_exit_code"] != 0:
        raise ValueError("X offline proof must record a real successful pytest run")
    if report["pytest_node_ids"] != list(X_OFFLINE_PROOF_NODE_IDS):
        raise ValueError("X offline proof pytest node IDs do not match the controlled set")
    _sha(report["pytest_stdout_sha256"], "pytest stdout")
    _sha(report["pytest_stderr_sha256"], "pytest stderr")
    test_hashes = report["test_code_sha256_by_path_relative_to_repository_root"]
    production_hashes = report["production_code_sha256_by_path_relative_to_repository_root"]
    if not isinstance(test_hashes, dict) or set(test_hashes) != set(X_OFFLINE_PROOF_TEST_PATHS):
        raise ValueError("X offline proof test code paths do not match the controlled set")
    if not isinstance(production_hashes, dict) or set(production_hashes) != set(X_OFFLINE_PROOF_PRODUCTION_PATHS):
        raise ValueError("X offline proof production code paths do not match the controlled set")
    _verify_code_hashes(test_hashes, repository_root, label="test code hashes")
    _verify_code_hashes(production_hashes, repository_root, label="production code hashes")
    if report["report_payload_sha256"] != canonical_payload_sha256(report):
        raise ValueError("invalid X offline proof payload hash")
    return report


def _validate_offline_proof(value: object, *, success: bool, repository_root: Path | None) -> None:
    row = _exact(value, {"status", "receipt_path_relative_to_repository_root", "receipt_sha256"}, label="offline proof")
    if row["status"] == "not_evaluated":
        if row != {"status": "not_evaluated", "receipt_path_relative_to_repository_root": None, "receipt_sha256": None}:
            raise ValueError("invalid not-evaluated offline proof")
        if success:
            raise ValueError("successful X probe requires verified offline proof")
        return
    if row["status"] != "verified" or not isinstance(row["receipt_path_relative_to_repository_root"], str):
        raise ValueError("invalid offline proof status")
    digest = _sha(row["receipt_sha256"], "offline proof receipt")
    if repository_root is None:
        return
    root = repository_root.resolve()
    path = (root / row["receipt_path_relative_to_repository_root"]).resolve()
    if not path.is_relative_to(root) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError("offline proof receipt does not match current artifact")
    validate_x_offline_proof_receipt(json.loads(path.read_text(encoding="utf-8")), repository_root=root)


def validate_x_probe_receipt(
    payload: object,
    *,
    config_path: Path | None = None,
    contract_path: Path | None = None,
    repository_root: Path | None = None,
) -> dict[str, Any]:
    report = _exact(payload, {
        "schema_version", "artifact_type", "status", "captured_at", "phase", "probe_scope",
        "source", "token_present", "state_scope", "request_counts", "http_responses",
        "config_sha256", "contract_sha256", "code_sha256_by_path_relative_to_repository_root",
        "database_state_after_probe", "live_validation", "offline_proof", "failures", "recovery",
        "report_payload_sha256",
    }, label="X probe receipt")
    _version(report, "x_single_source_probe")
    _timestamp(report["captured_at"], "captured_at")
    if report["status"] not in {"success", "failed"}:
        raise ValueError("invalid X probe receipt status")
    if config_path is None or contract_path is None or repository_root is None:
        raise ValueError("current config, contract, and repository root are required for X probe validation")
    _verify_current_hash(report["config_sha256"], config_path, "config")
    _verify_current_hash(report["contract_sha256"], contract_path, "contract")
    _verify_exact_code_hashes(
        report["code_sha256_by_path_relative_to_repository_root"],
        repository_root,
        X_PROBE_RECEIPT_CODE_PATHS,
    )
    if report["source"] != "x_openai" or report["probe_scope"] != X_PROBE_SCOPE:
        raise ValueError("invalid bounded X probe source or scope")
    if not isinstance(report["token_present"], bool):
        raise ValueError("invalid token presence")
    counts = _exact(report["request_counts"], {"identity_http_request_count", "timeline_http_request_count"}, label="X request counts")
    if any(not isinstance(value, int) or value not in {0, 1} for value in counts.values()):
        raise ValueError("invalid X request counts")
    if counts["timeline_http_request_count"] > counts["identity_http_request_count"]:
        raise ValueError("timeline request cannot precede identity request")
    total_requests = sum(counts.values())
    if total_requests and report["token_present"] is not True:
        raise ValueError("an X HTTP request requires token_present=true")
    responses = _exact(report["http_responses"], {"identity_http_status_code", "timeline_http_status_code"}, label="X HTTP responses")
    for stage in ("identity", "timeline"):
        count = counts[f"{stage}_http_request_count"]
        status = responses[f"{stage}_http_status_code"]
        if (count == 0 and status is not None) or (count == 1 and not isinstance(status, int)):
            raise ValueError(f"X {stage} request count and HTTP response contradict")
    live = _exact(report["live_validation"], {
        "identity_connectivity_verified", "timeline_connectivity_verified", "terminal_checkpoint_verified",
        "live_post_retrieval_verified", "fetched_item_count", "inserted_item_count",
    }, label="X live validation")
    boolean_keys = (
        "identity_connectivity_verified", "timeline_connectivity_verified",
        "terminal_checkpoint_verified", "live_post_retrieval_verified",
    )
    if any(not isinstance(live[key], bool) for key in boolean_keys) or any(
        not isinstance(live[key], int) or live[key] < 0 for key in ("fetched_item_count", "inserted_item_count")
    ):
        raise ValueError("invalid X live validation values")
    if live["inserted_item_count"] > live["fetched_item_count"]:
        raise ValueError("invalid X fetched/inserted item counts")
    identity_2xx = isinstance(responses["identity_http_status_code"], int) and 200 <= responses["identity_http_status_code"] < 300
    timeline_2xx = isinstance(responses["timeline_http_status_code"], int) and 200 <= responses["timeline_http_status_code"] < 300
    if live["identity_connectivity_verified"] is not identity_2xx:
        raise ValueError("identity connectivity must exactly match a real 2xx identity response")
    if live["timeline_connectivity_verified"] is not timeline_2xx:
        raise ValueError("timeline connectivity must exactly match a real 2xx timeline response")
    if live["live_post_retrieval_verified"] and live["fetched_item_count"] == 0:
        raise ValueError("live post retrieval requires at least one fetched item")
    if counts["timeline_http_request_count"] == 0 and (live["fetched_item_count"] or live["inserted_item_count"]):
        raise ValueError("item counts require a real timeline request")

    failures = report["failures"]
    if not isinstance(failures, list):
        raise ValueError("invalid X failures")
    for value in failures:
        row = _exact(value, {"reason", "error_class", "http_status_code"}, label="X failure")
        if not all(isinstance(row[key], str) and row[key] for key in ("reason", "error_class")):
            raise ValueError("invalid structured X failure")
        if row["http_status_code"] is not None and not isinstance(row["http_status_code"], int):
            raise ValueError("invalid structured X failure HTTP status")
        stage = report["phase"]
        if stage in {"identity_request", "timeline_request"} and row["http_status_code"] != responses[f"{stage.removesuffix('_request')}_http_status_code"]:
            raise ValueError("X failure HTTP status must match the recorded stage response")

    success = report["status"] == "success"
    _validate_offline_proof(report["offline_proof"], success=success, repository_root=repository_root)
    state = report["database_state_after_probe"]
    if state is not None:
        from ..sources.x_state import validate_x_runtime_meta

        state = validate_x_runtime_meta(state, context="X probe receipt")
    if success:
        if report["phase"] != "complete" or failures or report["recovery"] is not None:
            raise ValueError("invalid successful X phase/failure/recovery state")
        if counts != {"identity_http_request_count": 1, "timeline_http_request_count": 1}:
            raise ValueError("successful X probe requires exactly two bounded request attempts")
        if not identity_2xx or not timeline_2xx:
            raise ValueError("successful X probe requires 2xx identity and timeline responses")
        if state is None:
            raise ValueError("successful X probe requires database state")
        expected_scope = "terminal_checkpoint" if state["x_cursor_state"] == "checkpointed" else "draining_connectivity"
        if report["state_scope"] != expected_scope:
            raise ValueError("X state scope does not match database state")
        if live["terminal_checkpoint_verified"] is not (expected_scope == "terminal_checkpoint"):
            raise ValueError("X terminal checkpoint flag does not match state scope")
    else:
        if report["phase"] not in {
            "setup", "token_check", "offline_proof_validation", "identity_request", "timeline_request", "database_state_validation",
        }:
            raise ValueError("invalid failed X phase")
        if not failures or not isinstance(report["recovery"], str) or not report["recovery"]:
            raise ValueError("failed X probe needs a structured failure and recovery")
        if len(failures) != 1:
            raise ValueError("failed X probe must contain one structured failure")
        if report["state_scope"] is not None or live["terminal_checkpoint_verified"]:
            raise ValueError("failed X probe cannot claim a successful state scope/checkpoint")
    if report["report_payload_sha256"] != canonical_payload_sha256(report):
        raise ValueError("invalid X probe payload hash")
    return report


def atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)
