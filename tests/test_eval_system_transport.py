from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from openai import OpenAI
from openai.types.chat import ChatCompletion

_PATH = Path(__file__).resolve().parents[1] / "evals/_shared/transport.py"
_SPEC = importlib.util.spec_from_file_location("eval_system_transport", _PATH)
transport = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(transport)

SECRET = "test-private-key-never-persist"
PROMPT = {"system": "Return JSON.", "user": "Describe the raw item."}
REQUEST = {"model": "deepseek-v4-pro", "temperature": 0.2, "max_tokens": 600}


def completion(content='{"answer": true}', *, actual_model="served-alias", usage=True):
    return ChatCompletion.model_validate({
        "id": "completion-fixture", "object": "chat.completion", "created": 1,
        "model": actual_model,
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 17, "completion_tokens": 5, "total_tokens": 22,
                  "prompt_tokens_details": {"cached_tokens": 9}} if usage else None,
    })


def records(directory):
    return [json.loads(path.read_text()) for path in directory.glob("*.json")]


def fake_factory(create, created):
    def factory(**kwargs):
        created.append(kwargs)
        def create_with_identity(**request):
            result = create(**request)
            result.model_extra.setdefault("llm_gateway", {
                "projection_version": 1,
                "logical_request_id": request["extra_headers"]["X-LLM-Request-ID"],
                "attempt_id": "fixture-gateway-attempt", "provider_id": "fixture-provider",
                "actual_model": result.model, "requested_logical_model": request["model"],
            })
            return result
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create_with_identity)),
                               close=kwargs["http_client"].close)
    return factory


def chat(directory, factory, **changes):
    return transport.DurableChat(directory, provider="llm-gateway", base_url="http://127.0.0.1:39011/v1",
                                 project="ai-radar", case_id="case-a", client_factory=factory, **changes)


@pytest.mark.parametrize("model", ["deepseek-v4-pro", "personal_ark::deepseek-v4-pro-ga-260813",
                                  "personal_ark::deepseek-v4-flash-ga-260731"])
def test_success_is_durable_before_request_and_before_parse(tmp_path, monkeypatch, model):
    clients, requests = [], []

    def create(**kwargs):
        requests.append(kwargs)
        started = records(tmp_path)
        assert len(started) == 1 and started[0]["status"] == "started"
        assert started[0]["usage"] is None and started[0]["cost_usd"] is None
        assert started[0]["case_id"] == "case-a"
        return completion()

    production_parse = transport._parse_json_object

    def parse(text):
        snapshot = records(tmp_path)[0]
        assert snapshot["status"] == "response_received"
        assert snapshot["raw"]["choices"][0]["message"]["content"] == text
        assert snapshot["usage"]["prompt_tokens"] == 17
        return production_parse(text)

    monkeypatch.setattr(transport, "_parse_json_object", parse)
    request = {**REQUEST, "model": model}
    result = chat(tmp_path, fake_factory(create, clients))(stage="score", prompt=PROMPT, request=request)
    assert result["json"] == {"answer": True}
    assert result["model"] == "served-alias" and result["requested_model"] == model
    assert requests[0]["model"] == model
    assert "response_format" not in requests[0]
    assert requests[0]["extra_body"]["thinking"] == {"type": "disabled"}
    assert clients[0]["max_retries"] == 0
    assert clients[0]["default_headers"] == {"X-LLM-Project": "ai-radar"}
    assert "callsite_id" not in clients[0]
    assert clients[0]["api_key"] == "llm-gateway-local-placeholder"
    final = records(tmp_path)[0]
    assert final["status"] == "ok" and final["completed_at"]
    assert final["usage"]["prompt_tokens_details"]["cached_tokens"] == 9
    assert final["cost_usd"] is None and final["cost_status"] == "unpriced"
    assert final["gateway_request_id"] == requests[0]["extra_headers"]["X-LLM-Request-ID"]
    assert final["llm_gateway"]["logical_request_id"] == final["gateway_request_id"]


