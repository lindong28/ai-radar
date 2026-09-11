"""Application-owned egress boundary for checked-in AI Radar transports.

Every owned transport leaves through one local proxy port.  Whatever listens on
that port -- clash today, the domain router before it -- is the route authority;
this module does not re-derive routing decisions, it only pins the single exit,
refuses to run when nothing is serving it, and emits redacted attempt records.

Before 2026-09-09 the exit was pinned by attesting a twelve-field
`check-proxy-status` projection.  That attestation read the selector's *stored
mode* rather than the path, so it failed closed for nine hours on 2026-09-09
while the proxy itself was demonstrably serving requests -- a false negative it
had no way to distinguish from a real outage.

The replacement sends one real request through the port we are about to use.  A
TCP connect would not do: this repository measured that distinction on
2026-08-18, when a healthy-looking `http://127.0.0.1:59527` proxy line sat above
162 sources all failing `Connection refused`, and wrote the rule into CHANGELOG
2026-08-18 -- "端口探测区分不了「本地 listener 活着」与「上游隧道通」".
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import urllib.request
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from openai import OpenAI
from playwright.sync_api import ProxySettings

EGRESS_PROXY_PORT_ENV = "AI_RADAR_EGRESS_PROXY_PORT"
DEFAULT_EGRESS_PROXY_PORT = 59527
EGRESS_POLICY_ID = "local-egress-port-v1"
EGRESS_PROBE_URL_ENV = "AI_RADAR_EGRESS_PROBE_URL"
# Deliberately unrelated to anything this pipeline fetches, so a probe failure is
# never confounded with the outage being diagnosed -- the reason CHANGELOG
# 2026-08-18 picked this same endpoint.
DEFAULT_EGRESS_PROBE_URL = "https://api.github.com/zen"
_PROBE_TIMEOUT_SECONDS = 10.0
PROXY_ENV_NAMES = (
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
)
LOOPBACK_NO_PROXY = "localhost,127.0.0.1,::1"
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


def egress_proxy_port() -> int:
    """The single local port every owned transport leaves through."""

    raw = os.environ.get(EGRESS_PROXY_PORT_ENV, "").strip()
    if not raw:
        return DEFAULT_EGRESS_PROXY_PORT
    try:
        port = int(raw)
    except ValueError as exc:
        raise EgressPreflightError(f"{EGRESS_PROXY_PORT_ENV} is not an integer") from exc
    if not 1 <= port <= 65535:
        raise EgressPreflightError(f"{EGRESS_PROXY_PORT_ENV} is out of range: {port}")
    return port


def policy_for_port(port: int) -> SelectorPolicy:
    """Derive the policy record for one exit port.

    `policy_sha256` is a digest of the exit this process will actually use. Its
    consumer is now the `_audit()` record only -- the interpret receipt gate that
    used to compare it against an attested value was removed on 2026-09-11.
    """

    agent_proxy = f"http://127.0.0.1:{port}"
    digest = hashlib.sha256(f"{EGRESS_POLICY_ID}\n{agent_proxy}\n".encode()).hexdigest()
    return SelectorPolicy(agent_proxy=agent_proxy, policy_id=EGRESS_POLICY_ID, policy_sha256=digest)


def probe_egress_port(port: int) -> None:
    """Fail closed unless a real request gets through the exit port.

    NOT a TCP connect. A connect proves a socket is accepting on loopback and
    nothing else, and this repository has already paid for that distinction:
    on 2026-08-18 00:04 the proxy line read a perfectly healthy
    `http://127.0.0.1:59527` while all 162 sources in the same round failed with
    `Connection refused` from the far side. CHANGELOG 2026-08-18 wrote the rule
    down -- verify by sending a request through the proxy, "端口探测区分不了
    「本地 listener 活着」与「上游隧道通」" -- and the port in that incident is
    the same one this module defaults to.

    Any HTTP response counts, not only 200: reaching the origin at all means
    CONNECT succeeded and the tunnel carried TLS. A status check would instead
    tie our pipeline to one third party's uptime *and* its response codes.
    """

    url = os.environ.get(EGRESS_PROBE_URL_ENV, "").strip() or DEFAULT_EGRESS_PROBE_URL
    # Derived, never spelled a second time: a probe that checks a different
    # endpoint than the transports use passes while every request fails, and
    # nothing downstream can tell. Two independent f-strings drift silently.
    proxy = policy_for_port(port).agent_proxy
    try:
        with httpx.Client(proxy=proxy, timeout=_PROBE_TIMEOUT_SECONDS, trust_env=False) as client:
            client.get(url)
    except httpx.HTTPError as exc:
        raise EgressPreflightError(
            f"no request got through the egress proxy 127.0.0.1:{port} "
            f"({type(exc).__name__}); check who is listening there with "
            f"`lsof -nP -iTCP:{port} -sTCP:LISTEN`, then set "
            f"{EGRESS_PROXY_PORT_ENV} to the port your local router serves"
        ) from exc


@lru_cache(maxsize=1)
def require_selector_policy() -> SelectorPolicy:
    port = egress_proxy_port()
    probe_egress_port(port)
    return policy_for_port(port)


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
