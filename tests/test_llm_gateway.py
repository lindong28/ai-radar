from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import httpx
import pytest
from openai import OpenAI as SDKOpenAI

from airadar.provider import codex_gpt_mini, deepseek_chat, llm_gateway
from airadar.provider.base import ProviderItem
from airadar.provider.llm_gateway import GatewayRequestError


def completion_payload(request: httpx.Request, *, content: str = '{"ok": true}', provider: str = "ark",
                       model: str = "native-flash", usage: dict | None = None) -> dict[str, Any]:
    return {
        "id": "completion-test", "object": "chat.completion", "created": 0, "model": model,
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
        "usage": usage,
        "llm_gateway": {
            "projection_version": 1, "logical_request_id": request.headers["X-LLM-Request-ID"],
            "attempt_id": "attempt-test", "requested_logical_model": json.loads(request.content)["model"],
            "requested_route_id": None, "route_selection_source": "policy", "selected_route_id": "candidate-test",
            "actual_model": model, "provider_id": provider, "credential_profile_id": "personal-test",
            "file_revision": "registry-test", "loaded_revision": "registry-test",
        },
    }


def install_gateway(monkeypatch: pytest.MonkeyPatch, handler) -> list[httpx.Request]:
    requests: list[httpx.Request] = []

    def wrapped(request):
        requests.append(request)
        return handler(request)

    def factory(**kwargs):
        # Keep SDK serialization/status handling; replace only outbound I/O.
        kwargs["http_client"].close()
        kwargs["http_client"] = httpx.Client(transport=httpx.MockTransport(wrapped), trust_env=False)
        return SDKOpenAI(**kwargs)

    monkeypatch.setattr(llm_gateway, "OpenAI", factory)
    monkeypatch.setenv("AI_RADAR_LLM_GATEWAY_BASE_URL", llm_gateway.DEFAULT_BASE_URL)
    monkeypatch.setenv("AI_RADAR_LLM_GATEWAY_PROJECT", "ai-radar")
    return requests


def chat(**overrides):
    kwargs = dict(system="Return JSON only.", user='Return {"ok": true}.', default_model="deepseek-v4-flash",
                  model_env="AI_RADAR_DEEPSEEK_PREFILTER_MODEL", ark_model_env="AI_RADAR_ARK_PREFILTER_MODEL",
                  temperature=0.0, max_tokens=20)
    return deepseek_chat.chat_json(**(kwargs | overrides))


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    for key in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY", "ARK_API_KEY", "AI_RADAR_FORCE_HEURISTIC",
                "AI_RADAR_LLM_GATEWAY_SESSION", "AI_RADAR_DEEPSEEK_PREFILTER_MODEL", "AI_RADAR_OPENAI_SCORER_MODEL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AI_RADAR_DB", str(tmp_path / "radar.db"))
    monkeypatch.setenv("AI_RADAR_LLM_USAGE_DB", str(tmp_path / "usage.db"))


def test_sdk_sends_gateway_headers_placeholder_key_and_no_provider_route(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "must-not-be-forwarded")
    monkeypatch.setenv("OPENAI_API_KEY", "also-must-not-be-forwarded")
    monkeypatch.setenv("AI_RADAR_ARK_PREFILTER_MODEL", "provider-specific-model-must-not-be-selected")
    monkeypatch.setenv("AI_RADAR_DEEPSEEK_THINKING", "disabled")
    requests = install_gateway(monkeypatch, lambda req: httpx.Response(200, json=completion_payload(req)))
    result = chat()
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == "http://127.0.0.1:39011/v1/chat/completions"
    assert request.headers["X-LLM-Project"] == "ai-radar"
    assert request.headers["Authorization"] == "Bearer llm-gateway-local-placeholder"
    assert str(UUID(request.headers["X-LLM-Request-ID"])) == result.sent_request_id
    assert "X-LLM-Route" not in request.headers
    body = json.loads(request.content)
    assert body["model"] == "deepseek-v4-flash"
    assert body["thinking"] == {"type": "disabled"}
    assert body["timeout"] == 90
    assert "response_format" not in body
    assert result.json == {"ok": True}
    assert result.provider == "ark"
    assert result.model == "native-flash"
    assert result.gateway["logical_request_id"] == result.sent_request_id


@pytest.mark.parametrize("url", ["https://api.openai.com/v1", "http://10.0.0.1/v1", "http://127.0.0.1.evil.test/v1",
                                "http://user:secret@localhost/v1", "http://localhost/v1?key=x",
                                "http://localhost/v1#part", "file:///v1", "http://localhost/wrong"])
