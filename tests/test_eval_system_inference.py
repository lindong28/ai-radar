from __future__ import annotations

import importlib.util
import json
import sqlite3
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest

from airadar.curator import select as production_select
from airadar.web.routes.timeline import _PREFILTER_SCORING_CLAUSE

_PATH = Path(__file__).resolve().parents[1] / "evals/_shared/inference.py"
_SPEC = importlib.util.spec_from_file_location("eval_system_inference", _PATH)
inference = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(inference)


def raw(item_id="a", **changes):
    return {
        "case_id": f"case-{item_id}", "item_id": item_id, "title": f"New model {item_id}",
        "url": f"https://example.com/{item_id}", "source_id": f"source-{item_id}",
        "tier": "T1", "author": None, "content_text": f"New model {item_id} supports tool use.",
        "published_at": "2026-09-17T01:00:00Z", "fetched_at": "2026-09-17T02:00:00Z",
        "content_hash": f"hash-{item_id}", "source_enabled": True, "source_kind": "feed",
        **changes,
    }


def score(value=8, relevance=8):
    return {**dict.fromkeys(("density", "recency", "authority", "engineering", "significance"), value),
            "relevance": relevance, "reasoning": "评分阶段判断该模型明确降低了推理部署成本。", "topics": []}


def enrichment():
    return {"title_zh": "新模型支持工具调用", "summary_zh": "新模型提供工具调用能力，团队公开了实际部署方式与详细性能测试结果。",
            "why_recommend": "这次发布公开了工具调用接口的具体实现和部署测试结果，为模型工程团队评估接入成本提供了可复现的依据。",
            "primary_category": "model", "is_opinion": False, "tags": ["模型发布"]}


def stage(output, status="ok"):
    return {"status": status, "output": output, "raw": output, "usage": None, "model": "fixture"}


def row(item_id="a", value=8, relevance=8, **raw_changes):
    return {"input": raw(item_id, **raw_changes), "stage_results": {
        "prefilter": stage({"is_ai_related": True, "confidence": 0.9}),
        "score": stage(score(value, relevance)), "enrich": stage(enrichment()),
    }}


def config(**changes):
    return {"now": "2026-09-17T03:00:00Z", "pool_complete": True, "archive_initial": [],
            "selection": {"source_quota": "off", "freshness_quota": 0}, **changes}


def test_stage_aliases_use_production_prompts_and_preserve_transport_evidence():
    calls = []

    def chat(**kwargs):
        calls.append(kwargs)
        payload = {"prefilter": {"reason": "fixture evidence", "is_ai_related": True, "confidence": 0.9},
                   "score": score(), "enrich": enrichment()}[kwargs["stage"]]
        return {"json": payload, "model": "served-model", "provider": "fake", "attempt_id": "attempt-test",
                "requested_model": "requested-model",
                "usage": {"prompt_tokens": 12}, "raw": "raw-completion"}

    for name in ("prefilter", "score", "enrich"):
        result = inference.predict_one(name, raw(reference="DO_NOT_SEND", case_id="PRIVATE_CASE"), {"chat": chat})
        assert result["status"] == "ok"
        saved = result["stage_results"][name]
        assert saved["model"] == "served-model"
        assert saved["attempt_id"] == "attempt-test" and saved["requested_model"] == "requested-model"
        assert saved["raw"] == "raw-completion"
        assert saved["usage"] == {"prompt_tokens": 12}
    assert [call["stage"] for call in calls] == ["prefilter", "score", "enrich"]
    assert calls[0]["request"]["max_tokens"] == 500  # Short reason plus decision, shared with production.
    assert calls[1]["request"]["max_tokens"] == 600
    assert "max_tokens" not in calls[2]["request"]
    assert all("DO_NOT_SEND" not in json.dumps(call) and "PRIVATE_CASE" not in json.dumps(call) for call in calls)


def test_failed_transport_or_validation_is_not_retried_or_filled_with_success():
    calls = []

    def chat(**kwargs):
        calls.append(kwargs)
        raise TimeoutError("sensitive endpoint details")

    result = inference.predict_one("news-admission", raw(), {"chat": chat})
    assert result["status"] == "error" and result["output"] is None
    assert len(calls) == 1
    assert "sensitive" not in json.dumps(result)
    invalid = inference.predict_one("enrich", raw(), {"chat": lambda **_: {"json": {"tags": []}}})
    assert invalid["status"] == "error"
    assert invalid["stage_results"]["enrich"]["raw"] == {"tags": []}


