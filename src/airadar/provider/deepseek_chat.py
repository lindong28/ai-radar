from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import json_repair
from openai import OpenAI

from ..llm_usage import LlmUsageRecord, estimate_cost_usd, record_llm_usage, usage_int


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
    attempts: list[tuple[str, str, str, str]] = []
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
    if not attempts:
        raise RuntimeError("DEEPSEEK_API_KEY or ARK_API_KEY is required for DeepSeek provider")

    last_error: Exception | None = None
    for provider, api_key, base_url, model in attempts:
        try:
            client = OpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=float(os.environ.get("AI_RADAR_DEEPSEEK_TIMEOUT", "90")),
            )
            request: dict[str, Any] = {
                "model": model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "response_format": {"type": "json_object"},
                "temperature": temperature,
            }
            if provider == "deepseek" and (
                model.startswith("deepseek-v4") or model in {"deepseek-chat", "deepseek-reasoner"}
            ):
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
                record_llm_usage(
                    LlmUsageRecord(
                        stage=stage,
                        provider=provider,
                        model=actual_model,
                        item_id=item_id,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        total_tokens=total_tokens,
                        input_item_count=input_item_count,
                        input_char_count=input_char_count
                        if input_char_count is not None
                        else len(system) + len(user),
                        cost_usd=estimate_cost_usd(actual_model, input_tokens, output_tokens),
                        attribution={
                            **(attribution or {}),
                            "requested_model": model,
                            "model_env": model_env,
                            "ark_model_env": ark_model_env,
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
    raise RuntimeError(f"all DeepSeek provider endpoints failed: {last_error}")
