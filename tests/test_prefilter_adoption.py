"""Selected C11 contract: source prompt, DB consumer and frozen-response replay."""
import hashlib
import json
import os
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from jinja2 import Template

from airadar.db import migrate
from airadar.fetcher.dedup import FetchedItem, upsert_item
from airadar.prefilter import policy, prompts
from airadar.prefilter.runner import run_prefilter
from airadar.provider import deepseek_v32
from airadar.provider.deepseek_chat import ChatJsonResult
from airadar.sources.loader import SourceConfig
from airadar.sources.sync import sync_to_db
from evals._shared import assets, human_labels, inference, metrics

ROOT = Path(__file__).resolve().parents[1]
FROZEN_PROMPT = ROOT / "evals/news-admission/prompts/c11-reason-first.json"


@pytest.mark.parametrize("author,body", [(None, "模型发布"), ("Ada", "x" * 4100)])
def test_default_prompt_is_exact_selected_candidate(author, body):
    raw = dict(case_id="case", title="A model", url="https://example.com/a",
               source_id="sample", tier="T1", author=author,
               published_at="2026-09-17T00:00:00Z", content_text=body)
    candidate = json.loads(FROZEN_PROMPT.read_text())
    rendered = prompts.render_prefilter_prompt(inference._item(raw))
    assert rendered == {"system": candidate["system"],
                        "user": Template(candidate["user_template"]).render(item=inference._item(raw))}
    assert inference._request("prefilter", {}) == {
        "model": "deepseek-v4-flash", "temperature": 0.0, "max_tokens": 500}


@pytest.mark.parametrize("kind,slug,body,refs,expected", [
    ("feed", "buzzing_hn", "99 HN Points", [], False),
    ("feed", "buzzing_hn", "100 HN Points", [], True),
    ("feed", "other", "99 HN Points", [], True),
    ("x", "expert", "AI launch", [{"type": "replied_to", "id": "1"}], False),
    ("x", "expert", "AI launch", [{"type": "quoted", "id": "1"}], True),
    ("web", "blog", "AI title", [], False),
    ("web", "hf_daily_papers", "AI title", [], True),
])
def test_ingestion_to_prefilter_uses_existing_metadata(tmp_path, monkeypatch, kind, slug, body, refs, expected):
    monkeypatch.setenv("ARK_API_KEY", "fixture")
    monkeypatch.delenv("AI_RADAR_FORCE_HEURISTIC", raising=False)
    db_path = tmp_path / "test.db"
    migrate(db_path)
    conn = sqlite3.connect(db_path)
    sync_to_db([SourceConfig(slug=slug, name=slug, url="https://example.com",
                            tier="T1", kind=kind, enabled=True, meta={})], conn)
    fetched = FetchedItem(source_id=slug, url="https://example.com/a", title="AI title",
        author="Ada", published_at="2026-09-17T00:00:00Z", fetched_at="2026-09-17T00:00:00Z",
        content_text=body, extra={"referenced_tweets": refs})
    upsert_item(conn, fetched)
    conn.commit()
    item_id = conn.execute("SELECT id FROM items").fetchone()[0]
    calls = []
    def chat(**kw):
        calls.append(kw)
        return ChatJsonResult(json={"reason": "fixture model reason", "is_ai_related": True,
                                     "confidence": .9}, provider="ark", model="fixture-flash")
    monkeypatch.setattr(deepseek_v32, "chat_json", chat)
    result = run_prefilter(conn, provider=deepseek_v32.DeepSeekV32Prefilter(), item_ids=[item_id])
    assert (result.processed, result.errors) == (1, 0)
    stored, numeric, prompt = conn.execute(
        "SELECT output_json, numeric_json, input_json FROM item_evaluations").fetchone()
    saved = json.loads(stored)
    assert json.loads(numeric)["is_ai_related"] is expected
    assert saved["model_output"]["is_ai_related"] is True
    assert saved["reason"] == "fixture model reason"
    assert bool(saved["admission_policy"]["rejection_reasons"]) is not expected
    assert saved["policy_input"]["extra"]["referenced_tweets"] == refs
    assert calls[0]["system"] == json.loads(prompt)["system"]
    assert calls[0]["user"] == json.loads(prompt)["user"]
    assert len(calls) == 1
    raw = {**vars(fetched), "case_id": item_id, "source_kind": kind, "tier": "T1"}
    replay = inference.predict_one("prefilter", raw, {"chat": lambda **kw: {"json": chat().json}})
    assert replay["output"]["is_ai_related"] is expected
    pure = inference.predict_one("prefilter", raw, {
        "prefilter_policy": False, "chat": lambda **kw: {"json": chat().json}})
    assert pure["output"]["is_ai_related"] is True
    conn.close()


