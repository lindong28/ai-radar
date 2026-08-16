from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from airadar.audit.receipts import (
    NON_X_RECEIPT_CODE_PATHS,
    OBSERVATION_RECEIPT_CODE_PATHS,
    X_OFFLINE_PROOF_NODE_IDS,
    X_PROBE_RECEIPT_CODE_PATHS,
    canonical_item_set,
    canonical_payload_sha256,
    validate_non_x_receipt,
    validate_observation_receipt,
    validate_x_offline_proof_receipt,
    validate_x_probe_receipt,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "tests/fixtures/aihot_sources.json"
CONFIG = ROOT / "data/sources.toml"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _seal(payload: dict[str, object]) -> dict[str, object]:
    payload["report_payload_sha256"] = canonical_payload_sha256(payload)
    return payload


def _observation(*, empty: bool = False) -> dict[str, object]:
    if empty:
        observed: dict[str, list[str]] = {}
        reconciliation = {
            "matched": [], "renamed": [], "excluded_wechat": [],
            "ambiguous": [], "unmapped": [], "conflicting": [],
        }
        item_count = 0
    else:
        observed = {
            "AI as Normal Technology": ["https://example.test/1"],
            "公众号：Known": ["https://mp.weixin.qq.com/s/1"],
        }
        reconciliation = {
            "matched": [{"source": "AI as Normal Technology", "identity": "feed:ai_normal_technology"}],
            "renamed": [],
            "excluded_wechat": ["公众号：Known"],
            "ambiguous": [],
            "unmapped": [],
            "conflicting": [],
        }
        item_count = 2
    return _seal({
        "schema_version": 1,
        "artifact_type": "aihot_observation",
        "status": "success",
        "captured_at": "2026-08-13T00:00:00Z",
        "endpoint": "https://aihot.virxact.com/api/v1/items",
        "query": {"mode": "all", "by": "timeline", "window": "7d", "limit": 100},
        "contract_sha256": _sha(CONTRACT),
        "code_sha256_by_path_relative_to_repository_root": {
            path: _sha(ROOT / path) for path in OBSERVATION_RECEIPT_CODE_PATHS
        },
        "pagination": {
            "terminal_reached": True,
            "page_count": 1,
            "pages": [{
                "request_cursor": None,
                "response_next_cursor": None,
                "response_has_more": False,
                "response_item_count": item_count,
                "raw_response_body_sha256": "a" * 64,
            }],
        },
        "reconciliation": reconciliation,
        "observed_source_item_urls": observed,
        "observed_source_count": len(observed),
        "observed_item_count": item_count,
        "failure": None,
    })


def test_observation_validator_accepts_zero_item_terminal_page() -> None:
    assert validate_observation_receipt(
        _observation(empty=True), contract_path=CONTRACT, repository_root=ROOT
    )["status"] == "success"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda row: row.update(endpoint="https://example.test/items"), "endpoint"),
        (lambda row: row["query"].update(window="30d"), "query"),
        (lambda row: row["pagination"].update(terminal_reached=False), "terminal"),
        (lambda row: row["reconciliation"].update(matched=[]), "reconciliation"),
        (lambda row: row["reconciliation"]["matched"][0].update(identity="feed:does_not_exist"), "reconciliation"),
        (lambda row: row.update(observed_item_count=3), "count"),
    ],
)
def test_observation_validator_recomputes_reconciliation_and_terminal_state(mutation, message: str) -> None:  # noqa: ANN001
    payload = copy.deepcopy(_observation())
    mutation(payload)
    payload["report_payload_sha256"] = canonical_payload_sha256(payload)
    with pytest.raises(ValueError, match=message):
        validate_observation_receipt(payload, contract_path=CONTRACT, repository_root=ROOT)


def test_observation_validator_rejects_removed_nested_failure_stage() -> None:
    payload = _observation()
    payload.update(status="failed")
    payload["pagination"]["terminal_reached"] = False
    payload["pagination"]["pages"][-1].update(
        response_has_more=True,
        response_next_cursor="next-page",
    )
    payload["failure"] = {
        "error_class": "ReconciliationError",
        "error_message": "unmapped",
        "failure" + "_stage": "reconciliation",
    }
    payload["report_payload_sha256"] = canonical_payload_sha256(payload)

    with pytest.raises(ValueError, match="observation failure fields"):
        validate_observation_receipt(payload, contract_path=CONTRACT, repository_root=ROOT)


