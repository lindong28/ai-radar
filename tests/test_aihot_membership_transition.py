from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from airadar.audit.receipts import OBSERVATION_RECEIPT_CODE_PATHS, canonical_payload_sha256
from scripts.check_aihot_membership_transition import check_transition

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "tests/fixtures/aihot_sources.json"


def _write_json(path: Path, payload: dict[str, object]) -> str:
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _authorization(
    identity: str,
    *,
    action: str = "retire",
    allows: bool = True,
    evidence_class: str = "user_decision",
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "artifact_type": "aihot_retirement_evidence",
        "derived_aihot_identity": identity,
        "action": action,
        "evidence_class": evidence_class,
        "previous_contract_sha256": "a" * 64,
        "next_contract_sha256": "b" * 64,
        "authorized_at": "2026-08-13T00:00:00Z",
        "allows_retirement": allows,
    }
    return payload


def _ledger_row(path: Path, digest: str) -> dict[str, object]:
    return {
        "derived_aihot_identity": "feed:a",
        "evidence_class": "user_decision",
        "previous_contract_sha256": "a" * 64,
        "next_contract_sha256": "b" * 64,
        "evidence_path_relative_to_ledger_dir": path.name,
        "evidence_sha256": digest,
    }


def test_unauthorized_removal_fails() -> None:
    with pytest.raises(ValueError, match="unauthorized"):
        check_transition({"feed:a"}, set(), [])


def test_user_decision_retirement_passes(tmp_path: Path) -> None:
    evidence = tmp_path / "decision.json"
    digest = _write_json(evidence, _authorization("feed:a"))
    check_transition(
        {"feed:a"},
        set(),
        [_ledger_row(evidence, digest)],
        previous_sha256="a" * 64,
        next_sha256="b" * 64,
        root=tmp_path,
    )


def test_official_evidence_requires_hash_bound_actual_capture(tmp_path: Path) -> None:
    evidence = tmp_path / "official.json"
    payload = _authorization("feed:a", evidence_class="official_shutdown")
    payload.update(
        official_evidence_artifact_path_relative_to_ledger_dir="missing-official-capture.html",
        official_evidence_artifact_sha256="c" * 64,
        official_evidence_source_url="https://example.com/official-shutdown",
    )
    digest = _write_json(evidence, payload)
    row = _ledger_row(evidence, digest)
    row["evidence_class"] = "official_shutdown"

    with pytest.raises(ValueError, match="official evidence artifact"):
        check_transition(
            {"feed:a"}, set(), [row],
            previous_sha256="a" * 64, next_sha256="b" * 64, root=tmp_path,
        )


def test_official_evidence_accepts_bound_capture(tmp_path: Path) -> None:
    capture = tmp_path / "official-capture.html"
    capture.write_text("<html>Official shutdown notice</html>\n", encoding="utf-8")
    payload = _authorization("feed:a", evidence_class="official_shutdown")
    payload.update(
        official_evidence_artifact_path_relative_to_ledger_dir=capture.name,
        official_evidence_artifact_sha256=hashlib.sha256(capture.read_bytes()).hexdigest(),
        official_evidence_source_url="https://example.com/official-shutdown",
    )
    evidence = tmp_path / "official.json"
    digest = _write_json(evidence, payload)
    row = _ledger_row(evidence, digest)
    row["evidence_class"] = "official_shutdown"

    check_transition(
        {"feed:a"}, set(), [row],
        previous_sha256=str(row["previous_contract_sha256"]), next_sha256="b" * 64, root=tmp_path,
    )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda payload: payload.update(action="keep"), "retire"),
        (lambda payload: payload.update(allows_retirement=False), "allow"),
        (lambda payload: payload.update(derived_aihot_identity="feed:other"), "identity"),
        (lambda payload: payload.update(extra="unrelated"), "fields"),
    ],
)
def test_user_evidence_must_be_exact_and_authorize_the_same_retirement(
    tmp_path: Path,
    mutation,  # noqa: ANN001
    message: str,
) -> None:
    payload = _authorization("feed:a")
    mutation(payload)
    evidence = tmp_path / "decision.json"
    digest = _write_json(evidence, payload)
    with pytest.raises(ValueError, match=message):
        check_transition(
            {"feed:a"},
            set(),
            [_ledger_row(evidence, digest)],
            previous_sha256="a" * 64,
            next_sha256="b" * 64,
            root=tmp_path,
        )


