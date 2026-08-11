from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from airadar.db import migrate
from airadar.llm_usage import migrate_usage_db
from airadar.provider import deepseek_chat
from airadar.provider.base import ProviderItem
from airadar.provider.deepseek_v4_pro import DeepSeekV4ProEnricher, DeepSeekV4ProScorer
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


def test_chat_json_persists_completion_usage_for_attributed_call(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "radar.db"
    migrate(db_path)

    class Usage:
        prompt_tokens = 123
        completion_tokens = 45
        total_tokens = 168
        prompt_tokens_details = {"cached_tokens": 23}

    class FakeCompletions:
        def create(self, **kwargs):  # noqa: ANN001
            class Message:
                content = '{"ok": true}'

            class Choice:
                message = Message()

            class Completion:
                model = "deepseek-v4-flash-response"
                usage = Usage()
                choices = [Choice()]

            return Completion()

    class FakeOpenAI:
        def __init__(self, **kwargs):  # noqa: ANN001
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr(deepseek_chat, "OpenAI", FakeOpenAI)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.delenv("ARK_API_KEY", raising=False)

    result = deepseek_chat.chat_json(
        system="Return JSON only.",
        user='Return {"ok": true}.',
        default_model="deepseek-v4-flash",
        model_env="AI_RADAR_DEEPSEEK_PREFILTER_MODEL",
        ark_model_env="AI_RADAR_ARK_PREFILTER_MODEL",
        temperature=0.0,
        max_tokens=20,
        stage="prefilter",
        item_id="item-1",
        input_item_count=1,
        input_char_count=37,
        db_path=db_path,
    )

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT stage, provider, model, item_id, input_tokens, output_tokens,
                   total_tokens, input_item_count, input_char_count, cost_usd, attribution_json
            FROM llm_usage
            """
        ).fetchone()

    assert result.model == "deepseek-v4-flash-response"
    assert row[:10] == ("prefilter", "deepseek", "deepseek-v4-flash-response", "item-1", 123, 45, 168, 1, 37, None)
    assert json.loads(row[10])["cached_input_tokens"] == 23


def test_chat_json_preserves_paid_result_when_metering_write_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    main_db = tmp_path / "radar.db"
    usage_db = tmp_path / "llm_usage.db"
    migrate(main_db)
    migrate_usage_db(usage_db_path=usage_db, main_db_path=main_db)
    with sqlite3.connect(usage_db) as conn:
        conn.execute(
            """
            CREATE TRIGGER reject_usage BEFORE INSERT ON llm_usage
            BEGIN SELECT RAISE(ABORT, 'injected metering failure'); END
            """
        )

    calls: list[str] = []
    breaker_failures: list[Exception] = []

    class Usage:
        prompt_tokens = 10
        completion_tokens = 5
        total_tokens = 15

    class FakeCompletions:
        def __init__(self, base_url: str) -> None:
            self.base_url = base_url

        def create(self, **kwargs):  # noqa: ANN001, ARG002
            calls.append(self.base_url)

            class Message:
                content = '{"ok": true}'

            class Choice:
                message = Message()

            class Completion:
                model = "paid-result-model"
                usage = Usage()
                choices = [Choice()]

            return Completion()

    class FakeOpenAI:
        def __init__(self, **kwargs):  # noqa: ANN001
            self.chat = type(
                "Chat",
                (),
                {"completions": FakeCompletions(str(kwargs["base_url"]))},
            )()

    monkeypatch.setattr(deepseek_chat, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(deepseek_chat.ark_breaker, "is_open", lambda: False)
    monkeypatch.setattr(
        deepseek_chat.ark_breaker,
        "record_failure",
        breaker_failures.append,
    )
    monkeypatch.setenv("AI_RADAR_DB", str(main_db))
    monkeypatch.setenv("AI_RADAR_LLM_USAGE_DB", str(usage_db))
    monkeypatch.setenv("ARK_API_KEY", "ark-test-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key")
    monkeypatch.delenv("ARK_BASE_URL", raising=False)
    monkeypatch.delenv("AI_RADAR_ARK_BASE_URL", raising=False)

    with caplog.at_level(logging.ERROR, logger="airadar.llm_usage"):
        result = deepseek_chat.chat_json(
            system="Return JSON only.",
            user='Return {"ok": true}.',
            default_model="deepseek-v4-flash",
            model_env="AI_RADAR_DEEPSEEK_PREFILTER_MODEL",
            ark_model_env="AI_RADAR_ARK_PREFILTER_MODEL",
            temperature=0,
            stage="prefilter",
            item_id="item-paid",
            db_path=usage_db,
        )

    assert result.json == {"ok": True}
    assert result.provider == "ark"
    assert calls == ["https://ark.cn-beijing.volces.com/api/v3"]
    assert breaker_failures == []
    assert caplog.messages == [
        "llm_usage_metering_failure stage=prefilter provider=ark "
        "model=paid-result-model item_id=item-paid error=IntegrityError:injected metering failure"
    ]


def test_deepseek_providers_tag_usage_by_pipeline_stage(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "radar.db"
    usage_db_path = tmp_path / "llm_usage.db"
    migrate(db_path)
    monkeypatch.setenv("AI_RADAR_DB", str(db_path))
    monkeypatch.setenv("AI_RADAR_LLM_USAGE_DB", str(usage_db_path))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.delenv("ARK_API_KEY", raising=False)

    class Usage:
        prompt_tokens = 10
        completion_tokens = 5
        total_tokens = 15

    class FakeCompletions:
        def create(self, **kwargs):  # noqa: ANN001
            max_tokens = kwargs.get("max_tokens")
            if max_tokens == 200:
                payload = '{"is_ai_related": true, "confidence": 0.88}'
            elif max_tokens == 600:
                payload = (
                    '{"relevance": 8, "density": 7, "recency": 6, '
                    '"authority": 5, "engineering": 9, "reasoning": "useful", "topics": ["模型发布"]}'
                )
            else:
                payload = (
                    '{"title_zh": "中文标题", "summary_zh": "这是一段足够长的中文摘要，说明核心事实和背景。", '
                    '"why_recommend": "它提供了清晰的工程判断信号。", "tags": ["模型发布", "教程/实践"]}'
                )

            class Message:
                content = payload

            class Choice:
                message = Message()

            class Completion:
                model = kwargs["model"]
                usage = Usage()
                choices = [Choice()]

            return Completion()

    class FakeOpenAI:
        def __init__(self, **kwargs):  # noqa: ANN001
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr(deepseek_chat, "OpenAI", FakeOpenAI)
    item = ProviderItem(
        id="item-abc",
        title="LLM benchmark",
        url="https://example.com/llm-benchmark",
        source_id="example",
        tier="T1",
        author="Ada",
        published_at="2026-06-24T00:00:00Z",
        content_text="A practical LLM benchmark with API details.",
    )

    assert DeepSeekV32Prefilter().is_ai_related(item).is_ai_related is True
    assert DeepSeekV4ProScorer().score_5d(item).engineering == pytest.approx(9)
    assert DeepSeekV4ProEnricher().enrich(item).title_zh == "中文标题"

    with sqlite3.connect(db_path) as conn:
        main_usage_count = conn.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0]
    with sqlite3.connect(usage_db_path) as conn:
        rows = conn.execute(
            "SELECT stage, item_id, input_item_count, input_char_count FROM llm_usage ORDER BY id"
        ).fetchall()

    assert main_usage_count == 0
    assert [row[0] for row in rows] == ["prefilter", "score", "enrich"]
    assert {row[1] for row in rows} == {"item-abc"}
    assert all(row[2] == 1 for row in rows)
    assert all(row[3] > len(item.content_text) for row in rows)