def test_parse_failure_keeps_raw_usage_and_models(tmp_path):
    clients = []
    instance = chat(tmp_path, fake_factory(lambda **_: completion("[]"), clients))
    with pytest.raises(transport.TransportError):
        instance(stage="enrich", prompt=PROMPT, request=REQUEST)
    snapshot = records(tmp_path)[0]
    assert snapshot["status"] == "error" and snapshot["error_type"] == "ValueError"
    assert snapshot["raw"]["choices"][0]["message"]["content"] == "[]"
    assert snapshot["usage"]["total_tokens"] == 22
    assert snapshot["actual_model"] == "served-alias"
    assert len(clients) == 1


@pytest.mark.parametrize("model", ["gemini-flash", "personal_other::gemini-flash"])
def test_non_deepseek_selector_does_not_receive_thinking_control(tmp_path, model):
    requests = []

    def create(**request):
        requests.append(request)
        return completion()

    chat(tmp_path, fake_factory(create, []))(stage="score", prompt=PROMPT,
                                           request={**REQUEST, "model": model})
    assert "thinking" not in requests[0]["extra_body"]
    assert requests[0]["model"] == model


def test_actual_sdk_http_failure_is_one_attempt_and_retains_usage(tmp_path):
    requests, clients = [], []

    def handler(request):
        requests.append(request)
        assert records(tmp_path)[0]["status"] == "started"
        return httpx.Response(500, json={"error": {"code": "fixture_failure", "action": "inspect ledger"},
                                        "usage": {"total_tokens": 7}, "model": "error-served"})

    def factory(**kwargs):
        clients.append(kwargs)
        kwargs["http_client"].close()
        return OpenAI(api_key=kwargs["api_key"], base_url=kwargs["base_url"], max_retries=kwargs["max_retries"],
                      default_headers=kwargs["default_headers"],
                      http_client=httpx.Client(transport=httpx.MockTransport(handler), trust_env=False))

    with pytest.raises(transport.TransportError) as caught:
        chat(tmp_path, factory)(stage="score", prompt=PROMPT, request=REQUEST)
    assert len(requests) == len(clients) == 1
    snapshot = records(tmp_path)[0]
    assert snapshot["status"] == "error" and snapshot["http_status"] == 500
    assert snapshot["usage"] == {"total_tokens": 7}
    assert snapshot["actual_model"] == "error-served"
    assert snapshot["error_code"] == "fixture_failure"
    assert snapshot["error_action"] == "inspect ledger"
    assert requests[0].headers["X-LLM-Project"] == "ai-radar"
    assert requests[0].headers["X-LLM-Request-ID"] == snapshot["gateway_request_id"]
    assert SECRET not in str(caught.value)
    assert all(SECRET not in path.read_text() for path in tmp_path.iterdir())


def test_timeout_records_attempt_without_fabricating_usage_or_cost(tmp_path):
    def fail(**kwargs):
        raise TimeoutError(f"SDK error contains {SECRET}")

    with pytest.raises(transport.TransportError):
        chat(tmp_path, fake_factory(fail, []))(stage="prefilter", prompt=PROMPT, request=REQUEST)
    record = records(tmp_path)[0]
    assert record["status"] == "error" and record["usage"] is None and record["cost_usd"] is None
    assert record["actual_model"] is None
    assert SECRET not in json.dumps(record)


def test_missing_choices_does_not_discard_successful_api_usage(tmp_path):
    result = completion()
    result.choices = []
    with pytest.raises(transport.TransportError):
        chat(tmp_path, fake_factory(lambda **_: result, []))(stage="score", prompt=PROMPT, request=REQUEST)
    record = records(tmp_path)[0]
    assert record["usage"]["total_tokens"] == 22 and record["raw"]["choices"] == []