@pytest.mark.parametrize("artifact", ["observation", "non_x", "x_probe"])
@pytest.mark.parametrize("mutation", ["missing", "extra"])
def test_receipt_code_hash_sets_are_exact(artifact: str, mutation: str) -> None:
    if artifact == "observation":
        payload = _observation(empty=True)
        validate = lambda row: validate_observation_receipt(  # noqa: E731
            row, contract_path=CONTRACT, repository_root=ROOT
        )
    elif artifact == "non_x":
        payload = _non_x()
        validate = _validate_non_x
    else:
        payload = _x_success(ROOT)
        validate = lambda row: validate_x_probe_receipt(  # noqa: E731
            row, config_path=CONFIG, contract_path=CONTRACT, repository_root=ROOT
        )
    hashes = payload["code_sha256_by_path_relative_to_repository_root"]
    assert isinstance(hashes, dict)
    if mutation == "missing":
        hashes.pop(next(iter(hashes)))
    else:
        hashes["README.md"] = _sha(ROOT / "README.md")
    payload["report_payload_sha256"] = canonical_payload_sha256(payload)

    with pytest.raises(ValueError, match="code paths"):
        validate(payload)


def _round(*, inserted: int, status: int = 200, response_sha: str = "c" * 64) -> dict[str, object]:
    items = canonical_item_set({("https://example.test/a", "A")})
    return {
        "request_attempt_count": 1,
        "request_url": "https://example.test/feed",
        "final_response_url": "https://example.test/feed",
        "http_status_code": status,
        "response_body_sha256": response_sha,
        "oracle_canonical_item_set": items,
        "production_canonical_item_set": items,
        "persisted_canonical_item_set": items,
        "inserted_item_count": inserted,
    }


def _non_x_source(kind: str) -> dict[str, object]:
    replay = copy.deepcopy(_round(inserted=0))
    replay["request_attempt_count"] = 0
    for key in ("request_url", "final_response_url", "http_status_code"):
        replay.pop(key)
    return {
        "kind": kind,
        "first_live": _round(inserted=1),
        "immutable_replay": replay,
        "second_live": _round(inserted=0),
    }


def _non_x() -> dict[str, object]:
    rows = json.loads(CONTRACT.read_text(encoding="utf-8"))["sources"]
    sources = {
        row["slug"]: _non_x_source(row["kind"])
        for row in rows
        if row["kind"] in {"feed", "web"}
    }
    feed_count = sum(row["kind"] == "feed" for row in rows)
    web_count = sum(row["kind"] == "web" for row in rows)
    non_x_count = len(sources)
    return _seal({
        "schema_version": 1,
        "artifact_type": "non_x_retrieval",
        "status": "success",
        "captured_at": "2026-08-13T00:00:00Z",
        "phase": "complete",
        "probe_scope": {"kinds": ["feed", "web"], "live_round_count": 2, "immutable_replay_round_count": 1},
        "database_path_absolute": "/tmp/new.db",
        "config_sha256": _sha(CONFIG),
        "contract_sha256": _sha(CONTRACT),
        "code_sha256_by_path_relative_to_repository_root": {
            path: _sha(ROOT / path) for path in NON_X_RECEIPT_CODE_PATHS
        },
        "source_counts": {
            "contract_non_x_source_count": non_x_count,
            "contract_feed_source_count": feed_count,
            "contract_web_source_count": web_count,
            "configured_non_x_source_count": non_x_count,
            "attempted_source_count": non_x_count,
            "successful_source_count": non_x_count,
            "failed_source_count": 0,
        },
        "sources": sources,
        "failures": [],
        "recovery": None,
    })


