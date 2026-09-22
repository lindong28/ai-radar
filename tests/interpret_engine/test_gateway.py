from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import httpx
import numpy as np
import pytest
from openai import OpenAI

from airadar.interpret import runner
from airadar.interpret.engine import embedding
from airadar.interpret.engine.paths import ASSETS
from airadar.interpret.engine.summarizer.core import SummarizerConfig, summarize
from airadar.interpret.engine.summarizer.llm import LLMConfig, complete_text
from airadar.provider.llm_gateway import GatewayRequestError, gateway_client

ROOT = Path(__file__).resolve().parents[2]


def _identity(request, model):
    return {
        "projection_version": 1, "logical_request_id": request.headers["X-LLM-Request-ID"],
        "attempt_id": "attempt-fixture", "provider_id": "fixture-provider",
        "requested_logical_model": model, "actual_model": "fixture-native-model",
    }


def _factory(handler):
    def create(**kwargs):
        kwargs["http_client"].close()
        kwargs["http_client"] = httpx.Client(transport=httpx.MockTransport(handler))
        assert kwargs["max_retries"] == 0
        assert kwargs["api_key"] == "llm-gateway-local-placeholder"
        return OpenAI(**kwargs)
    return create


@pytest.mark.parametrize("endpoint", ["chat", "embeddings"])
@pytest.mark.parametrize("outcome", ["ok", "missing_identity", "wrong_id", "wrong_model", "429", "timeout"])
def test_gateway_single_send_and_identity(endpoint, outcome, monkeypatch):
    monkeypatch.setenv("AI_RADAR_LLM_GATEWAY_PROJECT", "ai-radar")
    for name in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY", "ARK_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    requests = []

    def handler(request):
        requests.append(request)
        body = json.loads(request.content)
        assert request.url.host == "127.0.0.1"
        assert request.headers["X-LLM-Project"] == "ai-radar"
        assert request.headers["X-LLM-Request-ID"]
        assert body["timeout"] == 90
        if outcome == "timeout":
            raise httpx.ReadTimeout("fixture timeout", request=request)
        if outcome == "429":
            return httpx.Response(429, json={"error": {"code": "fixture_rate_limit", "action": "inspect ledger"}})
        identity = _identity(request, body["model"])
        if outcome == "wrong_id":
            identity["logical_request_id"] = "wrong-id"
        if outcome == "wrong_model":
            identity["requested_logical_model"] = "wrong-model"
        if endpoint == "chat":
            result = {"id": "chat-fixture", "object": "chat.completion", "created": 0, "model": "native",
                      "choices": [{"index": 0, "message": {"role": "assistant", "content": "summary"}, "finish_reason": "stop"}],
                      "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}}
        else:
            assert body["model"] == "text-embedding-3-small"
            assert body["encoding_format"] == "float"
            result = {"object": "list", "model": "native", "data": [
                {"object": "embedding", "index": 0, "embedding": [1.0] * 1536}],
                "usage": {"prompt_tokens": 2, "total_tokens": 2}}
        if outcome != "missing_identity":
            result["llm_gateway"] = identity
        return httpx.Response(200, json=result)

    def invoke():
        if endpoint == "chat":
            return asyncio.run(complete_text("system", "user", LLMConfig.from_values(model="deepseek-v4-pro"), client_factory=_factory(handler)))
        with gateway_client(callsite_id="test.embedding", client_factory=_factory(handler)) as client:
            return embedding.get_embeddings(client, ["first"])

    if outcome == "ok":
        result = invoke()
        if endpoint == "chat":
            assert result.metadata["provider"] == "fixture-provider"
            assert result.metadata["backend_model"] == "fixture-native-model"
        else:
            assert result.shape == (1, 1536)
            assert np.isfinite(result).all()
    else:
        with pytest.raises(GatewayRequestError) as exc:
            invoke()
        assert exc.value.sent_request_id == requests[0].headers["X-LLM-Request-ID"]
        if outcome == "429":
            assert exc.value.code == "fixture_rate_limit"
            assert exc.value.action == "inspect ledger"
    assert len(requests) == 1


def _context(tmp_path, monkeypatch):
    kb = tmp_path / "kb"
    user = kb / "default"
    (user / "article_summaries").mkdir(parents=True)
    (user / "index.json").write_text("[]")
    (user / "persona.md").write_text("A reader interested in agent engineering")
    for name in ("building_effective_agents_output.md", "软件工程师头衔要没了ClaudeCode之父YC访谈_output.md"):
        (user / "article_summaries" / name).write_text("Fixture reference format")
    (kb / "tags.md").write_text((ASSETS / "tags.md").read_text())
    monkeypatch.setattr(embedding, "KB_ROOT", kb)
    monkeypatch.setenv("AI_RADAR_KB_ROOT", str(kb))
    return kb


@pytest.mark.parametrize("valid", [True, False])
def test_summary_without_external_checkout_preserves_paid_response(tmp_path, monkeypatch, valid):
    _context(tmp_path, monkeypatch)
    article = tmp_path / "article.md"
    article.write_text("# Fixture article\n\nDistinct engineering details.")
    content = '```json\n' + json.dumps({
        "recommendation": "可跳过", "criteria_reason": "信息增量已由摘要覆盖", "save_decision": False,
        "save_reason": "已覆盖", "tags": [], "keywords": [], "projects": [],
    }, ensure_ascii=False) + '\n```' if valid else "invalid response"
    requests = []

    def handler(request):
        requests.append(request)
        body = json.loads(request.content)
        return httpx.Response(200, json={"id": "fixture", "object": "chat.completion", "created": 0, "model": "native",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            "llm_gateway": _identity(request, body["model"])})

    call = summarize(str(article), config=SummarizerConfig(output_dir=tmp_path / "out"), client_factory=_factory(handler))
    if valid:
        result = asyncio.run(call)
        assert result.save_decision is False
        assert result.model_name == "fixture-native-model"
    else:
        with pytest.raises(GatewayRequestError) as exc:
            asyncio.run(call)
        assert exc.value.sent_request_id == requests[0].headers["X-LLM-Request-ID"]
        assert exc.value.body["choices"][0]["message"]["content"] == content
    assert len(requests) == 1
    receipt = json.loads(next((tmp_path / "out").glob("*_gateway.json")).read_text())
    assert receipt["request_id"] == requests[0].headers["X-LLM-Request-ID"]
    assert receipt["response"]["choices"][0]["message"]["content"] == content


def test_catalog_cli_reads_legacy_relative_paths_without_external_code(tmp_path, monkeypatch):
    kb = _context(tmp_path, monkeypatch)
    (kb / "articles").mkdir()
    (kb / "articles/legacy.md").write_text("Article")
    (kb / "default/article_summaries/legacy_output.md").write_text("Summary")
    (kb / "default/index.json").write_text(json.dumps([{
        "title": "Legacy", "input": {"article_file_path": "data/summary_agent/articles/legacy.md"},
        "output": {"summary_file_path": "data/summary_agent/default/article_summaries/legacy_output.md"},
        "metadata": {"url": "https://example.com/legacy", "source": "fixture", "saved_at": "2026-01-01", "tags": [], "keywords": []},
    }]))
    emb = kb / "default/embeddings"
    emb.mkdir()
    np.save(emb / "vectors.npy", np.ones((1, 1536), dtype=np.float32))
    (emb / "vectors_manifest.json").write_text('{"slugs": ["legacy"]}')
    env = {**runner._subprocess_env_source(), "PYTHONPATH": str(ROOT / "src"), "AI_RADAR_KB_ROOT": str(kb)}
    env.pop("AI_ASSISTANT_ROOT", None)
    process = subprocess.run([sys.executable, "-m", "airadar.interpret.engine.embedding", "--list-article-records", "--user", "default"],
                             cwd=tmp_path, env=env, text=True, capture_output=True, timeout=30)
    assert process.returncode == 0, process.stderr
    header, record = [json.loads(line) for line in process.stdout.splitlines()]
    assert header["alignment_status"] == "exact"
    assert record["article_file_path"] == str((kb / "articles/legacy.md").resolve())


def test_subprocess_boundary_excludes_provider_keys(monkeypatch):
    env = runner._subprocess_env_source({"OPENAI_API_KEY": "never-forward", "ARK_API_KEY": "never-forward", "AI_RADAR_KB_ROOT": "/kb", "AI_RADAR_LLM_GATEWAY_PROJECT": "ai-radar"})
    assert env == {"AI_RADAR_KB_ROOT": "/kb", "AI_RADAR_LLM_GATEWAY_PROJECT": "ai-radar"}