def test_unknown_usage_stays_null_on_success(tmp_path):
    output = chat(tmp_path, fake_factory(lambda **_: completion(usage=False), []))(stage="score", prompt=PROMPT, request=REQUEST)
    assert output["usage"] is None and records(tmp_path)[0]["usage"] is None


def test_gateway_request_omits_json_format_and_keeps_exact_requested_model(tmp_path):
    requests = []

    def create(**kwargs):
        requests.append(kwargs)
        return completion(actual_model="actual-ark-model")

    instance = chat(tmp_path, fake_factory(create, []))
    result = instance(stage="enrich", prompt=PROMPT, request={"model": "caller-pinned-model", "temperature": 0.2})
    assert "response_format" not in requests[0]
    assert "max_tokens" not in requests[0]
    assert requests[0]["model"] == "caller-pinned-model"
    assert result["model"] == "actual-ark-model"
    assert result["requested_model"] == "caller-pinned-model"


@pytest.mark.parametrize("provider,endpoint", [
    ("deepseek", "http://127.0.0.1:39011/v1"), ("ark", "http://127.0.0.1:39011/v1"),
    ("llm-gateway", "https://api.example.test/v1"), ("llm-gateway", "https://user:secret@localhost/v1"),
    ("llm-gateway", "https://localhost/v1?api_key=secret"), ("llm-gateway", "not-an-endpoint"),
])
def test_invalid_provider_or_endpoint_never_creates_client_or_attempt(tmp_path, provider, endpoint):
    clients = []
    with pytest.raises(ValueError):
        transport.DurableChat(tmp_path, provider=provider, base_url=endpoint,
                              client_factory=fake_factory(lambda **_: completion(), clients))
    assert not clients and not records(tmp_path)


def test_provider_key_never_enters_client_metadata_or_returned_payload(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", SECRET)
    monkeypatch.setenv("ARK_API_KEY", SECRET)
    clients = []
    instance = chat(tmp_path, fake_factory(lambda **_: completion(), clients))
    output = instance(stage="score", prompt=PROMPT, request=REQUEST)
    assert SECRET not in repr(instance)
    assert SECRET not in json.dumps(output)
    assert all(SECRET not in path.read_text() for path in tmp_path.iterdir())
    assert clients[0]["api_key"] != SECRET


def test_attempt_write_failure_prevents_network_request(tmp_path, monkeypatch):
    clients = []

    def fail_save(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(transport, "_persist", fail_save)
    with pytest.raises(OSError, match="disk full"):
        chat(tmp_path, fake_factory(lambda **_: completion(), clients))(stage="score", prompt=PROMPT, request=REQUEST)
    assert not clients


def test_repeated_calls_create_distinct_attempt_records(tmp_path):
    instance = chat(tmp_path, fake_factory(lambda **_: completion(), []))
    first = instance(stage="score", prompt=PROMPT, request=REQUEST)
    second = instance(stage="score", prompt=PROMPT, request=REQUEST)
    assert first["attempt_id"] != second["attempt_id"]
    assert len(records(tmp_path)) == 2


@pytest.mark.parametrize("identity", [None, {"projection_version": 2},
                                      {"projection_version": 1, "logical_request_id": "different"}])
def test_invalid_identity_preserves_raw_before_failure_and_never_resends(tmp_path, identity):
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        result = completion()
        result.model_extra["llm_gateway"] = identity
        return result

    with pytest.raises(transport.TransportError):
        chat(tmp_path, fake_factory(create, []))(stage="score", prompt=PROMPT, request=REQUEST)
    assert len(calls) == 1
    snapshot = records(tmp_path)[0]
    assert snapshot["status"] == "error"
    assert snapshot["gateway_request_id"] == calls[0]["extra_headers"]["X-LLM-Request-ID"]
    assert snapshot["raw"]["llm_gateway"] == identity
    assert snapshot["usage"]["total_tokens"] == 22
