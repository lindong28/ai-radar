from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

import json_repair

from ..llm_usage import (
    LlmUsageRecord,
    record_llm_usage_best_effort,
    usage_int,
)
from .llm_gateway import gateway_client, gateway_error, gateway_headers, gateway_identity

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChatJsonResult:
    json: dict[str, Any]
    provider: str
    model: str
    gateway: dict[str, Any] = field(default_factory=dict)
    sent_request_id: str | None = None


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
    model_env: str | None,
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
    # ark_model_env remains a compatibility argument; logical model selection
    # and gateway routing do not read provider-specific model overrides.
    model = os.environ.get(model_env, default_model) if model_env else default_model
    request_id = str(uuid4())
    completion = None
    client = None
    try:
        timeout = float(os.environ.get("AI_RADAR_DEEPSEEK_TIMEOUT", "90"))
        client = gateway_client(
            callsite_id="provider.deepseek_chat.chat_json",
            timeout=timeout,
        )
        request: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": temperature,
            "extra_headers": gateway_headers(request_id),
            "extra_body": {"timeout": timeout},
        }
        # Some eligible ARK endpoints reject response_format. Preserve the JSON
        # prompt and parser without imposing this control on every candidate.
        if model.startswith("deepseek-v4") or model in {"deepseek-chat", "deepseek-reasoner"}:
            request["extra_body"]["thinking"] = {"type": os.environ.get("AI_RADAR_DEEPSEEK_THINKING", "disabled")}
        if max_tokens is not None:
            request["max_tokens"] = max_tokens
        completion = client.chat.completions.create(**request)
        identity = gateway_identity(completion, request_id)
        if identity["requested_logical_model"] != model:
            raise ValueError("gateway requested logical model does not match the sent model")
        provider = identity["provider_id"]
        actual_model = identity["actual_model"]
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
                        "sent_request_id": request_id,
                        "llm_gateway": identity,
                    },
                ),
                db_path=db_path,
                usage=usage,
            )
        content = completion.choices[0].message.content
        if content is None:
            raise ValueError("chat response did not include message content")
        return ChatJsonResult(
            json=_parse_json_object(content), provider=provider, model=actual_model,
            gateway=identity, sent_request_id=request_id,
        )
    except Exception as exc:
        raise gateway_error(exc, request_id, completion=completion) from exc
    finally:
        if client is not None:
            try:
                client.close()
            except Exception as exc:
                logger.warning("LLM Gateway client close failed request_id=%s error_type=%s", request_id, type(exc).__name__)