def test_gateway_rejects_nonlocal_or_credential_urls(url):
    with pytest.raises(ValueError):
        llm_gateway.gateway_base_url(url)


@pytest.mark.parametrize("url", ["http://127.0.0.1:39011/v1/", "https://localhost/v1", "http://[::1]:39011/v1"])
def test_gateway_accepts_loopback_urls(url):
    assert llm_gateway.gateway_base_url(url) == url.rstrip("/")


@pytest.mark.parametrize("requested,override", [
    ("deepseek-v4-flash", "deepseek-v4-pro"),
    ("deepseek-v4-pro", "deepseek-v4-flash"),
])
def test_legacy_judge_explicit_model_overrides_environment(monkeypatch, requested, override):
    from airadar.eval.aihot_fit.judge import judge_once

    monkeypatch.setenv("AI_RADAR_FIT_JUDGE_MODEL", override)
    monkeypatch.setenv("AI_RADAR_ARK_FIT_JUDGE_MODEL", override)
    requests = install_gateway(monkeypatch, lambda req: httpx.Response(
        200, json=completion_payload(req, content='{"rationale":"same content","closeness":90}')
    ))
    result = judge_once(model=requested, dimension="summary", title="Title", content="Body",
                        reference="Reference", candidate="Candidate")
    assert len(requests) == 1
    assert json.loads(requests[0].content)["model"] == requested
    assert result["raw"]["llm_gateway"]["requested_logical_model"] == requested


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_gateway_rejects_invalid_timeout_before_client_creation(timeout):
    with pytest.raises(ValueError, match="positive and finite"):
        llm_gateway.gateway_client(callsite_id="test", timeout=timeout,
                                   client_factory=lambda **_: pytest.fail("must not create client"))


@pytest.mark.parametrize("request_id", [None, ""])
def test_identity_rejects_absent_sent_id_even_if_response_matches(request_id):
    with pytest.raises(GatewayRequestError, match="missing_sent_request_id"):
        llm_gateway.gateway_identity({"llm_gateway": {"projection_version": 1, "logical_request_id": request_id}}, request_id)


