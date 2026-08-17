from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

import pytest

from airadar.audit.receipts import OBSERVATION_RECEIPT_CODE_PATHS, canonical_payload_sha256
from scripts.audit_aihot_sources import (
    ObservationTraversalFailure,
    persist_successful_observation,
    reconcile_sources,
    traverse_pages,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "tests/fixtures/aihot_sources.json"
FAILED_SOURCE_DELTA_FIXTURE = ROOT / "tests/fixtures/aihot_failed_source_delta.json"


def test_terminal_cursor_traversal() -> None:
    pages = iter([
        {"data": [{"sourceName": "X：OpenAI (@OpenAI)", "url": "https://x.com/OpenAI/status/1"}], "hasMore": True, "nextCursor": "c2"},
        {"data": [{"sourceName": "OpenAI official updates", "url": "https://openai.com/index/a"}], "hasMore": False, "nextCursor": None},
    ])
    report = traverse_pages(lambda _cursor: (next(pages), b"raw-response"))
    assert report["pagination"]["terminal_reached"] is True
    assert report["pagination"]["page_count"] == 2
    assert report["observed_item_count"] == 2


def test_v1_envelope_cursor_traversal() -> None:
    pages = iter([
        {
            "items": [{
                "source": {"name": "X：OpenAI (@OpenAI)"},
                "links": {"original": "https://x.com/OpenAI/status/1"},
            }],
            "page": {"hasMore": True, "nextCursor": "c2"},
        },
        {
            "items": [{
                "source": {"name": "OpenAI official updates"},
                "links": {"original": "https://openai.com/index/a"},
            }],
            "page": {"hasMore": False, "nextCursor": None},
        },
    ])

    report = traverse_pages(lambda _cursor: (next(pages), b"raw-response"))

    assert report["pagination"]["page_count"] == 2
    assert report["observed_source_item_urls"] == {
        "OpenAI official updates": ["https://openai.com/index/a"],
        "X：OpenAI (@OpenAI)": ["https://x.com/OpenAI/status/1"],
    }


@pytest.mark.parametrize("page", [
    {"data": [], "hasMore": True, "nextCursor": None},
    {"data": [], "hasMore": True, "nextCursor": "same"},
    {"data": "bad", "hasMore": False, "nextCursor": None},
])
def test_invalid_cursor_or_page_fails(page: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        traverse_pages(lambda _cursor: (page, b"raw-response"), initial_seen={"same"})


def test_page_manifest_hashes_exact_raw_response_bytes() -> None:
    page = {"data": [], "hasMore": False, "nextCursor": None}

    report = traverse_pages(lambda _cursor: (page, b'{ "data": [] }\n'))

    assert report["pagination"]["pages"][0]["raw_response_body_sha256"] == hashlib.sha256(
        b'{ "data": [] }\n'
    ).hexdigest()


@pytest.mark.parametrize(
    ("item", "message"),
    [
        ({"url": "https://example.test/a"}, "source name"),
        ({"sourceName": "A"}, "original URL"),
        ({"sourceName": "A", "url": "ftp://example.test/a"}, "original URL"),
    ],
)
def test_traversal_rejects_items_without_valid_source_and_original_url(
    item: dict[str, object],
    message: str,
) -> None:
    page = {"data": [item], "hasMore": False, "nextCursor": None}

    with pytest.raises(ValueError, match=message):
        traverse_pages(lambda _cursor: (page, b"raw-response"))


def test_x_display_name_cannot_substitute_for_non_x_original_url(tmp_path: Path) -> None:
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps({"sources": [{
        "derived_aihot_identity": "x:openai",
        "name": "X: OpenAI (@OpenAI)",
        "aihot_aliases": [],
        "ai_radar_main_timeline_member": True,
    }]}))

    result = reconcile_sources(
        {"X: OpenAI (@OpenAI)": ["https://example.test/not-an-x-status"]},
        contract,
    )

    assert result["matched"] == []
    assert result["conflicting"] == [{
        "display_identity": "x:openai",
        "source": "X: OpenAI (@OpenAI)",
        "url_identity": None,
    }]


def test_x_alias_without_handle_cannot_substitute_for_status_url(tmp_path: Path) -> None:
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps({"sources": [{
        "derived_aihot_identity": "x:openai",
        "name": "OpenAI updates",
        "aihot_aliases": [],
        "ai_radar_main_timeline_member": True,
    }]}))

    result = reconcile_sources(
        {"OpenAI updates": ["https://example.test/not-an-x-status"]},
        contract,
    )

    assert result["matched"] == []
    assert result["conflicting"] == [{
        "display_identity": "x:openai",
        "source": "OpenAI updates",
        "url_identity": None,
    }]


def test_traversal_failure_retains_partial_page_manifest_for_failed_receipt() -> None:
    calls = 0

    def fetch(_cursor: str | None) -> tuple[dict[str, object], bytes]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return ({"data": [{"sourceName": "A", "url": "https://example.test/a"}], "hasMore": True, "nextCursor": "c2"}, b"first")
        raise RuntimeError("injected fetch failure")

    with pytest.raises(ObservationTraversalFailure) as captured:
        traverse_pages(fetch)

    traversal = captured.value.traversal
    assert traversal["pagination"]["terminal_reached"] is False
    assert traversal["pagination"]["page_count"] == 1
    assert traversal["observed_source_item_urls"] == {"A": ["https://example.test/a"]}