def test_policy_and_model_changes_invalidate_stage_stamp(monkeypatch):
    from airadar.ruleset import prefilter_inputs_digest
    original = prefilter_inputs_digest()
    monkeypatch.setattr(policy, "POLICY", "changed-policy")
    assert prefilter_inputs_digest() != original
    monkeypatch.undo()
    monkeypatch.setenv("AI_RADAR_ARK_PREFILTER_MODEL", "different-model")
    assert prefilter_inputs_digest() != original


def test_policy_switch_has_distinct_evaluation_identity():
    enabled = inference.identity({})
    disabled = inference.identity({"prefilter_policy": False})
    assert enabled != disabled
    assert enabled["prefilter_policy"] is True
    assert disabled["prefilter_policy"] is False
    assert "src/airadar/prefilter/policy.py" in enabled["source_sha256"]


@pytest.mark.parametrize("latest_has_date", [False, True])
def test_refetched_web_uses_observed_facts_without_changing_publication(tmp_path, monkeypatch, latest_has_date):
    monkeypatch.setenv("ARK_API_KEY", "fixture")
    monkeypatch.delenv("AI_RADAR_FORCE_HEURISTIC", raising=False)
    db_path = tmp_path / "refetch.db"
    migrate(db_path)
    conn = sqlite3.connect(db_path)
    sync_to_db([SourceConfig(slug="blog", name="blog", url="https://example.com",
                            tier="T1", kind="web", enabled=True, meta={})], conn)
    first = FetchedItem(source_id="blog", url="https://example.com/a", title="AI launch",
        author=None, published_at="2026-09-17T00:00:00Z", fetched_at="2026-09-17T00:00:00Z",
        content_text="AI launch")
    upsert_item(conn, first)
    latest = replace(first, fetched_at="2026-09-17T00:30:00Z",
                     published_at="2026-09-16T20:00:00Z" if latest_has_date else "2026-09-17T00:30:00Z")
    upsert_item(conn, latest)
    conn.commit()
    item_id, published, fetched = conn.execute("SELECT id,published_at,fetched_at FROM items").fetchone()
    assert published == first.published_at
    assert fetched == latest.fetched_at
    monkeypatch.setattr(deepseek_v32, "chat_json", lambda **kw: ChatJsonResult(
        json={"reason": "AI launch", "is_ai_related": True, "confidence": .9},
        provider="fixture", model="fixture-flash"))
    result = run_prefilter(conn, provider=deepseek_v32.DeepSeekV32Prefilter(), item_ids=[item_id])
    assert (result.processed, result.errors) == (1, 0)
    saved = json.loads(conn.execute("SELECT output_json FROM item_evaluations").fetchone()[0])
    assert saved["is_ai_related"] is latest_has_date
    assert saved["admission_policy"]["rejection_reasons"] == policy.rejection_reasons(
        {**vars(latest), "source_kind": "web"})
    conn.close()


def test_rule_match_does_not_turn_failed_model_into_negative():
    raw = dict(case_id="case", source_id="buzzing_hn", title="AI launch",
               content_text="99 HN Points", url="https://example.org/a", tier="T2",
               published_at="2026-09-17T00:00:00Z")
    result = inference.predict_one("prefilter", raw, {"chat": lambda **kw: {"json": {}}})
    assert result["status"] == "error"
    assert result["output"] is None