def test_client_does_not_inherit_proxy_or_sdk_retry_defaults(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.test:8888")
    with llm_gateway.gateway_client(callsite_id="test", base_url=llm_gateway.DEFAULT_BASE_URL) as client:
        assert client.max_retries == 0
        assert client._client._trust_env is False
        assert client._client.follow_redirects is False


@pytest.mark.parametrize("session", ["codex:12345678-1234-1234-1234-123456789abc", "claude:12345678-1234-1234-1234-123456789abc"])
def test_valid_session_header(monkeypatch, session):
    monkeypatch.setenv("AI_RADAR_LLM_GATEWAY_SESSION", session)
    assert llm_gateway.gateway_headers("request-id")["X-LLM-Session"] == session


@pytest.mark.parametrize("session", ["invalid", "codex:12345678-1234-1234-1234-123456789ABC", "other:12345678-1234-1234-1234-123456789abc"])
def test_invalid_session_is_not_sent(monkeypatch, session):
    monkeypatch.setenv("AI_RADAR_LLM_GATEWAY_SESSION", session)
    assert "X-LLM-Session" not in llm_gateway.gateway_headers("request-id")


@pytest.mark.parametrize("mutation", ["missing", "version", "boolean_version", "request_id", "model"])
def test_companion_errors_keep_sent_id_and_do_not_retry(monkeypatch, mutation):
    def handler(request):
        body = completion_payload(request)
        if mutation == "missing":
            body.pop("llm_gateway")
        elif mutation == "version":
            body["llm_gateway"]["projection_version"] = 2
        elif mutation == "boolean_version":
            body["llm_gateway"]["projection_version"] = True
        elif mutation == "request_id":
            body["llm_gateway"]["logical_request_id"] = "other-request"
        else:
            body["llm_gateway"]["requested_logical_model"] = "other-model"
        return httpx.Response(200, json=body)

    requests = install_gateway(monkeypatch, handler)
    with pytest.raises(GatewayRequestError) as caught:
        chat()
    assert len(requests) == 1
    assert caught.value.sent_request_id == requests[0].headers["X-LLM-Request-ID"]
    assert caught.value.body is not None


@pytest.mark.parametrize("failure", [429, 500, "timeout", "invalid_content"])
def test_transport_and_parse_failures_are_single_send(monkeypatch, failure):
    def handler(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("fixture timeout", request=request)
        if failure == "invalid_content":
            return httpx.Response(200, json=completion_payload(request, content="[]"))
        return httpx.Response(failure, json={"error": {"code": "fixture_rejection", "action": "inspect fixture ledger"}})

    requests = install_gateway(monkeypatch, handler)
    with pytest.raises(GatewayRequestError) as caught:
        chat()
    assert len(requests) == 1
    assert caught.value.sent_request_id == requests[0].headers["X-LLM-Request-ID"]
    if isinstance(failure, int):
        assert caught.value.code == "fixture_rejection"
        assert caught.value.action == "inspect fixture ledger"
        assert caught.value.body["error"]["code"] == "fixture_rejection"


def test_native_error_identity_headers_survive(monkeypatch):
    def handler(req):
        return httpx.Response(429, json={"error": {"code": "rate_limit"}}, headers={
            "X-LLM-Gateway-Logical-Request-ID": req.headers["X-LLM-Request-ID"],
            "X-LLM-Gateway-Selected-Route-ID": "personal%2Fmodel%2Fstream",
        })
    requests = install_gateway(monkeypatch, handler)
    with pytest.raises(GatewayRequestError) as caught:
        chat()
    assert len(requests) == 1
    assert caught.value.identity["logical_request_id"] == caught.value.sent_request_id
    assert caught.value.identity["selected_route_id"] == "personal/model/stream"


def test_codex_scorer_retains_schema_and_gateway_identity_without_key(monkeypatch):
    content = json.dumps(dict(relevance=8, density=7, recency=6, authority=5, engineering=9, reasoning="useful"))
    requests = install_gateway(monkeypatch, lambda req: httpx.Response(200, json=completion_payload(req, content=content,
                                                                                                  provider="openai", model="native-mini")))
    item = ProviderItem("item", "AI tooling", "https://example.test", "example", "T1", None, None, "Details")
    result = codex_gpt_mini.CodexGptMiniScorer().score_5d(item)
    assert len(requests) == 1
    assert result.engineering == 9
    assert result.raw["model"] == "native-mini"
    schema = json.loads(requests[0].content)["response_format"]
    assert schema["type"] == "json_schema"
    assert schema["json_schema"]["strict"] is True
    assert schema["json_schema"]["schema"]["additionalProperties"] is False


def test_codex_parse_error_keeps_sent_request_id_without_heuristic_fallback(monkeypatch):
    requests = install_gateway(monkeypatch, lambda req: httpx.Response(200, json=completion_payload(req, content='{"relevance": 99}')))
    item = ProviderItem("item", "AI tooling", "https://example.test", "example", "T1", None, None, "Details")
    with pytest.raises(GatewayRequestError) as caught:
        codex_gpt_mini.CodexGptMiniScorer().score_5d(item)
    assert len(requests) == 1
    assert caught.value.sent_request_id == requests[0].headers["X-LLM-Request-ID"]
    assert caught.value.body["llm_gateway"]["logical_request_id"] == caught.value.sent_request_id


def test_legacy_eval_judge_success_keeps_gateway_identity(monkeypatch):
    from airadar.eval import judge

    monkeypatch.setattr(judge, "render_judge_prompt", lambda pair: ("system", "user"))
    monkeypatch.delenv("AI_RADAR_DEEPSEEK_JUDGE_MODEL", raising=False)
    requests = install_gateway(monkeypatch, lambda req: httpx.Response(200, json=completion_payload(req)))
    result = judge.DeepSeekV4ProJudge().judge_pair(None)
    assert len(requests) == 1
    assert json.loads(requests[0].content)["model"] == "deepseek-v4-pro"
    assert result.raw["llm_gateway"]["logical_request_id"] == result.raw["sent_request_id"]


def test_legacy_eval_judge_does_not_retry_business_parse_failure(monkeypatch):
    from airadar.eval import judge

    monkeypatch.setattr(judge, "render_judge_prompt", lambda pair: ("system", "user"))
    requests = install_gateway(monkeypatch, lambda req: httpx.Response(200, json=completion_payload(
        req, content='{"suggestions": 3}')))
    with pytest.raises(GatewayRequestError) as caught:
        judge.DeepSeekV4ProJudge().judge_pair(None)
    assert len(requests) == 1
    assert caught.value.sent_request_id == requests[0].headers["X-LLM-Request-ID"]
    assert caught.value.identity["logical_request_id"] == caught.value.sent_request_id
