from __future__ import annotations

from typing import Any

import pytest

from airadar.provider import ark_breaker, deepseek_chat
from airadar.provider.deepseek_v4_flash import DeepSeekV4FlashScorer
from airadar.scorer import runner

ARK_BASE = "https://ark.test/api/plan/v3"
DEEPSEEK_BASE = "https://deepseek.test/v1"


def _install_fake_openai(monkeypatch, calls: list[dict[str, Any]], *, ark_error: Exception | None = None) -> None:
    """Fake OpenAI client that records every request and routes by base_url.

    ARK requests (base_url contains "ark.test") raise `ark_error` when provided,
    otherwise return a marker payload; DeepSeek requests always succeed.
    """

    class FakeCompletions:
        def __init__(self, base_url: str | None) -> None:
            self._base_url = base_url or ""

        def create(self, **kwargs: Any):  # noqa: ANN401
            calls.append({"base_url": self._base_url, **kwargs})
            is_ark = "ark.test" in self._base_url
            if is_ark and ark_error is not None:
                raise ark_error
            content = '{"ok": "ark"}' if is_ark else '{"ok": "deepseek"}'

            message = type("Message", (), {"content": content})()
            choice = type("Choice", (), {"message": message})()
            return type("Completion", (), {"choices": [choice], "model": kwargs["model"], "usage": None})()

    class FakeOpenAI:
        def __init__(self, **kwargs: Any) -> None:  # noqa: ANN401
            self.chat = type("Chat", (), {"completions": FakeCompletions(kwargs.get("base_url"))})()

    monkeypatch.setattr(deepseek_chat, "OpenAI", FakeOpenAI)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ARK_BASE_URL", ARK_BASE)
    monkeypatch.setenv("DEEPSEEK_BASE_URL", DEEPSEEK_BASE)
    monkeypatch.setenv("AI_RADAR_ARK_BREAKER_STATE", str(tmp_path / "ark-breaker.json"))
    for name in (
        "AI_RADAR_ARK_BASE_URL",
        "AI_RADAR_ARK_DEEPSEEK_MODEL",
        "AI_RADAR_ARK_PREFILTER_MODEL",
        "AI_RADAR_DEEPSEEK_PREFILTER_MODEL",
        "AI_RADAR_ARK_BREAKER_COOLDOWN_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)


def _chat(**overrides: Any):
    params: dict[str, Any] = dict(
        system="Return JSON only.",
        user="Return an object.",
        default_model="deepseek-v4-flash",
        model_env="AI_RADAR_DEEPSEEK_PREFILTER_MODEL",
        ark_model_env="AI_RADAR_ARK_PREFILTER_MODEL",
        temperature=0.0,
        max_tokens=20,
    )
    params.update(overrides)
    return deepseek_chat.chat_json(**params)


def test_ark_is_tried_first_when_both_keys_present(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "ark-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")
    calls: list[dict[str, Any]] = []
    _install_fake_openai(monkeypatch, calls)

    result = _chat()

    assert result.provider == "ark"
    assert result.json == {"ok": "ark"}
    assert len(calls) == 1  # DeepSeek never reached


def test_ark_request_drops_json_object_and_disables_thinking(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "ark-key")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    calls: list[dict[str, Any]] = []
    _install_fake_openai(monkeypatch, calls)

    _chat()

    ark_call = calls[0]
    assert "response_format" not in ark_call  # agent-plan endpoint 400s on json_object
    assert ark_call["extra_body"] == {"thinking": {"type": "disabled"}}


def test_deepseek_request_keeps_json_object(monkeypatch):
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")
    calls: list[dict[str, Any]] = []
    _install_fake_openai(monkeypatch, calls)

    _chat()

    assert calls[0]["response_format"] == {"type": "json_object"}


def test_quota_error_trips_breaker_and_falls_back(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "ark-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")
    calls: list[dict[str, Any]] = []
    _install_fake_openai(monkeypatch, calls, ark_error=Exception("Error code 429: quota exhausted"))

    result = _chat()

    assert result.provider == "deepseek"  # fell back for this item
    assert [("ark.test" in c["base_url"]) for c in calls] == [True, False]
    assert ark_breaker.is_open() is True


def test_open_breaker_skips_ark_entirely(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "ark-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")
    ark_breaker.trip("preexisting quota failure")
    calls: list[dict[str, Any]] = []
    _install_fake_openai(monkeypatch, calls)  # ARK would succeed if called

    result = _chat()

    assert result.provider == "deepseek"  # ARK skipped despite being healthy
    assert len(calls) == 1
    assert "ark.test" not in calls[0]["base_url"]


def test_breaker_retries_ark_after_cooldown(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "ark-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")
    monkeypatch.setenv("AI_RADAR_ARK_BREAKER_COOLDOWN_SECONDS", "0")
    ark_breaker.trip("expired failure")
    calls: list[dict[str, Any]] = []
    _install_fake_openai(monkeypatch, calls)

    result = _chat()

    assert result.provider == "ark"  # cooldown elapsed -> ARK retried


def test_non_quota_error_does_not_trip_breaker(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "ark-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")
    calls: list[dict[str, Any]] = []
    _install_fake_openai(monkeypatch, calls, ark_error=Exception("connection reset by peer"))

    result = _chat()

    assert result.provider == "deepseek"  # still falls back for this item
    assert ark_breaker.is_open() is False  # but breaker stays closed


def test_is_quota_error_classification():
    assert ark_breaker.is_quota_error(Exception("rate limit exceeded")) is True
    assert ark_breaker.is_quota_error(Exception("insufficient balance")) is True

    class Status429(Exception):
        status_code = 429

    assert ark_breaker.is_quota_error(Status429("boom")) is True
    assert ark_breaker.is_quota_error(Exception("connection reset")) is False


def test_flash_scorer_is_default(monkeypatch):
    monkeypatch.delenv("AI_RADAR_SCORER", raising=False)
    assert DeepSeekV4FlashScorer.model_id == "deepseek-v4-flash"
    assert isinstance(runner._provider_from_env(), DeepSeekV4FlashScorer)