def _validate_non_x(payload: dict[str, object]) -> dict[str, object]:
    return validate_non_x_receipt(
        payload,
        config_path=CONFIG,
        contract_path=CONTRACT,
        repository_root=ROOT,
    )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda row: row["sources"].__setitem__("fake_member", row["sources"].pop("ai_normal_technology")), "membership"),
        (lambda row: row["sources"]["ai_normal_technology"].update(kind="web"), "kind"),
        (lambda row: row["sources"]["ai_normal_technology"]["first_live"].update(http_status_code=304), "first live"),
        (lambda row: row["sources"]["ai_normal_technology"]["first_live"].update(
            oracle_canonical_item_set=canonical_item_set(set()),
            production_canonical_item_set=canonical_item_set(set()),
            persisted_canonical_item_set=canonical_item_set(set()),
        ), "non-empty"),
        (lambda row: row["sources"]["ai_normal_technology"]["immutable_replay"].update(response_body_sha256="e" * 64), "response body"),
        (lambda row: row["sources"]["ai_normal_technology"]["second_live"]["production_canonical_item_set"].update(canonical_items=[]), "canonical"),
        (lambda row: row.update(recovery="rerun"), "recovery"),
    ],
)
def test_non_x_validator_rejects_reviewer_counterexamples(mutation, message: str) -> None:  # noqa: ANN001
    payload = copy.deepcopy(_non_x())
    mutation(payload)
    payload["report_payload_sha256"] = canonical_payload_sha256(payload)
    with pytest.raises(ValueError, match=message):
        _validate_non_x(payload)


@pytest.mark.parametrize("phase", ["first_live", "immutable_replay", "second_live"])
def test_non_x_live_phase_failure_requires_source_and_closed_counts(phase: str) -> None:
    payload = _non_x()
    payload.update(status="failed", phase=phase, sources={})
    payload["source_counts"].update(attempted_source_count=1, successful_source_count=0, failed_source_count=1)
    payload["failures"] = [{
        "source_id": None,
        "error_class": "InjectedError",
        "error_message": "injected",
        "request_attempt_count": 1 if phase != "immutable_replay" else 0,
        "http_status_code": 500 if phase != "immutable_replay" else None,
    }]
    payload["recovery"] = "fix_then_rerun"
    payload["report_payload_sha256"] = canonical_payload_sha256(payload)
    with pytest.raises(ValueError, match="source_id"):
        _validate_non_x(payload)


def test_non_x_validator_rejects_removed_nested_failure_stage() -> None:
    payload = _non_x()
    payload.update(status="failed", phase="first_live", sources={})
    payload["source_counts"].update(
        attempted_source_count=1,
        successful_source_count=0,
        failed_source_count=1,
    )
    payload["failures"] = [{
        "source_id": "ai_normal_technology",
        "error_class": "InjectedError",
        "error_message": "injected",
        "request_attempt_count": 1,
        "http_status_code": 500,
        "failure" + "_stage": "first_live",
    }]
    payload["recovery"] = "fix_then_rerun"
    payload["report_payload_sha256"] = canonical_payload_sha256(payload)

    with pytest.raises(ValueError, match="non-X failure fields"):
        _validate_non_x(payload)


def _offline_receipt(root: Path) -> dict[str, object]:
    test_path = root / "tests/test_x_api.py"
    production = [
        root / "src/airadar/fetcher/x_api.py",
        root / "src/airadar/fetcher/runner.py",
        root / "src/airadar/sources/x_state.py",
    ]
    return _seal({
        "schema_version": 1,
        "artifact_type": "x_pagination_offline_test",
        "status": "success",
        "captured_at": "2026-08-13T00:00:00Z",
        "pytest_node_ids": list(X_OFFLINE_PROOF_NODE_IDS),
        "pytest_exit_code": 0,
        "pytest_stdout_sha256": "a" * 64,
        "pytest_stderr_sha256": "b" * 64,
        "test_code_sha256_by_path_relative_to_repository_root": {
            "tests/test_x_api.py": _sha(test_path),
        },
        "production_code_sha256_by_path_relative_to_repository_root": {
            path.relative_to(root).as_posix(): _sha(path) for path in production
        },
    })


