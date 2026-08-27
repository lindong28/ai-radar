"""Application-owned egress boundary for checked-in AI Radar transports.

The domain router remains the route authority.  This module only validates its
machine status, launches owned transports through the validated selector, and
emits redacted application-side attempt records.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import urllib.request
from collections.abc import Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from openai import OpenAI
from playwright.sync_api import ProxySettings

EXPECTED_AGENT_PROXY = "http://127.0.0.1:59521"
EXPECTED_POLICY_ID = "domain-routing-v1"
EXPECTED_STATUS_SCHEMA_ID = "agent-domain-routing-status-v1"
EXPECTED_TENCENT_STATUS_SCOPE = "openai-provider-route-aggregate"
STATUS_COMMAND = (
    "/bin/zsh",
    "-fc",
    'source "$HOME/.zshrc" >/dev/null 2>&1; check-proxy-status --format=kv',
)
PROXY_ENV_NAMES = (
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
)
LOOPBACK_NO_PROXY = "localhost,127.0.0.1,::1"
_POLICY_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_VALUES = {
    "stored_mode": "domain-routing",
    "effective_mode": "domain-routing",
    "status_schema_id": EXPECTED_STATUS_SCHEMA_ID,
    "policy_id": EXPECTED_POLICY_ID,
    "policy_projection": "matched",
    "router_status": "running",
    "gcp_sg_status": "healthy",
    "tencent_status": "healthy",
    "tencent_status_scope": EXPECTED_TENCENT_STATUS_SCOPE,
    "direct_status": "healthy",
    "route_attribution": "available",
    "overall_status": "healthy",
}
_AUDIT_LOGGER = logging.getLogger("airadar.egress.audit")
_AUDIT_LOGGER.setLevel(logging.INFO)
_AUDIT_LOGGER.propagate = False
if not any(getattr(handler, "_airadar_egress_audit", False) for handler in _AUDIT_LOGGER.handlers):
    _audit_handler = logging.StreamHandler()
    _audit_handler.setFormatter(logging.Formatter("%(message)s"))
    _audit_handler._airadar_egress_audit = True  # type: ignore[attr-defined]
    _AUDIT_LOGGER.addHandler(_audit_handler)


class EgressPreflightError(RuntimeError):
    """The selector status is absent, malformed, or not healthy."""


class EgressRouteBoundaryError(RuntimeError):
    """One client was asked to cross its selector/direct launch boundary."""


@dataclass(frozen=True, slots=True)
class SelectorPolicy:
    agent_proxy: str
    policy_id: str
    policy_sha256: str


def parse_proxy_status(
    raw: str,
    *,
    expected_agent_proxy: str = EXPECTED_AGENT_PROXY,
) -> SelectorPolicy:
    fields: dict[str, str] = {}
    for line_number, raw_line in enumerate(raw.splitlines(), start=1):
        line = raw_line.strip()
        if not line or "=" not in line:
            raise EgressPreflightError(f"malformed status line {line_number}")
        key, value = line.split("=", 1)
        if not key or not value or key.strip() != key or value.strip() != value:
            raise EgressPreflightError(f"malformed status line {line_number}")
        if key in fields:
            raise EgressPreflightError(f"duplicate status field: {key}")
        fields[key] = value

    missing = sorted(({*_REQUIRED_VALUES, "agent_proxy", "policy_sha256"}) - fields.keys())
    if missing:
        raise EgressPreflightError(f"missing status fields: {','.join(missing)}")
    for key, expected in _REQUIRED_VALUES.items():
        if fields[key] != expected:
            raise EgressPreflightError(f"{key} must be {expected}")
    if fields["agent_proxy"] != expected_agent_proxy:
        raise EgressPreflightError("agent_proxy does not match the domain-router contract")
    if not _POLICY_SHA_RE.fullmatch(fields["policy_sha256"]):
        raise EgressPreflightError("policy_sha256 must be 64 lowercase hexadecimal characters")
    return SelectorPolicy(
        agent_proxy=fields["agent_proxy"],
        policy_id=fields["policy_id"],
        policy_sha256=fields["policy_sha256"],
    )


def _run_status(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


@lru_cache(maxsize=1)
def require_selector_policy() -> SelectorPolicy:
    try:
        completed = _run_status(STATUS_COMMAND)
    except (OSError, subprocess.SubprocessError) as exc:
        raise EgressPreflightError(f"status command failed: {type(exc).__name__}") from exc
    if completed.returncode != 0:
        raise EgressPreflightError(f"status command returned {completed.returncode}")
    return parse_proxy_status(completed.stdout)


def reset_selector_policy_cache() -> None:
    require_selector_policy.cache_clear()


def _hostname(url: str) -> str:
    hostname = urlsplit(url).hostname
    if not hostname:
        raise ValueError("request URL must include a hostname")
    return hostname.casefold()


def is_loopback_url(url: str) -> bool:
    return _hostname(url) in {"localhost", "127.0.0.1", "::1"}


def _audit(
    *,
    policy: SelectorPolicy,
    callsite_id: str,
    hostname: str | None,
    launch: str,
    local_outcome: str,
) -> None:
    _AUDIT_LOGGER.info(
        json.dumps(
            {
                "callsite_id": callsite_id,
                "hostname": hostname,
                "launch": launch,
                "policy_id": policy.policy_id,
                "policy_sha256": policy.policy_sha256,
                "local_outcome": local_outcome,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )


class SelectorHttpxClient(httpx.Client):
    def __init__(
        self,
        policy: SelectorPolicy | None,
        *,
        callsite_id: str,
        request_url: str | None = None,
        **kwargs: Any,
    ) -> None:
        self._selector_policy = policy
        self._callsite_id = callsite_id
        self._launch = "direct-loopback" if request_url and is_loopback_url(request_url) else "selector"
        if self._launch != "direct-loopback" and policy is None:
            raise EgressPreflightError("selector policy is required for external HTTP requests")
        proxy = None if self._launch == "direct-loopback" else policy.agent_proxy
        kwargs.pop("proxy", None)
        kwargs.pop("trust_env", None)
        event_hooks = dict(kwargs.pop("event_hooks", {}))
        request_hooks = list(event_hooks.get("request", []))
        request_hooks.append(self._enforce_request_boundary)
        event_hooks["request"] = request_hooks
        kwargs["event_hooks"] = event_hooks
        super().__init__(proxy=proxy, trust_env=False, **kwargs)

    def _enforce_request_boundary(self, request: httpx.Request) -> None:
        request_is_loopback = is_loopback_url(str(request.url))
        launch_is_loopback = self._launch == "direct-loopback"
        if request_is_loopback != launch_is_loopback:
            raise EgressRouteBoundaryError("request crossed the selector/direct client boundary")

    def send(self, request: httpx.Request, *args: Any, **kwargs: Any) -> httpx.Response:
        hostname = request.url.host.casefold()
        try:
            response = super().send(request, *args, **kwargs)
        except Exception as exc:
            if self._selector_policy is not None:
                _audit(
                    policy=self._selector_policy,
                    callsite_id=self._callsite_id,
                    hostname=hostname,
                    launch=self._launch,
                    local_outcome=f"request:error:{type(exc).__name__}",
                )
            raise
        if self._selector_policy is not None:
            _audit(
                policy=self._selector_policy,
                callsite_id=self._callsite_id,
                hostname=hostname,
                launch=self._launch,
                local_outcome=f"request:http:{response.status_code}",
            )
        return response


def selector_httpx_client(
    policy: SelectorPolicy | None = None,
    *,
    callsite_id: str,
    request_url: str | None = None,
    **kwargs: Any,
) -> SelectorHttpxClient:
    if request_url and is_loopback_url(request_url):
        return SelectorHttpxClient(
            policy,
            callsite_id=callsite_id,
            request_url=request_url,
            **kwargs,
        )
    return SelectorHttpxClient(
        policy or require_selector_policy(),
        callsite_id=callsite_id,
        request_url=request_url,
        **kwargs,
    )


def selector_openai_client(
    policy: SelectorPolicy | None = None,
    *,
    callsite_id: str,
    api_key: str,
    base_url: str | None = None,
    **kwargs: Any,
) -> OpenAI:
    endpoint = base_url or "https://api.openai.com/v1"
    selected_policy = policy
    if selected_policy is None and not is_loopback_url(endpoint):
        selected_policy = require_selector_policy()
    http_client = selector_httpx_client(
        selected_policy,
        callsite_id=callsite_id,
        request_url=endpoint,
    )
    return OpenAI(
        api_key=api_key,
        base_url=base_url,
        http_client=http_client,
        **kwargs,
    )


class _AuditedOpener:
    def __init__(
        self,
        opener: urllib.request.OpenerDirector,
        *,
        policy: SelectorPolicy | None,
        callsite_id: str,
        redirect_handler: _BoundaryRedirectHandler,
        launch: str,
    ) -> None:
        self._opener = opener
        self._policy = policy
        self._callsite_id = callsite_id
        self._redirect_handler = redirect_handler
        self._launch = launch

    def open(self, request: str | urllib.request.Request, *, timeout: float | None = None) -> Any:
        try:
            response = self._opener.open(request, timeout=timeout)
        except Exception as exc:
            if self._policy is not None:
                _audit(
                    policy=self._policy,
                    callsite_id=self._callsite_id,
                    hostname=_hostname(self._redirect_handler.last_url),
                    launch=self._launch,
                    local_outcome=f"request:error:{type(exc).__name__}",
                )
            raise
        if self._policy is not None:
            response_hostname = _hostname(self._redirect_handler.last_url)
            response_url = getattr(response, "geturl", lambda: None)()
            if isinstance(response_url, str):
                response_hostname = _hostname(response_url)
            _audit(
                policy=self._policy,
                callsite_id=self._callsite_id,
                hostname=response_hostname,
                launch=self._launch,
                local_outcome=f"request:http:{getattr(response, 'status', 'unknown')}",
            )
        return response


class _BoundaryRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(
        self,
        *,
        initial_url: str,
        loopback: bool,
        proxy_authority: str | None = None,
    ) -> None:
        self._loopback = loopback
        self._proxy_authority = proxy_authority
        self.last_url = initial_url

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        resolved = urljoin(req.full_url, newurl)
        self.last_url = resolved
        if is_loopback_url(resolved) != self._loopback:
            raise EgressRouteBoundaryError("redirect crossed the selector/direct opener boundary")
        redirected = super().redirect_request(req, fp, code, msg, headers, resolved)
        if redirected is not None and not self._loopback:
            assert self._proxy_authority is not None
            redirected.set_proxy(self._proxy_authority, "http")
        return redirected


def open_external_url(
    request: str | urllib.request.Request,
    *,
    policy: SelectorPolicy | None = None,
    callsite_id: str,
    timeout: float,
) -> Any:
    owned_request = request if isinstance(request, urllib.request.Request) else urllib.request.Request(request)
    request_url = owned_request.full_url
    loopback = is_loopback_url(request_url)
    selected_policy = policy or (None if loopback else require_selector_policy())
    proxy_authority: str | None = None
    if not loopback:
        assert selected_policy is not None
        proxy_authority = urlsplit(selected_policy.agent_proxy).netloc
        if not proxy_authority:
            raise EgressPreflightError("validated agent_proxy has no authority")
        owned_request.set_proxy(proxy_authority, "http")
    redirect_handler = _BoundaryRedirectHandler(
        initial_url=request_url,
        loopback=loopback,
        proxy_authority=proxy_authority,
    )
    opener = _AuditedOpener(
        urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            redirect_handler,
        ),
        policy=selected_policy,
        callsite_id=callsite_id,
        redirect_handler=redirect_handler,
        launch="direct-loopback" if loopback else "selector",
    )
    return opener.open(owned_request, timeout=timeout)


def managed_subprocess_env(
    policy: SelectorPolicy | None = None,
    source: Mapping[str, str] | None = None,
    *,
    callsite_id: str,
) -> dict[str, str]:
    selected_policy = policy or require_selector_policy()
    env: MutableMapping[str, str] = dict(os.environ if source is None else source)
    for name in PROXY_ENV_NAMES:
        env[name] = selected_policy.agent_proxy
    env["NO_PROXY"] = LOOPBACK_NO_PROXY
    env["no_proxy"] = LOOPBACK_NO_PROXY
    _audit(
        policy=selected_policy,
        callsite_id=callsite_id,
        hostname=None,
        launch="managed-standard-env",
        local_outcome="subprocess_env:prepared",
    )
    return dict(env)


def direct_subprocess_env(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return an ambient-proxy-free environment for explicitly direct local tools."""

    env: MutableMapping[str, str] = dict(os.environ if source is None else source)
    for name in PROXY_ENV_NAMES:
        env.pop(name, None)
    env["NO_PROXY"] = LOOPBACK_NO_PROXY
    env["no_proxy"] = LOOPBACK_NO_PROXY
    return dict(env)


def playwright_launch_proxy(
    request_url: str,
    *,
    policy: SelectorPolicy | None = None,
    callsite_id: str,
) -> ProxySettings | None:
    hostname = _hostname(request_url)
    if is_loopback_url(request_url):
        return None
    selected_policy = policy or require_selector_policy()
    _audit(
        policy=selected_policy,
        callsite_id=callsite_id,
        hostname=hostname,
        launch="selector",
        local_outcome="playwright_proxy_config:prepared",
    )
    return {"server": selected_policy.agent_proxy}
