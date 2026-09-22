"""One gateway request, durable run-local evidence, no consumer retries.

Gateway owns provider credentials, routing and authoritative attempt accounting.
Local records retain prompts/responses and correlate via the logical request ID.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from airadar.provider.deepseek_chat import _parse_json_object
from airadar.provider.llm_gateway import (
    gateway_base_url,
    gateway_client,
    gateway_error,
    gateway_headers,
    gateway_identity,
)

CALLSITE_ID = "provider.deepseek_chat.chat_json"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _sync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _persist(path: Path, record: dict[str, Any], *, first: bool = False) -> None:
    """Create exclusively, then replace atomically; fsync before the request proceeds."""
    destination = path if first else path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(record, stream, ensure_ascii=False, allow_nan=False, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    if not first:
        os.replace(destination, path)
    _sync_directory(path.parent)


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list, str, int, float, bool)):
        return value
    return value.model_dump(mode="json")


class TransportError(RuntimeError):
    """A safe error referring to the complete run-local attempt, without SDK text."""

    def __init__(self, attempt_id: str, error_type: str):
        self.attempt_id = attempt_id
        self.error_type = error_type
        super().__init__(f"attempt {attempt_id} failed ({error_type})")


class DurableChat:
    """Inject as inference config['chat']; construct per case for explicit linkage.

    ``provider`` must identify the gateway, never an upstream provider.
    ``thinking`` is explicit because environment loading belongs to the caller.
    """

    def __init__(
        self,
        attempts_dir: Path,
        *,
        provider: str,
        base_url: str,
        project: str = "ai-radar",
        timeout: float = 90,
        thinking: str = "disabled",
        case_id: str | None = None,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        if provider != "llm-gateway":
            raise ValueError("new evaluations require provider=llm-gateway; migrate the config, not frozen runs")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive and finite")
        if not project:
            raise ValueError("gateway project is required")
        self.attempts_dir = Path(attempts_dir)
        self.provider = provider
        self.base_url = gateway_base_url(base_url)
        self.project = project
        self.timeout = timeout
        self.thinking = thinking
        self.case_id = case_id
        self._client_factory = client_factory

    def _save(self, path: Path, record: dict[str, Any], *, first: bool = False) -> None:
        _persist(path, record, first=first)

    def __call__(self, *, stage: str, prompt: dict[str, str], request: dict[str, Any]) -> dict[str, Any]:
        model = request["model"]
        if not isinstance(model, str) or not model:
            raise ValueError("request.model is required")
        # Narrow input prevents hidden stream/retry/header overrides through request.
        unknown = set(request) - {"model", "temperature", "max_tokens"}
        if unknown:
            raise ValueError("unsupported request fields")
        messages = [{"role": role, "content": prompt[key]} for role, key in (("system", "system"), ("user", "user"))]
        api_request: dict[str, Any] = {**request, "messages": messages, "extra_body": {"timeout": self.timeout}}
        # These controls belong to the requested model, not the chosen provider.
        # ARK's native endpoint rejects json_object; prompts already request JSON.
        if model.startswith("deepseek"):
            api_request["extra_body"]["thinking"] = {"type": self.thinking}
        attempt_id = uuid4().hex
        self.attempts_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.attempts_dir / f"{attempt_id}.json"
        record: dict[str, Any] = {
            "attempt_id": attempt_id, "case_id": self.case_id, "stage": stage,
            "gateway_request_id": attempt_id, "gateway_project": self.project, "llm_gateway": None,
            "provider": self.provider, "base_url": self.base_url, "callsite_id": CALLSITE_ID,
            "requested_model": model, "actual_model": None,
            "request_parameters": {key: value for key, value in api_request.items() if key != "messages"},
            "prompt_sha256": hashlib.sha256(json.dumps(messages, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
            "started_at": _now(), "completed_at": None, "status": "started",
            "raw": None, "usage": None, "cost_usd": None, "cost_status": "unpriced",
            "max_retries": 0, "fallback": False,
        }
        self._save(path, record, first=True)
        client = None
        try:
            client = gateway_client(
                callsite_id=CALLSITE_ID, base_url=self.base_url, project=self.project,
                timeout=self.timeout, client_factory=self._client_factory,
            )
            completion = client.chat.completions.create(**api_request, extra_headers=gateway_headers(attempt_id))
            # Capture usage before content/schema access; an empty choices list is still paid.
            record["usage"] = _json_value(getattr(completion, "usage", None))
            record["actual_model"] = getattr(completion, "model", None)
            record["raw"] = _json_value(completion)
            record.update(status="response_received", response_received_at=_now())
            self._save(path, record)
            record["llm_gateway"] = gateway_identity(completion, attempt_id)
            self._save(path, record)
            if record["llm_gateway"]["requested_logical_model"] != model:
                raise ValueError("gateway requested logical model differs from sent model")
            content = completion.choices[0].message.content
            if not isinstance(content, str):
                raise ValueError("completion has no text content")
            parsed = _parse_json_object(content)
            record.update(status="ok", completed_at=_now())
            self._save(path, record)
            return {
                "json": parsed, "model": record["actual_model"], "requested_model": model,
                "provider": self.provider, "usage": record["usage"], "raw": record["raw"],
                "attempt_id": attempt_id,
                "gateway_request_id": attempt_id, "llm_gateway": record["llm_gateway"],
            }
        except Exception as exc:
            gateway_failure = gateway_error(exc, attempt_id)
            if record["raw"] is None:
                # SDK errors can carry usage even though no ChatCompletion was returned.
                body = getattr(exc, "body", None)
                response = getattr(exc, "response", None)
                if response is not None:
                    try:
                        body = response.json()
                    except (ValueError, TypeError):
                        body = response.text
                record["raw"] = _json_value(body)
                if isinstance(body, dict):
                    record["usage"] = body.get("usage", record["usage"])
                    record["actual_model"] = body.get("model", record["actual_model"])
                    record["llm_gateway"] = body.get("llm_gateway")
            record.update(status="error", completed_at=_now(), error_type=type(exc).__name__)
            record["error_code"] = gateway_failure.code
            record["error_action"] = gateway_failure.action
            if gateway_failure.identity:
                record["llm_gateway"] = gateway_failure.identity
            if isinstance(getattr(exc, "status_code", None), int):
                record["http_status"] = exc.status_code
            self._save(path, record)
            raise TransportError(attempt_id, type(exc).__name__) from None
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception as exc:
                    # Closing cannot erase a completed paid attempt or trigger a retry.
                    record["close_error_type"] = type(exc).__name__
                    self._save(path, record)
