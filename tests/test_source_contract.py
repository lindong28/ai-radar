from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from airadar.sources.contract import load_source_contract, validate_source_contract
from airadar.sources.loader import load_sources
from scripts.render_sources_from_contract import render, render_union_receipt

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "tests" / "fixtures" / "aihot_sources.json"
UNION_RECEIPT_PATH = ROOT / "artifacts/source-union-receipt.json"


def test_checked_in_contract_is_the_only_editable_source_authority() -> None:
    payload = load_source_contract(CONTRACT_PATH)

    assert payload["schema_version"] == 2
    assert len(payload["sources"]) == 163
    assert render() == (ROOT / "data" / "sources.toml").read_text(encoding="utf-8")
    assert not (ROOT / "scripts" / "render_aihot_source_contract.py").exists()


def test_contract_has_no_convenience_copies() -> None:
    payload = load_source_contract(CONTRACT_PATH)

    for row in payload["sources"]:
        assert "derived_aihot_identity" in row
        assert "aihot" + "_identity" not in row
        assert row["ai_radar_main_timeline_member"] is (row["kind"] != "wechat")
        assert isinstance(row["paused"], bool)
        assert "main_membership" not in row
        assert "public_url" not in row
        assert "registry_key" not in row
        assert "username" not in row
        assert row["name"].casefold() not in {str(alias).casefold() for alias in row["aihot_aliases"]}
        if row["kind"] != "wechat":
            assert "public_url_override" not in row


def test_contract_wechat_optional_boundary_is_explicit() -> None:
    payload = load_source_contract(CONTRACT_PATH)
    wechat_rows = [row for row in payload["sources"] if row["kind"] == "wechat"]

    # More than one is the point: an incumbent WeChat feed and its candidate
    # replacement run side by side until one of them is switched off.
    assert {row["required_env"] for row in wechat_rows} == {"MP2RSS_FEED_URL", "WECHAT2RSS_FEED_URL"}
    for wechat in wechat_rows:
        assert wechat["optional"] is True
        assert wechat["fetch_url"] == f"${{{wechat['required_env']}}}"
        assert wechat["wechat_only"] is True
        assert wechat["public_url_override"] == "https://mp.weixin.qq.com/"
    assert {row["slug"]: row["paused"] for row in payload["sources"]} == {
        row["slug"]: row["slug"] == "wx_mp2rss" for row in payload["sources"]
    }


def test_fresh_aihot_delta_has_stable_contract_identities_and_observed_aliases() -> None:
    payload = load_source_contract(CONTRACT_PATH)
    rows = {row["derived_aihot_identity"]: row for row in payload["sources"]}

    expected = {
        "web:deepseek_api_updates": "DeepSeek：API 更新日志",
        "x:deepseek_ai": "X：DeepSeek (@deepseek_ai)",
        "x:zhang_benita": "X：張小珺 Xiaojùn (@zhang_benita)",
        "x:siliconflowai": "X：硅基流动 SiliconFlow (@SiliconFlowAI)",
    }
    assert expected.keys() <= rows.keys()
    for identity, observed_name in expected.items():
        row = rows[identity]
        assert observed_name.casefold() in {
            row["name"].casefold(),
            *(alias.casefold() for alias in row["aihot_aliases"]),
        }


def _wechat_rows(payload: dict) -> list[dict]:
    return [row for row in payload["sources"] if row["kind"] == "wechat"]


def _point_both_wechat_feeds_at_one_env(payload: dict) -> None:
    first, second = _wechat_rows(payload)[:2]
    second["required_env"] = first["required_env"]
    second["fetch_url"] = first["fetch_url"]


def _drop_every_wechat_source(payload: dict) -> None:
    payload["sources"] = [row for row in payload["sources"] if row["kind"] != "wechat"]


def _break_wechat_env_placeholder_pairing(payload: dict) -> None:
    _wechat_rows(payload)[0]["required_env"] = "SOME_OTHER_FEED_URL"


def _change_frozen_public_owner_to_feed(payload: dict) -> None:
    owner = next(row for row in payload["sources"] if row["slug"] == "wx_mp2rss")
    owner.update(
        kind="feed",
        derived_aihot_identity="feed:wx_mp2rss",
        fetch_url="https://example.test/wx-mp2rss.xml",
        ai_radar_main_timeline_member=True,
        meta={},
    )
    for field in ("optional", "public_url_override", "required_env", "wechat_only"):
        owner.pop(field)


