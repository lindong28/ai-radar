"""Gateway owns provider routing; legacy caller keys never select a fallback."""

from __future__ import annotations

import json

import httpx
import pytest
from test_llm_gateway import chat, completion_payload, install_gateway

from airadar.provider.base import ProviderItem
from airadar.provider.deepseek_v4_flash import DeepSeekV4FlashScorer
from airadar.provider.deepseek_v4_flash_v2 import DeepSeekV4FlashScorer as FlashV2
from airadar.provider.deepseek_v4_pro import DeepSeekV4ProScorer
from airadar.provider.deepseek_v4_pro_v2 import DeepSeekV4ProScorer as ProV2
from airadar.provider.deepseek_v32 import DeepSeekV32Prefilter
from airadar.scorer import runner


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    for name in ("ARK_API_KEY", "DEEPSEEK_API_KEY", "AI_RADAR_FORCE_HEURISTIC", "AI_RADAR_DEEPSEEK_SCORER_MODEL",
                 "AI_RADAR_DEEPSEEK_PREFILTER_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AI_RADAR_DB", str(tmp_path / "radar.db"))
    monkeypatch.setenv("AI_RADAR_LLM_USAGE_DB", str(tmp_path / "usage.db"))


@pytest.mark.parametrize("provider", ["ark", "deepseek"])
def test_gateway_response_decides_provider_even_when_both_legacy_keys_exist(monkeypatch, provider):
    monkeypatch.setenv("ARK_API_KEY", "legacy-ark-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "legacy-deepseek-key")
    requests = install_gateway(monkeypatch, lambda req: httpx.Response(200, json=completion_payload(req, provider=provider)))
    result = chat()
    assert result.provider == provider
    assert len(requests) == 1
    assert requests[0].url.host == "127.0.0.1"


@pytest.mark.parametrize("scorer", [DeepSeekV4FlashScorer, DeepSeekV4ProScorer, FlashV2, ProV2])
def test_scorers_call_gateway_without_provider_keys(monkeypatch, scorer):
    payload = dict(relevance=8, density=7, recency=6, authority=5, engineering=9, reasoning="useful", topics=[])
    requests = install_gateway(monkeypatch, lambda req: httpx.Response(200, json=completion_payload(req, content=json.dumps(payload))))
    item = ProviderItem("item", "AI tooling", "https://example.test", "example", "T1", None, None, "Details")
    result = scorer().score_5d(item)
    assert len(requests) == 1
    assert json.loads(requests[0].content)["model"] == scorer.model_id
    assert result.raw["sent_request_id"] == requests[0].headers["X-LLM-Request-ID"]


def test_prefilter_calls_gateway_without_provider_keys(monkeypatch):
    requests = install_gateway(monkeypatch, lambda req: httpx.Response(200, json=completion_payload(
        req, content='{"reason": "AI benchmark", "is_ai_related": true, "confidence": 0.9}')))
    item = ProviderItem("item", "AI tooling", "https://example.test", "example", "T1", None, None, "Details")
    assert DeepSeekV32Prefilter().is_ai_related(item).is_ai_related
    assert len(requests) == 1


def test_explicit_heuristic_skips_gateway(monkeypatch):
    monkeypatch.setenv("AI_RADAR_FORCE_HEURISTIC", "1")
    requests = install_gateway(monkeypatch, lambda req: pytest.fail("heuristic mode must not dispatch"))
    item = ProviderItem("item", "AI tooling", "https://example.test", "example", "T1", None, "2026-09-22T00:00:00Z", "Details")
    DeepSeekV4FlashScorer().score_5d(item)
    DeepSeekV32Prefilter().is_ai_related(item)
    assert requests == []
    assert "explicitly enabled" in DeepSeekV4FlashScorer().smoke_test()


def test_smoke_does_not_claim_inference_ready(monkeypatch):
    install_gateway(monkeypatch, lambda req: pytest.fail("smoke status must not make a paid call"))
    assert "not checked" in DeepSeekV4FlashScorer().smoke_test()


def test_flash_scorer_is_default(monkeypatch):
    monkeypatch.delenv("AI_RADAR_SCORER", raising=False)
    assert DeepSeekV4FlashScorer.model_id == "deepseek-v4-flash"
    assert isinstance(runner._provider_from_env(), DeepSeekV4FlashScorer)
