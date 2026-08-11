from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import json_repair
from openai import OpenAI

from ..llm_usage import (
    LlmUsageRecord,
    cache_usage_attribution,
    record_llm_usage_best_effort,
    usage_int,
)
from . import ark_breaker


@dataclass(frozen=True)
class ChatJsonResult:
    json: dict[str, Any]
    provider: str
    model: str


def _normalized_deepseek_base_url() -> str:
    configured = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
    if configured.endswith("/chat/completions"):
        configured = configured[: -len("/chat/completions")]
    if configured == "https://api.deepseek.com":
        configured = f"{configured}/v1"
    return configured


def _ark_base_url() -> str:
    return os.environ.get("AI_RADAR_ARK_BASE_URL") or os.environ.get(
        "ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"
    )


def _parse_json_object(content: str) -> dict[str, Any]:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        parsed = json_repair.loads(content)
    if isinstance(parsed, list) and len(parsed) == 1 and isinstance(parsed[0], dict):
        parsed = parsed[0]
    if not isinstance(parsed, dict):
        raise ValueError("chat response JSON must be an object")
    return parsed


def chat_json(
    *,
    system: str,
    user: str,
    default_model: str,
    model_env: str,
    ark_model_env: str,
    temperature: float,
    max_tokens: int | None = None,
    stage: str | None = None,
    item_id: str | None = None,
    input_item_count: int = 1,
    input_char_count: int | None = None,
    attribution: dict[str, Any] | None = None,
    db_path: str | Path | None = None,
) -> ChatJsonResult:
    # ARK (Volcengine agent plan) is tried first so its prepaid monthly token
    # allowance is consumed before the pay-per-token DeepSeek fallback. The
    # breaker short-circuits ARK once that allowance is exhausted.
    attempts: list[tuple[str, str, str, str]] = []
    ark_key = os.environ.get("ARK_API_KEY")
    if ark_key:
        attempts.append(
            (
                "ark",
                ark_key,
                _ark_base_url(),
                os.environ.get(ark_model_env)
                or os.environ.get("AI_RADAR_ARK_DEEPSEEK_MODEL")
                or os.environ.get(model_env, default_model),
            )
        )
    deepseek_key = os.environ.get("DEEPSEEK_API_KEY")
    if deepseek_key:
        attempts.append(
            (
                "deepseek",
                deepseek_key,
                _normalized_deepseek_base_url(),
                os.environ.get(model_env, default_model),
            )
        )
    if not attempts:
        raise RuntimeError("DEEPSEEK_API_KEY or ARK_API_KEY is required for DeepSeek provider")

    has_deepseek_fallback = bool(deepseek_key)
    last_error: Exception | None = None
    for provider, api_key, base_url, model in attempts:
        # Skip ARK while the breaker is open, but only when DeepSeek can take over;
        # if ARK is the only configured provider there is nothing to fall back to.
        if provider == "ark" and has_deepseek_fallback and ark_breaker.is_open():
            continue
        try:
            client = OpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=float(os.environ.get("AI_RADAR_DEEPSEEK_TIMEOUT", "90")),
            )
            request: dict[str, Any] = {
                "model": model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "temperature": temperature,
            }
            if provider == "ark":
                # The agent-plan endpoint rejects response_format=json_object (HTTP 400)
                # and defaults thinking ON (burning reasoning tokens). Omit the former and
                # always disable the latter; prompts already ask for JSON and
                # _parse_json_object() repairs any stray wrapping.
                request["extra_body"] = {"thinking": {"type": os.environ.get("AI_RADAR_ARK_THINKING", "disabled")}}
            else:
                request["response_format"] = {"type": "json_object"}
                if model.startswith("deepseek-v4") or model in {"deepseek-chat", "deepseek-reasoner"}:
                    request["extra_body"] = {
                        "thinking": {"type": os.environ.get("AI_RADAR_DEEPSEEK_THINKING", "disabled")}
                    }
            if max_tokens is not None:
                request["max_tokens"] = max_tokens
            completion = client.chat.completions.create(**request)
            actual_model = str(getattr(completion, "model", None) or model)
            usage = getattr(completion, "usage", None)
            if stage is not None:
                input_tokens = usage_int(usage, "prompt_tokens")
                output_tokens = usage_int(usage, "completion_tokens")
                total_tokens = usage_int(usage, "total_tokens") or input_tokens + output_tokens
                record_llm_usage_best_effort(
                    LlmUsageRecord(
                        stage=stage,
                        provider=provider,
                        model=actual_model,
                        item_id=item_id,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        total_tokens=total_tokens,
                        input_item_count=input_item_count,
                        input_char_count=input_char_count if input_char_count is not None else len(system) + len(user),
                        attribution={
                            **(attribution or {}),
                            "requested_model": model,
                            "model_env": model_env,
                            "ark_model_env": ark_model_env,
                            **cache_usage_attribution(usage),
                        },
                    ),
                    db_path=db_path,
                )
            content = completion.choices[0].message.content
            if content is None:
                raise ValueError("chat response did not include message content")
            return ChatJsonResult(json=_parse_json_object(content), provider=provider, model=actual_model)
        except Exception as exc:  # pragma: no cover - exercised only with live providers
            last_error = exc
            if provider == "ark":
                ark_breaker.record_failure(exc)
    raise RuntimeError(f"all DeepSeek provider endpoints failed: {last_error}")