def test_prefilter_false_short_circuits_scoring_but_positive_calls_it():
    calls = []

    def chat(**kwargs):
        calls.append(kwargs["stage"])
        return {"json": {"reason": "unrelated fixture", "is_ai_related": False, "confidence": 1}}

    assert inference.predict_one("news-admission", raw(), {"chat": chat})["status"] == "pending_pool"
    assert calls == ["prefilter"]
    calls.clear()

    def yes_chat(**kwargs):
        calls.append(kwargs["stage"])
        return {"json": {"reason": "AI fixture", "is_ai_related": True, "confidence": 1} if kwargs["stage"] == "prefilter" else score()}

    assert inference.predict_one("news-admission", raw(), {"chat": yes_chat})["status"] == "pending_pool"
    assert calls == ["prefilter", "score"]


def test_rank_projection_changes_final_score_and_selected_reason_only():
    rows = [row("a", 9), row("b", 8), row("c", 2)]
    projected = inference.project_pool(rows, config())
    a, b, c = [entry["output"] for entry in projected]
    # Production calibration endpoints, not a 5D average disguised as display score.
    assert [a["score"], b["score"]] == [92, 62]
    assert c["score"] == 20
    assert a["reason"] == score()["reasoning"]
    assert c["reason"] == enrichment()["why_recommend"]
    assert [a["featured"], b["featured"], c["featured"]] == [True, True, False]
    assert all(entry["case_id"].startswith("case-") for entry in projected)


def test_single_selection_keeps_raw_score_and_js_rounding():
    result = inference.project_pool([row(value=7.25)], config())[0]
    assert result["output"]["score"] == 73  # JS Math.round, not Python bankers' rounding.


@pytest.mark.parametrize("relevance,expected", [(6.49, False), (6.5, True), (9, True)])
def test_admission_matches_production_sql_at_relevance_boundary(relevance, expected):
    with sqlite3.connect(":memory:") as conn:
        conn.executescript("CREATE TABLE items (id TEXT); CREATE TABLE item_evaluations (id INT, item_id TEXT, stage TEXT, error TEXT, numeric_json TEXT);")
        conn.execute("INSERT INTO items VALUES ('a')")
        conn.executemany("INSERT INTO item_evaluations VALUES (?, 'a', ?, NULL, ?)", [
            (1, "prefilter", json.dumps({"is_ai_related": True})),
            (2, "scoring", json.dumps(score(relevance=relevance))),
        ])
        production = bool(conn.execute(f"SELECT 1 FROM items i WHERE {_PREFILTER_SCORING_CLAUSE}").fetchone())
    result = inference.project_pool([row(relevance=relevance)], config())[0]["output"]["member"]
    assert result is production is expected


def test_source_and_latest_same_source_url_gates_are_in_pool_projection():
    rows = [row("a"), row("b", source_enabled=False), row("c", source_kind="wechat"),
            row("d", source_id="source-a", url="https://example.com/a/", fetched_at="2026-09-17T02:01:00Z")]
    results = inference.project_pool(rows, config())
    assert [entry["output"]["member"] for entry in results] == [False, False, False, True]


def test_pool_failure_keeps_admission_but_invalidates_rank_dependent_results():
    rows = [row("a"), row("b")]
    rows[1]["stage_results"]["enrich"] = stage(None, "error")
    result = inference.project_pool(rows, config())
    assert all(entry["target_status"]["news-admission"] == "ok" for entry in result)
    assert all(entry["target_status"]["visible-score"] == "error" for entry in result)
    assert all(entry["output"]["featured"] is None for entry in result)


def test_fixed_scores_can_be_reselected_without_chat_or_reference_counts():
    rows = [row("a", 8), row("b", 7)]
    snapshot = deepcopy(rows)
    low = inference.project_pool(rows, config())
    high = inference.project_pool(rows, config(selection={"threshold": 7.5, "freshness_quota": 0, "source_quota": "off"}))
    assert sum(entry["output"]["featured"] for entry in low) == 2
    assert sum(entry["output"]["featured"] for entry in high) == 1
    assert rows == snapshot
    archive = inference.project_pool(rows, config(archive_initial=["b"], selection={"threshold": 9, "freshness_quota": 0}))
    assert [entry["output"]["featured"] for entry in archive] == [False, True]


def test_projection_requires_explicit_complete_pool_time_and_initial_state():
    for missing in ("now", "pool_complete", "archive_initial"):
        settings = config()
        del settings[missing]
        with pytest.raises((KeyError, ValueError)):
            inference.project_pool([row()], settings)