def test_contract_reconciliation_reports_unmapped(tmp_path) -> None:
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps({"sources": []}))
    result = reconcile_sources({"Unknown": ["https://example.test/a"]}, contract)
    assert result["unmapped"] == ["Unknown"]


def test_current_failed_observation_reconciles_after_accepting_fresh_source_delta() -> None:
    observation = json.loads(FAILED_SOURCE_DELTA_FIXTURE.read_text(encoding="utf-8"))
    assert observation["status"] == "failed"

    result = reconcile_sources(observation["observed_source_item_urls"], CONTRACT)

    assert result["unmapped"] == []
    assert result["ambiguous"] == []
    assert result["conflicting"] == []


def test_reconciliation_reports_excluded_wechat_separately(tmp_path: Path) -> None:
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps({"sources": []}))

    result = reconcile_sources(
        {"公众号：智谱（GLM）": ["https://mp.weixin.qq.com/s/example"]},
        contract,
    )

    assert result["excluded_wechat"] == ["公众号：智谱（GLM）"]
    assert result["unmapped"] == []


def test_reconciliation_rejects_conflicting_x_display_identity(tmp_path: Path) -> None:
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps({"sources": [
        {
            "derived_aihot_identity": "x:openai",
            "aihot_aliases": ["X: OpenAI (@OpenAI)"],
            "ai_radar_main_timeline_member": True,
        },
        {
            "derived_aihot_identity": "x:anthropicai",
            "aihot_aliases": ["X: Anthropic (@AnthropicAI)"],
            "ai_radar_main_timeline_member": True,
        },
    ]}))

    result = reconcile_sources(
        {"X: OpenAI (@OpenAI)": ["https://x.com/AnthropicAI/status/1"]},
        contract,
    )

    assert result["conflicting"] == [{
        "display_identity": "x:openai",
        "source": "X: OpenAI (@OpenAI)",
        "url_identity": "x:anthropicai",
    }]
    assert result["matched"] == []


def _successful_report() -> dict[str, object]:
    report: dict[str, object] = {
        "schema_version": 1,
        "artifact_type": "aihot_observation",
        "status": "success",
        "captured_at": "2026-08-13T01:02:03Z",
        "endpoint": "https://aihot.virxact.com/api/v1/items",
        "query": {"mode": "all", "by": "timeline", "window": "7d", "limit": 100},
        "contract_sha256": hashlib.sha256(CONTRACT.read_bytes()).hexdigest(),
        "code_sha256_by_path_relative_to_repository_root": {
            path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
            for path in OBSERVATION_RECEIPT_CODE_PATHS
        },
        "pagination": {"terminal_reached": True, "page_count": 1, "pages": [{"request_cursor": None, "response_next_cursor": None, "response_has_more": False, "response_item_count": 0, "raw_response_body_sha256": "a" * 64}]},
        "observed_item_count": 0,
        "observed_source_count": 0,
        "observed_source_item_urls": {},
        "reconciliation": {"matched": [], "renamed": [], "excluded_wechat": [], "ambiguous": [], "unmapped": [], "conflicting": []},
        "failure": None,
    }
    report["report_payload_sha256"] = canonical_payload_sha256(report)
    return report


def test_successful_observations_are_append_only_and_indexed(tmp_path: Path) -> None:
    report = _successful_report()
    output = tmp_path / "aihot-observation-final.json"

    daily = persist_successful_observation(
        report,
        artifacts_dir=tmp_path,
        output_path=output,
        contract_path=CONTRACT,
    )

    assert daily.suffix == ".json"
    assert str(uuid.UUID(daily.stem)) == daily.stem
    assert json.loads(output.read_text()) == report
    index = json.loads((tmp_path / "observations" / "index.json").read_text())
    assert index["observations"] == [{
        "artifact_sha256": hashlib.sha256(daily.read_bytes()).hexdigest(),
        "artifact_path_relative_to_artifacts_dir": f"observations/{daily.name}",
    }]
    assert "latest" not in index
    second = persist_successful_observation(
        report,
        artifacts_dir=tmp_path,
        output_path=tmp_path / "second-final.json",
        contract_path=CONTRACT,
    )
    assert second != daily
    updated_index = json.loads((tmp_path / "observations" / "index.json").read_text())
    assert len(updated_index["observations"]) == 2