def test_replay_selected_frozen_run_through_db_consumer(tmp_path, monkeypatch):
    source = os.environ.get("PREFILTER_ADOPTION_SOURCE_RUN")
    if not source:
        pytest.skip("set PREFILTER_ADOPTION_SOURCE_RUN for the retained 300-case replay")
    source = Path(source)
    cases = assets.read_jsonl(source / "cases.jsonl")
    predictions = {r["case_id"]: r for r in assets.read_jsonl(source / "predictions.jsonl")}
    reference_policy = {r["case_id"]: r for r in assets.read_jsonl(source / "policy-predictions.jsonl")}
    started = assets.read_json(source / "started.json")
    assert assets.digest(cases) == started["case_identity"]
    assert started["object_identity"]["prompt_override"] == assets.read_json(FROZEN_PROMPT)
    assert len(cases) == 300
    db_path = tmp_path / "replay.db"
    migrate(db_path)
    conn = sqlite3.connect(db_path)
    for case in cases:
        raw, key = case["input"], case["case_id"]
        conn.execute("INSERT OR IGNORE INTO sources(id,name,url,tier,kind,synced_at) VALUES(?,?,?,?,?,?)",
            (raw["source_id"], raw["source_id"], raw["url"], raw["tier"], raw["source_kind"], raw["fetched_at"]))
        conn.execute("""INSERT INTO items(id,source_id,url,title,author,published_at,fetched_at,
                     content_text,content_hash,extra_json) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (key, raw["source_id"], raw["url"], raw["title"], raw.get("author"), raw["published_at"],
             raw["fetched_at"], raw["content_text"], key, json.dumps(raw.get("extra") or {})))
    conn.commit()
    by_id = {c["case_id"]: c for c in cases}
    calls = []
    def chat(**kw):
        key = kw["item_id"]
        attempt_id = predictions[key]["stage_results"]["prefilter"]["attempt_id"]
        attempt = assets.read_json(source / "attempts" / f"{attempt_id}.json")
        # Compare against the actual archived request, not only a template copy.
        messages = [{"role": "system", "content": kw["system"]},
                    {"role": "user", "content": kw["user"]}]
        assert hashlib.sha256(json.dumps(messages, ensure_ascii=False, sort_keys=True).encode()).hexdigest() == attempt["prompt_sha256"]
        assert kw["default_model"] == started["object_identity"]["request"]["model"]
        assert kw["temperature"] == started["object_identity"]["request"]["temperature"]
        assert kw["max_tokens"] == started["object_identity"]["request"]["max_tokens"]
        calls.append(key)
        raw_response = predictions[key]["stage_results"]["prefilter"]["raw"]
        return ChatJsonResult(json=json.loads(raw_response["choices"][0]["message"]["content"]),
                               provider="frozen-replay", model=raw_response["model"])
    monkeypatch.setenv("ARK_API_KEY", "fixture")
    monkeypatch.delenv("AI_RADAR_FORCE_HEURISTIC", raising=False)
    monkeypatch.setattr(deepseek_v32, "chat_json", chat)
    result = run_prefilter(conn, provider=deepseek_v32.DeepSeekV32Prefilter(), item_ids=list(by_id))
    assert (result.processed, result.errors) == (300, 0)
    replay = []
    for key, numeric in conn.execute("SELECT item_id,numeric_json FROM item_evaluations"):
        member = json.loads(numeric)["is_ai_related"]
        assert member is reference_policy[key]["output"]["member"]
        replay.append({"case_id": key, "status": "ok", "output": {"member": member}})
    assert len(calls) == 300
    reviews = Path(os.environ["PREFILTER_ADOPTION_REVIEWS"])
    effective, coverage = human_labels.apply_labels(cases, human_labels.load_annotations(reviews), "news-admission")
    score = metrics.score("O1", effective, replay)
    assert score["metrics"]["precision"]["value"] == pytest.approx(134 / 145)
    assert score["metrics"]["recall"]["value"] == pytest.approx(134 / 137)
    print(json.dumps({"cases": len(cases), "sources": len({c["input"]["source_id"] for c in cases}),
        "source_kinds": sorted({c["input"]["source_kind"] for c in cases}), "human": coverage,
        "metrics": score["metrics"], "new_model_calls": 0}, ensure_ascii=False))
    conn.close()
