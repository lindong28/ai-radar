"""Reason-first means the returned explanation, not a synthesized post-hoc one."""
import json
from types import SimpleNamespace

import pytest

from airadar.provider import deepseek_v32
from airadar.provider.base import ProviderItem
from airadar.provider.deepseek_chat import _parse_json_object
from airadar.provider.judgment import require_reason_first
from airadar.prefilter.runner import _evaluate_item
from evals._shared.inference import predict_one
from evals._shared.judge import DEFAULT_MODEL, judge_text


@pytest.mark.parametrize("decision,value", [("is_ai_related", True), ("is_ai_related", False), ("score", 0), ("score", 2)])
def test_real_parser_preserves_order_and_rejects_reverse(decision, value):
    valid = json.dumps({"reason": "specific evidence", decision: value})
    assert require_reason_first(_parse_json_object(valid), decision) == "specific evidence"
    reverse = json.dumps({decision: value, "reason": "specific evidence"})
    with pytest.raises(ValueError, match="before"):
        require_reason_first(_parse_json_object(reverse), decision)


@pytest.mark.parametrize("reason", [None, "", "  ", 1])
def test_missing_or_empty_reason_is_not_success(reason):
    with pytest.raises(ValueError, match="nonempty"):
        require_reason_first({"reason": reason, "is_ai_related": True}, "is_ai_related")


@pytest.mark.parametrize("answer", [True, False])
def test_production_and_eval_preserve_same_reason(monkeypatch, answer):
    monkeypatch.setenv("ARK_API_KEY", "fixture")
    monkeypatch.delenv("AI_RADAR_FORCE_HEURISTIC", raising=False)
    payload = {"reason": "有具体实验" if answer else "仅泛泛推荐没有说明依据", "is_ai_related": answer, "confidence": .9}
    monkeypatch.setattr(deepseek_v32, "chat_json", lambda **kw: SimpleNamespace(json=payload, provider="ark", model="fixture"))
    item = ProviderItem("case", "title", "https://example.com", "source", "T2", None, "2026-09-20T00:00:00Z", "body")
    numeric, output, error, _ = _evaluate_item(deepseek_v32.DeepSeekV32Prefilter(), item)
    assert error is None and numeric.is_ai_related is answer
    assert output["reason"] == payload["reason"] == output["raw"]["json"]["reason"]
    result = predict_one("prefilter", vars(item), {"chat": lambda **kw: {"json": payload}})
    assert result["output"]["reason"] == payload["reason"]


def test_judge_rejects_decision_before_reason_and_retains_response():
    wire = {"score": 2, "reason": "late explanation"}
    response = {"json": wire, "raw": json.dumps(wire), "model": DEFAULT_MODEL, "provider": "deepseek"}
    result = judge_text("a", "title", {}, "a", "a", chat=lambda **kw: response)
    assert result["status"] == "error" and result["score"] is None
    assert result["call"]["raw"] == response["raw"]
    response["json"] = {"reason": "actual explanation", "score": 2}
    result = judge_text("a", "title", {}, "a", "a", chat=lambda **kw: response)
    assert result["status"] == "ok" and result["reason"] == "actual explanation"