def test_pool_selection_matches_production_curate_with_frozen_clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 17, 3, tzinfo=UTC)

    monkeypatch.setattr(production_select, "datetime", Clock)
    rows = [row("a", 8), row("b", 7), row("c", 5, published_at="2026-09-16T10:00:00Z")]
    with sqlite3.connect(":memory:") as conn:
        conn.executescript("""
        CREATE TABLE sources (id TEXT, tier TEXT, kind TEXT, enabled INT);
        CREATE TABLE items (id TEXT, content_hash TEXT, url TEXT, published_at TEXT, source_id TEXT);
        CREATE TABLE item_evaluations (id INT, item_id TEXT, stage TEXT, error TEXT, numeric_json TEXT, output_json TEXT);
        CREATE TABLE curation_runs (id TEXT, ruleset_version TEXT, weights_json TEXT, threshold REAL, input_eval_ids TEXT, output_curated_ids TEXT, created_at TEXT, shadow_json TEXT);
        CREATE TABLE curated_items (run_id TEXT, item_id TEXT, weighted_score REAL, rank INT, reason_json TEXT);
        """)
        for index, entry in enumerate(rows):
            item = entry["input"]
            conn.execute("INSERT INTO sources VALUES (?, ?, ?, 1)", (item["source_id"], item["tier"], item["source_kind"]))
            conn.execute("INSERT INTO items VALUES (?, ?, ?, ?, ?)", tuple(item[key] for key in ("item_id", "content_hash", "url", "published_at", "source_id")))
            for offset, name in enumerate(("score", "enrich")):
                payload = json.dumps(entry["stage_results"][name]["output"])
                conn.execute("INSERT INTO item_evaluations VALUES (?, ?, ?, NULL, ?, ?)",
                             (index * 2 + offset, item["item_id"], "scoring" if name == "score" else name, payload, payload))
        run = production_select.curate(conn, freshness_quota=2, limit=3, source_quota=None)
        scores = dict(conn.execute("SELECT item_id, weighted_score FROM curated_items"))
    settings = config(selection={"freshness_quota": 2, "limit": 3, "source_quota": "off"})
    actual = inference.project_pool(rows, settings)
    assert {entry["item_id"] for entry in actual if entry["output"]["featured"]} == set(run.output_curated_ids)
    assert {entry["item_id"]: entry["output"]["score"] for entry in actual if entry["output"]["featured"]} == {key: round(value * 10) for key, value in scores.items()}


def test_identity_omits_secrets_and_records_sources_and_model_config():
    result = inference.identity(config(chat=object(), api_key="NEVER_SERIALIZE", models={"score": "pinned-model"}))
    assert "NEVER_SERIALIZE" not in json.dumps(result)
    assert result["requests"]["score"]["model"] == "pinned-model"
    assert result["source_sha256"]["web/static/app.js"]


def test_model_selector_is_respected_and_unsupported_provider_is_not_substituted(monkeypatch):
    monkeypatch.setenv("AI_RADAR_SCORER", "deepseek_v4_pro")
    monkeypatch.delenv("AI_RADAR_DEEPSEEK_SCORER_MODEL", raising=False)
    assert inference.identity(config())["requests"]["score"]["model"] == "deepseek-v4-pro"
    monkeypatch.setenv("AI_RADAR_SCORER", "codex_gpt_mini")
    calls = []
    result = inference.predict_one("score", raw(), {"chat": lambda **kwargs: calls.append(kwargs)})
    assert result["status"] == "error" and not calls


def test_source_quota_and_category_order_change_visible_selection():
    paper = row("a", 8)
    paper["stage_results"]["enrich"]["output"]["primary_category"] = "paper"
    product = row("b", 7.8, source_id="source-a")
    # Existing production paper multiplier changes who gets the sole slot.
    settings = config(selection={"limit": 1, "freshness_quota": 0, "source_quota": "off"})
    result = inference.project_pool([paper, product], settings)
    assert [entry["output"]["featured"] for entry in result] == [False, True]
    # The normal per-source quota uses the configured limit, not reference count.
    quota = config(selection={"limit": 2, "freshness_quota": 0, "source_quota": "source=0.5"})
    result = inference.project_pool([paper, product], quota)
    assert sum(entry["output"]["featured"] for entry in result) == 1


def test_independent_score_for_rejected_item_does_not_enter_production_pool():
    rows = [row("a", 8), row("b", 10)]
    rows[1]["stage_results"]["prefilter"] = stage({"is_ai_related": False, "confidence": 1})
    del rows[1]["stage_results"]["enrich"]
    result = inference.project_pool(rows, config())
    assert result[0]["output"]["score"] == 80  # One production candidate, so no rank calibration.
    assert result[0]["target_status"]["visible-score"] == "ok"
    assert result[1]["output"]["score"] == 100  # Independent O2 prediction remains available.
    assert result[1]["output"]["featured"] is False
    assert result[1]["output"]["member"] is False
    assert not result[0]["pool_error_item_ids"]