def _x_success(root: Path) -> dict[str, object]:
    proof_path = root / "artifacts/x-pagination-offline-receipt.json"
    return _seal({
        "schema_version": 1,
        "artifact_type": "x_single_source_probe",
        "status": "success",
        "captured_at": "2026-08-13T00:00:00Z",
        "phase": "complete",
        "probe_scope": {"identity_requests_max": 1, "timeline_requests_max": 1, "lookback_minutes": 20, "max_results": 5, "backfill": False},
        "source": "x_openai",
        "token_present": True,
        "state_scope": "terminal_checkpoint",
        "request_counts": {"identity_http_request_count": 1, "timeline_http_request_count": 1},
        "http_responses": {"identity_http_status_code": 200, "timeline_http_status_code": 200},
        "config_sha256": _sha(root / "data/sources.toml"),
        "contract_sha256": _sha(root / "tests/fixtures/aihot_sources.json"),
        "code_sha256_by_path_relative_to_repository_root": {
            path: _sha(root / path) for path in X_PROBE_RECEIPT_CODE_PATHS
        },
        "database_state_after_probe": {
            "x_state_schema_version": 1,
            "x_cursor_state": "checkpointed",
            "x_reference_status": "verified",
            "x_reference_validated_at": "2026-08-13T00:00:00Z",
            "x_user_id": "42",
            "x_since_id": "100",
        },
        "live_validation": {
            "identity_connectivity_verified": True,
            "timeline_connectivity_verified": True,
            "terminal_checkpoint_verified": True,
            "live_post_retrieval_verified": True,
            "fetched_item_count": 1,
            "inserted_item_count": 1,
        },
        "offline_proof": {
            "status": "verified",
            "receipt_path_relative_to_repository_root": proof_path.relative_to(root).as_posix(),
            "receipt_sha256": _sha(proof_path),
        },
        "failures": [],
        "recovery": None,
    })


def test_x_offline_proof_validator_requires_fixed_nodes_and_current_hashes(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "src/airadar/fetcher").mkdir(parents=True)
    (tmp_path / "src/airadar/sources").mkdir(parents=True)
    for source, target in [
        (ROOT / "tests/test_x_api.py", tmp_path / "tests/test_x_api.py"),
        (ROOT / "src/airadar/fetcher/x_api.py", tmp_path / "src/airadar/fetcher/x_api.py"),
        (ROOT / "src/airadar/fetcher/runner.py", tmp_path / "src/airadar/fetcher/runner.py"),
        (ROOT / "src/airadar/sources/x_state.py", tmp_path / "src/airadar/sources/x_state.py"),
    ]:
        target.write_bytes(source.read_bytes())
    payload = _offline_receipt(tmp_path)
    assert validate_x_offline_proof_receipt(payload, repository_root=tmp_path)["status"] == "success"
    payload["pytest_node_ids"] = list(reversed(payload["pytest_node_ids"]))
    payload["report_payload_sha256"] = canonical_payload_sha256(payload)
    with pytest.raises(ValueError, match="node"):
        validate_x_offline_proof_receipt(payload, repository_root=tmp_path)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda row: row.update(token_present=False), "token"),
        (lambda row: row["http_responses"].update(identity_http_status_code=401), "2xx"),
        (lambda row: row["request_counts"].update(identity_http_request_count=0), "request"),
        (lambda row: row["live_validation"].update(live_post_retrieval_verified=True, fetched_item_count=0, inserted_item_count=0), "post"),
    ],
)
def test_x_validator_rejects_incoherent_success(mutation, message: str) -> None:  # noqa: ANN001
    payload = copy.deepcopy(_x_success(ROOT))
    mutation(payload)
    payload["report_payload_sha256"] = canonical_payload_sha256(payload)
    with pytest.raises(ValueError, match=message):
        validate_x_probe_receipt(
            payload,
            config_path=CONFIG,
            contract_path=CONTRACT,
            repository_root=ROOT,
        )


