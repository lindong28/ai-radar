from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from test_enrich_runner import _db
from test_llm_gateway import completion_payload, install_gateway

from airadar.enrich import category_release as release
from airadar.enrich import runner_v2
from airadar.enrich.classification import classification_projection
from airadar.provider.base_v2 import EnrichResultV2
from airadar.ruleset import enrich_inputs_digest


class JointProvider:
    model_id = "joint-fixture"
    calls = 0

    def enrich(self, item):
        self.calls += 1
        return EnrichResultV2(
            title_zh="联合调用生成的标题", summary_zh="联合调用生成的摘要，保留文章原有信息与具体的研究方法。",
            why_recommend="这篇文章提供了具体的实验材料与技术细节，可以据此了解相关工作的研究方法和使用边界。",
            tags=("模型发布",), primary_category="model", is_opinion=False, raw={"joint": True})


def test_prompt_bytes_match_frozen_research_rubric():
    assert release.RUBRIC == (Path(__file__).parents[1] / "evals/content-enrichment/prompts/category-v2.txt").read_text()


def test_full_material_delivery_without_gold():
    raw = {"title": "title", "content_text": "original", "url": "https://example.com/a"}
    article = {"status": "available", "content_text": "a" * 6000 + "TAIL", "url": raw["url"],
               "linked_articles": [{"status": "available", "content_text": "linked", "url": "https://example.com/b"}]}
    quotes = [{"status": "available", "content_text": "quoted", "url": "https://x.com/a/status/1", "gold": "never expose"},
              {"status": "unavailable", "content_text": "missing"}]
    prompt = release.production_prompt(raw, article, quotes)
    assert all(s in prompt["user"] for s in ("TAIL", "original", "quoted", "linked"))
    assert "never expose" not in prompt["user"] and "missing" not in prompt["user"]
    assert {o["role"] for d in json.loads(prompt["user"].split("\n", 1)[1]) for o in d["origins"]} == {
        "current-item", "article", "linked-article", "quoted-post"}


@pytest.mark.parametrize("failures", [0, 2, 3])
def test_default_runner_stores_category_trace_and_bounded_attempts(monkeypatch, tmp_path, failures):
    conn = _db(tmp_path)
    joint = JointProvider()
    monkeypatch.setattr(runner_v2, "_provider_from_env", lambda: joint)
    monkeypatch.setattr(runner_v2, "prepare_article_context", lambda *a, **k: {"status": "unavailable"})
    monkeypatch.setenv("AI_RADAR_DEEPSEEK_THINKING", "disabled")
    monkeypatch.setenv("AI_RADAR_DEEPSEEK_TIMEOUT", "1")
    monkeypatch.setenv("AI_RADAR_LLM_USAGE_DB", str(tmp_path / "usage.db"))
    counter = 0

    def respond(req):
        nonlocal counter
        counter += 1
        content = '{"primary_category":"paper","reason":"wrong order"}' if counter <= failures else '{"reason":"研究证据支持论文","primary_category":"paper"}'
        payload = completion_payload(req, content=content, provider="ark", model="deepseek-v4-pro-ga-260813")
        payload["choices"][0]["message"]["reasoning_content"] = "provider reasoning"
        return httpx.Response(200, json=payload)

    requests = install_gateway(monkeypatch, respond)
    summary = runner_v2.run_enrich(conn)
    assert summary.processed == 1 and summary.errors == int(failures == 3)
    assert joint.calls == 1 and len(requests) == min(failures + 1, 3)
    for req in requests:
        sent = json.loads(req.content)
        assert sent["model"] == release.CONFIG["default_model"]
        assert sent["thinking"] == {"type": "enabled"}
        assert sent["reasoning_effort"] == "high" and sent["max_tokens"] == 32768
        assert sent["timeout"] == 90 and sent["temperature"] == 0
    row = conn.execute("SELECT * FROM item_evaluations WHERE stage='enrich'").fetchone()
    trace = json.loads(row["input_json"])["category_trace"]
    assert trace["version"] == "0.1.0" and len(trace["attempts"]) == len(requests)
    assert all(a["sent_request_id"] and a["reasoning"] for a in trace["attempts"])
    if failures == 3:
        assert row["error"] and all(a["error"] for a in trace["attempts"])
        assert row["error"].startswith("output rejected:")
        assert runner_v2.run_enrich(conn).processed == 0
    else:
        output = json.loads(row["output_json"])
        assert output["primary_category"] == "paper" and output["is_opinion"] is False
        assert output["title_zh"] == "联合调用生成的标题"
        assert "category_trace" not in output
        assert classification_projection(output).primary_category == "paper"
    conn.close()