def _observation(
    captured_at: str,
    *,
    contract_path: Path,
    identity: str | None = None,
) -> dict[str, object]:
    sources = {} if identity is None else {"AI as Normal Technology": ["https://example.test/a"]}
    matched = [] if identity is None else [{"source": "AI as Normal Technology", "identity": identity}]
    payload: dict[str, object] = {
        "schema_version": 1,
        "artifact_type": "aihot_observation",
        "status": "success",
        "captured_at": captured_at,
        "endpoint": "https://aihot.virxact.com/api/v1/items",
        "query": {"mode": "all", "by": "timeline", "window": "7d", "limit": 100},
        "contract_sha256": hashlib.sha256(contract_path.read_bytes()).hexdigest(),
        "code_sha256_by_path_relative_to_repository_root": {
            path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
            for path in OBSERVATION_RECEIPT_CODE_PATHS
        },
        "pagination": {
            "terminal_reached": True,
            "page_count": 1,
            "pages": [{
                "request_cursor": None,
                "response_next_cursor": None,
                "response_has_more": False,
                "response_item_count": len(sources),
                "raw_response_body_sha256": "d" * 64,
            }],
        },
        "reconciliation": {
            "matched": matched,
            "renamed": [],
            "excluded_wechat": [],
            "ambiguous": [],
            "unmapped": [],
            "conflicting": [],
        },
        "observed_source_item_urls": sources,
        "observed_source_count": len(sources),
        "observed_item_count": len(sources),
        "failure": None,
    }
    payload["report_payload_sha256"] = canonical_payload_sha256(payload)
    return payload


def _thirty_day_row(tmp_path: Path) -> dict[str, object]:
    previous_contract = tmp_path / "previous-contract.json"
    previous_contract.write_bytes(CONTRACT.read_bytes())
    previous_contract_sha = hashlib.sha256(previous_contract.read_bytes()).hexdigest()
    references = []
    start = datetime(2026, 7, 1, tzinfo=UTC)
    for offset in range(30):
        path = tmp_path / f"observation-{offset:02}.json"
        captured_at = (start + timedelta(days=offset)).isoformat().replace("+00:00", "Z")
        digest = _write_json(path, _observation(captured_at, contract_path=previous_contract))
        references.append({
            "artifact_path_relative_to_ledger_dir": path.name,
            "artifact_sha256": digest,
            "contract_artifact_path_relative_to_ledger_dir": previous_contract.name,
            "contract_artifact_sha256": previous_contract_sha,
        })
    return {
        "derived_aihot_identity": "feed:ai_normal_technology",
        "evidence_class": "thirty_day_absence",
        "previous_contract_sha256": previous_contract_sha,
        "next_contract_sha256": "b" * 64,
        "observation_artifacts": references,
    }


def test_thirty_day_absence_uses_unique_successful_receipts_and_target_absence(tmp_path: Path) -> None:
    row = _thirty_day_row(tmp_path)
    check_transition(
        {"feed:ai_normal_technology"}, set(), [row],
        previous_sha256=str(row["previous_contract_sha256"]), next_sha256="b" * 64, root=tmp_path,
        contract_path=CONTRACT,
    )


@pytest.mark.parametrize("case", ["duplicate", "date_gap", "still_present", "failed_reconciliation"])
def test_thirty_day_absence_rejects_reviewer_counterexamples(tmp_path: Path, case: str) -> None:
    row = _thirty_day_row(tmp_path)
    references = row["observation_artifacts"]
    assert isinstance(references, list)
    if case == "duplicate":
        references[-1] = copy.deepcopy(references[0])
    else:
        reference = references[-1]
        assert isinstance(reference, dict)
        path = tmp_path / str(reference["artifact_path_relative_to_ledger_dir"])
        captured_at = "2026-08-01T00:00:00Z" if case == "date_gap" else "2026-07-30T00:00:00Z"
        payload = _observation(
            captured_at,
            contract_path=tmp_path / str(reference["contract_artifact_path_relative_to_ledger_dir"]),
            identity=None if case == "date_gap" else "feed:ai_normal_technology",
        )
        if case == "failed_reconciliation":
            payload["status"] = "failed"
            payload["reconciliation"] = {
                "matched": [],
                "renamed": [],
                "excluded_wechat": [],
                "ambiguous": [],
                "unmapped": ["Present"],
                "conflicting": [],
            }
            payload["failure"] = {
                "error_class": "ReconciliationError",
                "error_message": "unmapped",
            }
            payload["report_payload_sha256"] = canonical_payload_sha256(payload)
        reference["artifact_sha256"] = _write_json(path, payload)
    with pytest.raises(ValueError):
        check_transition(
            {"feed:ai_normal_technology"}, set(), [row],
            previous_sha256=str(row["previous_contract_sha256"]), next_sha256="b" * 64, root=tmp_path,
            contract_path=CONTRACT,
        )
