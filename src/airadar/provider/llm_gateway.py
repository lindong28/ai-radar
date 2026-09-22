"""Single-send SDK transport for the same-host LLM Gateway."""

from __future__ import annotations

import ipaddress
import math
import os
from collections.abc import Callable
from typing import Any
from urllib.parse import unquote, urlsplit
from uuid import UUID

import httpx
from openai import OpenAI

DEFAULT_BASE_URL = "http://127.0.0.1:39011/v1"


class GatewayRequestError(RuntimeError):
    """Keep the sent identity even when no usable completion was received."""

    def __init__(
        self,
        request_id: str,
        *,
        code: str,
        action: str = "Inspect the gateway ledger before issuing another request.",
        body: Any = None,
        identity: dict[str, Any] | None = None,
    ) -> None:
        self.sent_request_id = request_id
        self.code = code
        self.action = action
        self.body = body
        self.identity = identity
        super().__init__(f"LLM Gateway request failed: request_id={request_id} code={code}; {action}")


def gateway_base_url(value: str | None = None) -> str:
    configured = value if value is not None else os.environ.get("AI_RADAR_LLM_GATEWAY_BASE_URL", DEFAULT_BASE_URL)
    parsed = urlsplit(configured)
    host = parsed.hostname or ""
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host == "localhost"
    if (
        parsed.scheme not in {"http", "https"}
        or not loopback
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != "/v1"
    ):
        raise ValueError("LLM Gateway base URL must be a credential-free loopback HTTP(S) /v1 URL")
    # Accessing port also rejects invalid or out-of-range values before dispatch.
    _ = parsed.port
    return configured.rstrip("/")


def gateway_client(
    *,
    callsite_id: str,
    base_url: str | None = None,
    project: str | None = None,
    timeout: float = 90,
    client_factory: Callable[..., Any] | None = None,
) -> Any:
    """Provider credentials, proxy environment, redirects and retries stay out."""
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("LLM Gateway timeout must be positive and finite")
    endpoint = gateway_base_url(base_url)
    project_id = project if project is not None else os.environ.get("AI_RADAR_LLM_GATEWAY_PROJECT", "ai-radar")
    if not project_id or not callsite_id:
        raise ValueError("LLM Gateway project and callsite_id are required")
    transport = httpx.Client(trust_env=False, follow_redirects=False, timeout=timeout)
    kwargs = {
        "api_key": "llm-gateway-local-placeholder",
        "base_url": endpoint,
        "timeout": timeout,
        "max_retries": 0,
        "default_headers": {"X-LLM-Project": project_id},
        "http_client": transport,
    }
    try:
        return client_factory(**kwargs) if client_factory is not None else OpenAI(**kwargs)
    except Exception:
        transport.close()
        raise


def gateway_headers(request_id: str) -> dict[str, str]:
    if not request_id:
        raise ValueError("LLM Gateway request id is required")
    headers = {"X-LLM-Request-ID": request_id}
    session = os.environ.get("AI_RADAR_LLM_GATEWAY_SESSION", "")
    prefix, separator, value = session.partition(":")
    try:
        valid_session = separator and prefix in {"claude", "codex"} and str(UUID(value)) == value
    except ValueError:
        valid_session = False
    if valid_session:
        headers["X-LLM-Session"] = session
    return headers


def _response_body(completion: Any) -> Any:
    if isinstance(completion, dict):
        return completion
    dump = getattr(completion, "model_dump", None)
    return dump(mode="json") if callable(dump) else None


def gateway_identity(completion: Any, request_id: str) -> dict[str, Any]:
    if not isinstance(request_id, str) or not request_id:
        raise GatewayRequestError("unavailable", code="missing_sent_request_id", body=_response_body(completion))
    extra = completion if isinstance(completion, dict) else getattr(completion, "model_extra", None)
    identity = extra.get("llm_gateway") if isinstance(extra, dict) else None
    if not isinstance(identity, dict) or type(identity.get("projection_version")) is not int or identity["projection_version"] != 1:
        raise GatewayRequestError(request_id, code="invalid_gateway_identity", body=_response_body(completion))
    if identity.get("logical_request_id") != request_id:
        raise GatewayRequestError(
            request_id, code="gateway_identity_mismatch", body=_response_body(completion), identity=identity
        )
    for field in ("attempt_id", "provider_id", "actual_model", "requested_logical_model"):
        if not isinstance(identity.get(field), str) or not identity[field]:
            raise GatewayRequestError(
                request_id, code="incomplete_gateway_identity", body=_response_body(completion), identity=identity
            )
    return dict(identity)


def gateway_error(exc: Exception, request_id: str, *, completion: Any = None) -> GatewayRequestError:
    if isinstance(exc, GatewayRequestError):
        return exc
    body = getattr(exc, "body", None)
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            body = response.json()
        except ValueError:
            body = response.text
    if body is None:
        body = _response_body(completion)
    error = body.get("error", body) if isinstance(body, dict) else {}
    error = error if isinstance(error, dict) else {}
    identity = error.get("llm_gateway")
    if identity is None and response is not None:
        prefix = "x-llm-gateway-"
        identity = {
            name[len(prefix):].replace("-", "_"): unquote(value)
            for name, value in response.headers.items()
            if name.startswith(prefix)
        } or None
    return GatewayRequestError(
        request_id,
        code=str(error.get("code") or type(exc).__name__),
        action=str(error.get("action") or "Inspect the gateway ledger before issuing another request."),
        body=body,
        identity=identity,
    )


def gateway_smoke_status(*, heuristic: bool = False) -> str:
    if heuristic and os.environ.get("AI_RADAR_FORCE_HEURISTIC"):
        return "offline heuristic explicitly enabled; LLM Gateway not checked"
    gateway_base_url()
    return "LLM Gateway configured; listener, model readiness and inference not checked"