def test_injected_provider_does_not_add_call(monkeypatch, tmp_path):
    conn = _db(tmp_path)
    monkeypatch.setattr(runner_v2, "prepare_article_context", lambda *a, **k: {"status": "unavailable"})
    monkeypatch.setattr(release, "chat_json", lambda **k: pytest.fail("unexpected model call"))
    assert runner_v2.run_enrich(conn, provider=JointProvider()).errors == 0
    row = conn.execute("SELECT input_json FROM item_evaluations WHERE stage='enrich'").fetchone()
    assert "category_trace" not in json.loads(row[0])
    conn.close()


def test_gateway_failure_is_retained_with_distinct_attempt_ids(monkeypatch, tmp_path):
    requests = install_gateway(monkeypatch, lambda req: httpx.Response(503, json={"error": {"message": "unavailable"}}))
    with pytest.raises(release.ClassificationFailed) as exc:
        release.classify({"system": "s", "user": "u"}, item_id="failure", db_path=tmp_path / "usage.db")
    attempts = exc.value.trace["attempts"]
    assert len(requests) == len(attempts) == 3
    assert len({a["sent_request_id"] for a in attempts}) == 3
    assert all(a["error_type"] == "GatewayRequestError" for a in attempts)


def test_category_prompt_and_config_move_enrichment_stamp(monkeypatch):
    before = enrich_inputs_digest()
    monkeypatch.setattr(release, "RUBRIC", release.RUBRIC + " changed")
    after = enrich_inputs_digest()
    assert before != after
    monkeypatch.setattr(release, "CONFIG", {**release.CONFIG, "temperature": 0.1})
    assert enrich_inputs_digest() != after


def test_transient_category_failure_recovers_next_round(monkeypatch, tmp_path):
    conn = _db(tmp_path)
    joint = JointProvider()
    monkeypatch.setattr(runner_v2, "_provider_from_env", lambda: joint)
    monkeypatch.setattr(runner_v2, "prepare_article_context", lambda *a, **k: {"status": "unavailable"})
    healthy = False

    def respond(req):
        if not healthy:
            return httpx.Response(503, json={"error": {"message": "unavailable"}})
        return httpx.Response(200, json=completion_payload(
            req, content='{"reason":"research","primary_category":"paper"}',
            provider="ark", model="deepseek-v4-pro-ga-260813"))

    requests = install_gateway(monkeypatch, respond)
    assert runner_v2.run_enrich(conn).errors == 1
    assert len(requests) == 3
    row = conn.execute("SELECT error FROM item_evaluations WHERE stage='enrich'").fetchone()
    assert row[0].startswith("enrich failed:")
    healthy = True
    summary = runner_v2.run_enrich(conn)
    assert summary.processed == 1 and summary.errors == 0
    assert len(requests) == 4 and joint.calls == 2
    conn.close()


def test_parse_failure_preserves_received_response(monkeypatch, tmp_path):
    def respond(req):
        payload = completion_payload(req, content="[1,2]", provider="ark", model="deepseek-v4-pro-ga-260813")
        payload["choices"][0]["message"]["reasoning_content"] = "received reasoning"
        return httpx.Response(200, json=payload)

    requests = install_gateway(monkeypatch, respond)
    with pytest.raises(release.ClassificationFailed) as exc:
        release.classify({"system": "s", "user": "u"}, item_id="bad-json", db_path=tmp_path / "usage.db")
    assert exc.value.output_rejected and len(requests) == 3
    for attempt in exc.value.trace["attempts"]:
        message = attempt["response_body"]["choices"][0]["message"]
        assert message["content"] == "[1,2]" and message["reasoning_content"] == "received reasoning"
        assert attempt["sent_request_id"]