def test_contract_rejects_non_wechat_frozen_public_owner() -> None:
    payload = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    _change_frozen_public_owner_to_feed(payload)

    with pytest.raises(ValueError, match="wx_mp2rss.*optional WeChat owner"):
        validate_source_contract(payload)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda payload: payload.update(schema_version=1), "schema_version"),
        (lambda payload: payload["sources"][0].update(public_url="https://duplicate.test"), "unknown fields"),
        (lambda payload: payload["sources"][0]["aihot_aliases"].append(payload["sources"][0]["name"]), "public name"),
        (lambda payload: payload["sources"][0].update(optional=True), "optional fields"),
        (lambda payload: payload["sources"][0].pop("paused"), "required fields"),
        (lambda payload: payload["sources"][0].update(paused="false"), "paused.*boolean"),
        (lambda payload: payload["sources"][0].update(derived_aihot_identity="feed:wrong"), "identity"),
        # Two WeChat feeds pointed at one env var would silently fetch the same
        # feed twice, which reads as "the replacement agrees with the incumbent".
        (_point_both_wechat_feeds_at_one_env, "reuses required_env"),
        (_drop_every_wechat_source, "at least one optional WeChat source"),
        (_break_wechat_env_placeholder_pairing, "required_env and fetch_url must match"),
    ],
)
def test_contract_validator_rejects_semantically_invalid_records(mutation, message: str) -> None:  # noqa: ANN001
    payload = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    mutation(payload)

    with pytest.raises(ValueError, match=message):
        validate_source_contract(payload)


@pytest.mark.parametrize("schema_header", ["", "schema_version = 1\n"])
@pytest.mark.parametrize("meta", [{}, {"adapter": "x_api"}, {"adapter": "anything", "username": "not-an-api-route"}])
def test_legacy_source_schema_does_not_interpret_adapter_routing(
    tmp_path: Path,
    schema_header: str,
    meta: dict[str, str],
) -> None:
    meta_lines = "\n".join(f'{key} = "{value}"' for key, value in meta.items())
    config = tmp_path / "sources.toml"
    config.write_text(
        schema_header
        + """
[[source]]
slug = "legacy_x"
name = "Legacy X-shaped RSS"
url = "https://api.x.com/2/users/by/username/NotAValidatedIdentity"
tier = "T2"
kind = "x"
"""
        + (f"\n[source.meta]\n{meta_lines}\n" if meta else ""),
        encoding="utf-8",
    )

    [source] = load_sources(config)

    assert source.meta == meta


def test_contract_validation_does_not_mutate_caller_payload() -> None:
    payload = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    before = copy.deepcopy(payload)

    validate_source_contract(payload)

    assert payload == before


def test_contract_rejects_cross_identity_public_name_alias_collision() -> None:
    payload = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    first, second = payload["sources"][:2]
    second["aihot_aliases"].append(first["name"].swapcase())

    with pytest.raises(ValueError, match="cross-identity public name/alias collision"):
        validate_source_contract(payload)


def test_generated_source_union_is_exact_current_contract_projection() -> None:
    generated = json.loads(render_union_receipt())
    checked_in = json.loads(UNION_RECEIPT_PATH.read_text(encoding="utf-8"))

    assert generated == checked_in
    assert set(generated) == {
        "schema_version",
        "artifact_type",
        "status",
        "contract_sha256",
        "source_counts",
        "identities",
        "limitations",
    }
    assert generated["status"] == "generated_current_contract_projection"
    assert all(set(row) == {"derived_aihot_identity", "slug"} for row in generated["identities"])
    assert "authority_reference" not in generated
    assert "observation_reference" not in render_union_receipt()


def test_source_union_summary_counts_are_recomputed() -> None:
    payload = json.loads(render_union_receipt())
    payload["source_counts"]["total"] += 1

    from airadar.sources.contract import validate_source_union_receipt

    with pytest.raises(ValueError, match="count"):
        validate_source_union_receipt(payload, contract_path=CONTRACT_PATH)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.update(authority_reference={}),
        lambda payload: payload["identities"][0].update(observation_reference={}),
    ],
)
def test_source_union_rejects_removed_authority_fields(mutation) -> None:  # noqa: ANN001
    payload = json.loads(render_union_receipt())
    mutation(payload)

    from airadar.sources.contract import validate_source_union_receipt

    with pytest.raises(ValueError, match="source union"):
        validate_source_union_receipt(payload, contract_path=CONTRACT_PATH)