def test_observation_index_append_survives_contract_evolution(tmp_path: Path) -> None:
    first = _successful_report()
    persist_successful_observation(
        first,
        artifacts_dir=tmp_path,
        output_path=tmp_path / "first-final.json",
        contract_path=CONTRACT,
    )

    evolved_contract = tmp_path / "evolved-contract.json"
    evolved_payload = json.loads(CONTRACT.read_text(encoding="utf-8"))
    evolved_payload["sources"][0]["name"] = "AI as Normal Technology (renamed)"
    evolved_contract.write_text(
        json.dumps(evolved_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    second = _successful_report()
    second["captured_at"] = "2026-08-14T01:02:03Z"
    second["contract_sha256"] = hashlib.sha256(evolved_contract.read_bytes()).hexdigest()
    second["report_payload_sha256"] = canonical_payload_sha256(second)

    persist_successful_observation(
        second,
        artifacts_dir=tmp_path,
        output_path=tmp_path / "second-final.json",
        contract_path=evolved_contract,
    )

    index = json.loads((tmp_path / "observations/index.json").read_text(encoding="utf-8"))
    assert len(index["observations"]) == 2


def test_observation_index_integrity_is_checked_before_append(tmp_path: Path) -> None:
    report = _successful_report()
    observations = tmp_path / "observations"
    observations.mkdir()
    (observations / "index.json").write_text(json.dumps({"schema_version": 1, "artifact_type": "aihot_observation_index", "path_base": "artifacts_dir", "observations": [{
        "artifact_sha256": "0" * 64,
        "path": "observations/missing.json",
    }]}))

    with pytest.raises(ValueError, match="index integrity"):
        persist_successful_observation(
            report,
            artifacts_dir=tmp_path,
            output_path=tmp_path / "final.json",
            contract_path=CONTRACT,
        )
    assert not [path for path in observations.glob("*.json") if path.name != "index.json"]


@pytest.mark.parametrize("legacy_field", ["latest", "capture_date_utc"])
def test_observation_index_rejects_removed_legacy_fields(tmp_path: Path, legacy_field: str) -> None:
    report = _successful_report()
    observations = tmp_path / "observations"
    observations.mkdir()
    entry: dict[str, object] = {
        "artifact_sha256": "0" * 64,
        "artifact_path_relative_to_artifacts_dir": "observations/missing.json",
    }
    index: dict[str, object] = {
        "schema_version": 1,
        "artifact_type": "aihot_observation_index",
        "path_base": "artifacts_dir",
        "observations": [entry],
    }
    if legacy_field == "latest":
        index[legacy_field] = {}
    else:
        entry[legacy_field] = "2026-08-13"
    (observations / "index.json").write_text(json.dumps(index), encoding="utf-8")

    with pytest.raises(ValueError, match="index integrity"):
        persist_successful_observation(
            report,
            artifacts_dir=tmp_path,
            output_path=tmp_path / "final.json",
            contract_path=CONTRACT,
        )


@pytest.mark.parametrize("failure_point", ["daily", "output", "index"])
def test_each_observation_write_failure_leaves_no_dangling_commit_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    report = _successful_report()
    output = tmp_path / "final.json"
    from scripts import audit_aihot_sources as module
    real_atomic_json = module.atomic_json

    def fail_output(path: Path, payload: dict[str, object]) -> None:
        point = "index" if path.name == "index.json" else "output" if path == output else "daily"
        if point == failure_point:
            raise OSError(f"injected {failure_point} failure")
        real_atomic_json(path, payload)

    monkeypatch.setattr(module, "atomic_json", fail_output)
    with pytest.raises(OSError, match=f"injected {failure_point}"):
        persist_successful_observation(
            report,
            artifacts_dir=tmp_path,
            output_path=output,
            contract_path=CONTRACT,
        )
    assert not (tmp_path / "observations" / "index.json").exists()
    assert not [
        path for path in (tmp_path / "observations").glob("*.json")
        if path.name != "index.json"
    ]


def test_later_index_failure_preserves_previously_indexed_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _successful_report()
    first_output = tmp_path / "first.json"
    first_daily = persist_successful_observation(
        first,
        artifacts_dir=tmp_path,
        output_path=first_output,
        contract_path=CONTRACT,
    )
    index_path = tmp_path / "observations/index.json"
    indexed_before = index_path.read_bytes()

    second = _successful_report()
    second["captured_at"] = "2026-08-14T01:02:03Z"
    second["report_payload_sha256"] = canonical_payload_sha256(second)
    second_output = tmp_path / "second.json"
    from scripts import audit_aihot_sources as module
    real_atomic_json = module.atomic_json

    def fail_new_index(path: Path, payload: dict[str, object]) -> None:
        if path == index_path:
            raise OSError("injected later index failure")
        real_atomic_json(path, payload)

    monkeypatch.setattr(module, "atomic_json", fail_new_index)
    with pytest.raises(OSError, match="later index"):
        persist_successful_observation(
            second,
            artifacts_dir=tmp_path,
            output_path=second_output,
            contract_path=CONTRACT,
        )

    assert first_daily.is_file()
    assert first_output.is_file()
    assert index_path.read_bytes() == indexed_before
    assert not second_output.exists()
    assert len([
        path for path in (tmp_path / "observations").glob("*.json")
        if path.name != "index.json"
    ]) == 1


def test_cli_refuses_to_overwrite_existing_observation_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts import audit_aihot_sources as module

    output = tmp_path / "existing.json"
    output.write_text('{"historical":true}\n', encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["audit_aihot_sources.py", "--output", str(output)])

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        module.main()
    assert output.read_text(encoding="utf-8") == '{"historical":true}\n'