def test_x_failure_rejects_zero_requests_with_200_and_connectivity() -> None:
    payload = _x_success(ROOT)
    payload.update(status="failed", phase="identity_request", state_scope=None)
    payload["request_counts"] = {"identity_http_request_count": 0, "timeline_http_request_count": 0}
    payload["http_responses"] = {"identity_http_status_code": 200, "timeline_http_status_code": None}
    payload["database_state_after_probe"] = None
    payload["live_validation"] = {
        "identity_connectivity_verified": True,
        "timeline_connectivity_verified": False,
        "terminal_checkpoint_verified": False,
        "live_post_retrieval_verified": False,
        "fetched_item_count": 0,
        "inserted_item_count": 0,
    }
    payload["offline_proof"] = {"status": "not_evaluated", "receipt_path_relative_to_repository_root": None, "receipt_sha256": None}
    payload["failures"] = [{"reason": "authentication_rejected", "error_class": "HTTPStatusError", "http_status_code": 200}]
    payload["recovery"] = "replace_or_confirm_token_then_rerun_single_source_probe"
    payload["report_payload_sha256"] = canonical_payload_sha256(payload)
    with pytest.raises(ValueError, match="request"):
        validate_x_probe_receipt(
            payload,
            config_path=CONFIG,
            contract_path=CONTRACT,
            repository_root=ROOT,
        )


def test_x_validator_rejects_removed_nested_failure_stage() -> None:
    payload = _x_success(ROOT)
    payload.update(status="failed", phase="identity_request", state_scope=None)
    payload["request_counts"] = {"identity_http_request_count": 1, "timeline_http_request_count": 0}
    payload["http_responses"] = {"identity_http_status_code": 401, "timeline_http_status_code": None}
    payload["database_state_after_probe"] = None
    payload["live_validation"] = {
        "identity_connectivity_verified": False,
        "timeline_connectivity_verified": False,
        "terminal_checkpoint_verified": False,
        "live_post_retrieval_verified": False,
        "fetched_item_count": 0,
        "inserted_item_count": 0,
    }
    payload["offline_proof"] = {
        "status": "not_evaluated",
        "receipt_path_relative_to_repository_root": None,
        "receipt_sha256": None,
    }
    payload["failures"] = [{
        "reason": "authentication_rejected",
        "error_class": "HTTPStatusError",
        "http_status_code": 401,
        "failure" + "_stage": "identity_request",
    }]
    payload["recovery"] = "replace_or_confirm_token_then_rerun_single_source_probe"
    payload["report_payload_sha256"] = canonical_payload_sha256(payload)

    with pytest.raises(ValueError, match="X failure fields"):
        validate_x_probe_receipt(
            payload,
            config_path=CONFIG,
            contract_path=CONTRACT,
            repository_root=ROOT,
        )


@pytest.mark.parametrize(
    ("state_scope", "cursor_state", "terminal", "fetched", "live_post"),
    [
        ("terminal_checkpoint", "checkpointed", True, 0, False),
        ("draining_connectivity", "draining", False, 0, False),
    ],
)
def test_x_success_allows_terminal_zero_item_and_draining_connectivity(
    state_scope: str,
    cursor_state: str,
    terminal: bool,
    fetched: int,
    live_post: bool,
) -> None:
    payload = _x_success(ROOT)
    payload["state_scope"] = state_scope
    payload["database_state_after_probe"]["x_cursor_state"] = cursor_state
    if cursor_state == "draining":
        payload["database_state_after_probe"].update(
            x_pagination_token="next-page",
            x_pending_since_id="101",
            x_since_time="2026-08-13T00:00:00Z",
        )
        payload["database_state_after_probe"].pop("x_since_id")
    payload["live_validation"].update(
        terminal_checkpoint_verified=terminal,
        live_post_retrieval_verified=live_post,
        fetched_item_count=fetched,
        inserted_item_count=0,
    )
    payload["report_payload_sha256"] = canonical_payload_sha256(payload)

    assert validate_x_probe_receipt(
        payload, config_path=CONFIG, contract_path=CONTRACT, repository_root=ROOT
    )["status"] == "success"


def test_x_live_post_flag_is_independent_but_cannot_exist_without_fetched_item() -> None:
    payload = _x_success(ROOT)
    payload["live_validation"].update(fetched_item_count=0, inserted_item_count=0)
    payload["report_payload_sha256"] = canonical_payload_sha256(payload)

    with pytest.raises(ValueError, match="live post"):
        validate_x_probe_receipt(
            payload, config_path=CONFIG, contract_path=CONTRACT, repository_root=ROOT
        )
