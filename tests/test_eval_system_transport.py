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
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)), close=lambda: None)
    return factory


def chat(directory, factory, **changes):
    return transport.DurableChat(directory, provider="deepseek", base_url="https://api.example.test/v1",
                                 api_key=SECRET, case_id="case-a", client_factory=factory, **changes)


def test_success_is_durable_before_request_and_before_parse(tmp_path, monkeypatch):
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
    result = chat(tmp_path, fake_factory(create, clients))(stage="score", prompt=PROMPT, request=REQUEST)
    assert result["json"] == {"answer": True}
    assert result["model"] == "served-alias" and result["requested_model"] == REQUEST["model"]
    assert requests[0]["model"] == REQUEST["model"]
    assert requests[0]["response_format"] == {"type": "json_object"}
    assert requests[0]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert clients[0]["max_retries"] == 0
    assert clients[0]["callsite_id"] == "provider.deepseek_chat.chat_json"
    final = records(tmp_path)[0]
    assert final["status"] == "ok" and final["completed_at"]
    assert final["usage"]["prompt_tokens_details"]["cached_tokens"] == 9
    assert final["cost_usd"] is None and final["cost_status"] == "unpriced"


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


def test_actual_sdk_http_failure_is_one_attempt_and_retains_usage(tmp_path):
    requests, clients = [], []

    def handler(request):
        requests.append(request)
        assert records(tmp_path)[0]["status"] == "started"
        return httpx.Response(500, json={"error": {"message": SECRET}, "usage": {"total_tokens": 7}, "model": "error-served"})

    def factory(**kwargs):
        clients.append(kwargs)
        return OpenAI(api_key=kwargs["api_key"], base_url=kwargs["base_url"], max_retries=kwargs["max_retries"],
                      http_client=httpx.Client(transport=httpx.MockTransport(handler)))

    with pytest.raises(transport.TransportError) as caught:
        chat(tmp_path, factory)(stage="score", prompt=PROMPT, request=REQUEST)
    assert len(requests) == len(clients) == 1
    snapshot = records(tmp_path)[0]
    assert snapshot["status"] == "error" and snapshot["http_status"] == 500
    assert snapshot["usage"] == {"total_tokens": 7}
    assert snapshot["actual_model"] == "error-served"
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


def test_ark_request_omits_json_format_and_keeps_exact_requested_model(tmp_path):
    requests = []

    def create(**kwargs):
        requests.append(kwargs)
        return completion(actual_model="actual-ark-model")

    instance = transport.DurableChat(tmp_path, provider="ark", base_url="https://ark.example.test/api/v3",
                                     api_key=SECRET, client_factory=fake_factory(create, []))
    result = instance(stage="enrich", prompt=PROMPT, request={"model": "caller-pinned-model", "temperature": 0.2})
    assert "response_format" not in requests[0]
    assert "max_tokens" not in requests[0]
    assert requests[0]["model"] == "caller-pinned-model"
    assert result["model"] == "actual-ark-model"
    assert result["requested_model"] == "caller-pinned-model"


@pytest.mark.parametrize("provider,endpoint", [
    ("unknown", "https://example.test/v1"), ("deepseek", "https://user:secret@example.test/v1"),
    ("deepseek", "https://example.test/v1?api_key=secret"), ("deepseek", "not-an-endpoint"),
])
def test_invalid_provider_or_endpoint_never_creates_client_or_attempt(tmp_path, provider, endpoint):
    clients = []
    with pytest.raises(ValueError):
        transport.DurableChat(tmp_path, provider=provider, base_url=endpoint, api_key=SECRET,
                              client_factory=fake_factory(lambda **_: completion(), clients))
    assert not clients and not records(tmp_path)


def test_key_never_enters_repr_metadata_or_returned_payload(tmp_path):
    instance = chat(tmp_path, fake_factory(lambda **_: completion(json.dumps({"echo": SECRET})), []))
    output = instance(stage="score", prompt={"system": SECRET, "user": "raw"}, request=REQUEST)
    assert SECRET not in repr(instance)
    assert SECRET not in json.dumps(output)
    assert all(SECRET not in path.read_text() for path in tmp_path.iterdir())
    assert records(tmp_path)[0]["raw"]["choices"][0]["message"]["content"] == '{"echo": "[REDACTED]"}'


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
