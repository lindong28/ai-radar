from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import json_repair
from openai import OpenAI


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
            content = completion.choices[0].message.content
            if content is None:
                raise ValueError("chat response did not include message content")
            return ChatJsonResult(json=_parse_json_object(content), provider=provider, model=model)
        except Exception as exc:  # pragma: no cover - exercised only with live providers
            last_error = exc
    raise RuntimeError(f"all DeepSeek provider endpoints failed: {last_error}")
