from __future__ import annotations

from typing import Any

from airadar.provider import deepseek_chat
from airadar.provider.deepseek_v32 import DeepSeekV32Prefilter


def test_prefilter_default_model_uses_current_deepseek_v4_flash() -> None:
    assert DeepSeekV32Prefilter.model_id == "deepseek-v4-flash"


def test_chat_json_disables_deepseek_v4_thinking_for_json_mode(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    class FakeCompletions:
        def create(self, **kwargs):  # noqa: ANN001
            calls.append(kwargs)

            class Message:
                content = '{"ok": true}'

            class Choice:
                message = Message()

            class Completion:
                choices = [Choice()]

            return Completion()

    class FakeOpenAI:
        def __init__(self, **kwargs):  # noqa: ANN001
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr(deepseek_chat, "OpenAI", FakeOpenAI)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    monkeypatch.delenv("AI_RADAR_DEEPSEEK_THINKING", raising=False)

    result = deepseek_chat.chat_json(
        system="Return JSON only.",
        user='Return {"ok": true}.',
        default_model="deepseek-v4-flash",
        model_env="AI_RADAR_DEEPSEEK_PREFILTER_MODEL",
        ark_model_env="AI_RADAR_ARK_PREFILTER_MODEL",
        temperature=0.0,
        max_tokens=20,
    )

    assert result.json == {"ok": True}
    assert result.model == "deepseek-v4-flash"
    assert calls[0]["extra_body"] == {"thinking": {"type": "disabled"}}
