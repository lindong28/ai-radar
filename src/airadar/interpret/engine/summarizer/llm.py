"""Single-send gateway transport; no provider keys, routing or fallback here."""

from __future__ import annotations

import asyncio
import math
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from airadar.provider.llm_gateway import (
    gateway_base_url,
    gateway_client,
    gateway_error,
    gateway_headers,
    gateway_identity,
)


@dataclass(frozen=True)
class LLMConfig:
    model: str
    base_url: str | None = None
    temperature: float = 0.3
    max_tokens: int = 8000

    @classmethod
    def from_values(cls, *, model=None, api_key=None, base_url=None, temperature=0.3, max_tokens=8000):
        if api_key:
            raise ValueError("Provider API keys are not accepted; configure llm-gateway instead")
        model = model or os.environ.get("AI_RADAR_INTERPRET_MODEL", "deepseek-v4-pro")
        if model == "ai-radar-interpret-deepseek":
            model = os.environ.get("AI_RADAR_DEEPSEEK_INTERPRET_MODEL", "deepseek-v4-pro")
        if not isinstance(model, str) or not model.strip() or not math.isfinite(temperature):
            raise ValueError("A logical model and finite temperature are required")
        return cls(model, gateway_base_url(base_url), temperature, max_tokens)


@dataclass(frozen=True)
class LLMResponse:
    content: str
    metadata: dict[str, Any]
    raw: Any = None


def render_prompts(template_dir: Path, context: dict[str, Any]) -> tuple[str, str]:
    env = Environment(
        loader=FileSystemLoader(str(template_dir)), undefined=StrictUndefined,
        autoescape=False, trim_blocks=True, lstrip_blocks=True,
    )
    return env.get_template("system.md.j2").render(**context), env.get_template("user_article.md.j2").render(**context)


def supported_model_aliases() -> list[str]:
    # Logical models are centrally maintained, not a local provider catalog.
    return ["deepseek-v4-pro", "ai-radar-interpret-deepseek"]


def _complete(system_prompt, user_prompt, config, client_factory):
    request_id = str(uuid4())
    completion = None
    client = gateway_client(
        callsite_id="interpret.engine.chat", base_url=config.base_url,
        timeout=90, client_factory=client_factory,
    )
    try:
        completion = client.chat.completions.create(
            model=config.model,
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            temperature=config.temperature, max_tokens=config.max_tokens,
            extra_headers=gateway_headers(request_id),
            extra_body={"timeout": 90, **(
                {"thinking": {"type": os.environ.get("AI_RADAR_DEEPSEEK_THINKING", "disabled")}}
                if config.model.startswith("deepseek-") else {}
            )},
        )
        identity = gateway_identity(completion, request_id)
        if identity["requested_logical_model"] != config.model:
            raise ValueError("Gateway requested logical model mismatch")
        content = completion.choices[0].message.content
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Gateway returned empty summary text")
        usage = completion.usage
        return LLMResponse(content, {
            "requested_model": config.model, "backend_attempted": "llm-gateway",
            "backend_used": "llm-gateway", "provider": identity["provider_id"],
            "backend_model": identity["actual_model"], "fallback_used": False,
            "gateway_request_id": request_id, "gateway": identity,
            "usage": usage.model_dump() if usage is not None else None,
            "input_char_count": len(system_prompt) + len(user_prompt),
        }, completion)
    except Exception as exc:
        raise gateway_error(exc, request_id, completion=completion) from exc
    finally:
        client.close()


async def complete_text(
    system_prompt: str, user_prompt: str, config: LLMConfig, *,
    client_factory: Callable[..., Any] | None = None, temp_dir: Path | None = None,
) -> LLMResponse:
    del temp_dir
    return await asyncio.to_thread(_complete, system_prompt, user_prompt, config, client_factory)
