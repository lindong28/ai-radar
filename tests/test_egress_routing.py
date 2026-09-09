from __future__ import annotations

import contextlib
import json
import logging
import socket
import threading
import urllib.error
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import openai
import pytest

from airadar import cli
from airadar.egress import (
    DEFAULT_EGRESS_PROXY_PORT,
    EGRESS_POLICY_ID,
    EGRESS_PROBE_URL_ENV,
    EGRESS_PROXY_PORT_ENV,
    EgressPreflightError,
    EgressRouteBoundaryError,
    SelectorPolicy,
    direct_subprocess_env,
    egress_proxy_port,
    managed_subprocess_env,
    open_external_url,
    playwright_launch_proxy,
    policy_for_port,
    probe_egress_port,
    require_selector_policy,
    reset_selector_policy_cache,
    selector_httpx_client,
    selector_openai_client,
)


class _RecordingHandler(BaseHTTPRequestHandler):
    requests: list[tuple[str, str]]
    response_status = 200
    response_body = b"ok"

    def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
        self.requests.append((self.command, self.path))
        if self.path == "/redirect-external":
            self.send_response(302)
            self.send_header("Location", "http://destination.example/final")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "http://redirect.example/start":
            self.send_response(302)
            self.send_header("Location", "http://destination.example/final")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "http://redirect.example/fail-start":
            self.send_response(302)
            self.send_header("Location", "http://destination.example/fail")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "http://destination.example/fail":
            self.send_error(503)
            return
        self.send_response(self.response_status)
        self.send_header("Content-Length", str(len(self.response_body)))
        self.end_headers()
        self.wfile.write(self.response_body)

    def do_CONNECT(self) -> None:  # noqa: N802 - stdlib callback name
        self.requests.append((self.command, self.path))
        self.send_error(502)

    def log_message(self, _format: str, *_args: object) -> None:
        return


@contextmanager
def _server(*, response_body: bytes = b"ok", response_status: int = 200) -> Iterator[tuple[str, list[tuple[str, str]]]]:
    requests: list[tuple[str, str]] = []
    handler = type(
        "RecordingHandler",
        (_RecordingHandler,),
        {"requests": requests, "response_body": response_body, "response_status": response_status},
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _policy(proxy: str) -> SelectorPolicy:
    port = int(proxy.rsplit(":", 1)[1])
    return policy_for_port(port)


@contextlib.contextmanager
def _refusing_port() -> Iterator[int]:
    """A port that reliably refuses: bound (so nothing else can take it), never listening."""

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    try:
        yield int(sock.getsockname()[1])
    finally:
        sock.close()


def test_default_exit_port_is_the_pinned_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """59527, spelled out: the constant and the reader must be able to disagree.

    Comparing egress_proxy_port() against DEFAULT_EGRESS_PROXY_PORT would pass for
    any value of the constant, and would also fail on any machine configured the way
    the preflight's own error message tells operators to configure it.
    """

    monkeypatch.delenv(EGRESS_PROXY_PORT_ENV, raising=False)

    assert DEFAULT_EGRESS_PROXY_PORT == 59527
    assert egress_proxy_port() == 59527


def test_exit_port_is_overridable_for_a_differently_configured_router(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(EGRESS_PROXY_PORT_ENV, "7897")

    assert egress_proxy_port() == 7897


def test_blank_exit_port_falls_back_to_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EGRESS_PROXY_PORT_ENV, "   ")

    assert egress_proxy_port() == DEFAULT_EGRESS_PROXY_PORT


@pytest.mark.parametrize(("value", "message"), [("not-a-port", "integer"), ("0", "range"), ("70000", "range")])
def test_unusable_exit_port_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch, value: str, message: str
) -> None:
    monkeypatch.setenv(EGRESS_PROXY_PORT_ENV, value)

    with pytest.raises(EgressPreflightError, match=message):
        egress_proxy_port()


def test_probe_is_fail_closed_when_nothing_serves_the_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """The message must name the port, how to inspect it, and the override."""

    monkeypatch.delenv(EGRESS_PROBE_URL_ENV, raising=False)
    with _refusing_port() as dead:
        with pytest.raises(EgressPreflightError) as raised:
            probe_egress_port(dead)

    text = str(raised.value)
    assert str(dead) in text
    assert "lsof" in text
    assert EGRESS_PROXY_PORT_ENV in text


def test_probe_rejects_a_listener_that_is_not_a_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """The regression this probe exists for. A TCP connect cannot see it.

    On 2026-08-18 00:04 the round header read a healthy
    `=== egress proxy: http://127.0.0.1:59527 ===` while all 162 sources failed
    with `Connection refused`; CHANGELOG 2026-08-18 concluded that a port probe
    "区分不了「本地 listener 活着」与「上游隧道通」". A socket that accepts and
    then says nothing useful is exactly that state, and it must not pass.
    """

    monkeypatch.delenv(EGRESS_PROBE_URL_ENV, raising=False)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    try:
        port = int(listener.getsockname()[1])
        # A plain TCP connect against this succeeds -- assert that, so the test
        # states what it is discriminating against rather than implying it.
        with socket.create_connection(("127.0.0.1", port), timeout=5):
            pass
        with pytest.raises(EgressPreflightError, match="no request got through"):
            probe_egress_port(port)
    finally:
        listener.close()


def test_probe_passes_when_a_real_proxy_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Negative control for the two above: the probe is not simply always red.

    Also asserts the request actually traversed the proxy -- the stub records the
    absolute-form request URI that a forward proxy receives.
    """

    with _server() as (proxy_url, requests):
        monkeypatch.setenv(EGRESS_PROBE_URL_ENV, "http://egress.probe.invalid/ok")
        probe_egress_port(int(proxy_url.rsplit(":", 1)[1]))

    assert ("GET", "http://egress.probe.invalid/ok") in requests


def test_probe_goes_where_the_policy_points_not_where_it_guesses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The probe must derive its endpoint, not spell one of its own.

    Two independent f-strings for the same address drift silently: the probe
    then verifies an endpoint the transports never use, so it passes while
    every request fails and no downstream reading differs. Asking whether the
    two strings look alike cannot catch that -- this points the policy at a
    second server and requires the probe to follow it.
    """

    with _server() as (real_url, real_requests), _server() as (decoy_url, decoy_requests):
        real_port = int(real_url.rsplit(":", 1)[1])
        decoy_port = int(decoy_url.rsplit(":", 1)[1])
        monkeypatch.setenv(EGRESS_PROBE_URL_ENV, "http://egress.probe.invalid/ok")
        monkeypatch.setattr(
            "airadar.egress.policy_for_port", lambda _port: policy_for_port(real_port)
        )

        probe_egress_port(decoy_port)

    assert ("GET", "http://egress.probe.invalid/ok") in real_requests
    assert decoy_requests == []


def test_require_selector_policy_hands_out_the_port_it_probed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gate's whole job: probe port A and then hand out a policy for port A.

    Without this, probing 7897 while every transport proxies to 59527 passes the
    preflight, reports healthy, and fails every request.
    """

    reset_selector_policy_cache()
    try:
        with _server() as (proxy_url, requests):
            port = int(proxy_url.rsplit(":", 1)[1])
            monkeypatch.setenv(EGRESS_PROXY_PORT_ENV, str(port))
            monkeypatch.setenv(EGRESS_PROBE_URL_ENV, "http://egress.probe.invalid/ok")

            policy = require_selector_policy()

        assert policy.agent_proxy == f"http://127.0.0.1:{port}"
        assert policy == policy_for_port(port)
        assert ("GET", "http://egress.probe.invalid/ok") in requests
    finally:
        reset_selector_policy_cache()


def test_require_selector_policy_probes_before_handing_out_a_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A policy must never be issued for an exit that is not there."""

    reset_selector_policy_cache()
    try:
        monkeypatch.delenv(EGRESS_PROBE_URL_ENV, raising=False)
        with _refusing_port() as dead:
            monkeypatch.setenv(EGRESS_PROXY_PORT_ENV, str(dead))
            with pytest.raises(EgressPreflightError, match="no request got through"):
                require_selector_policy()
    finally:
        reset_selector_policy_cache()


def test_policy_identity_follows_the_exit_port() -> None:
    """The interpret receipt gate pins policy_sha256, so moving the exit must move it."""

    first = policy_for_port(59527)
    again = policy_for_port(59527)
    elsewhere = policy_for_port(7897)

    assert first == again
    assert first.agent_proxy == "http://127.0.0.1:59527"
    assert first.policy_id == EGRESS_POLICY_ID
    assert len(first.policy_sha256) == 64
    assert first.policy_sha256 != elsewhere.policy_sha256


@pytest.mark.parametrize(
    "hostname",
    [
        "api.anthropic.com",
        "api.openai.com",
        "chatgpt.com",
        "api.x.com",
        "ark.cn-beijing.volces.com",
        "api.deepseek.com",
        "feeds.example.org",
    ],
)
def test_external_httpx_matrix_uses_actual_selector_listener_despite_parent_gcp_env(
    hostname: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    with _server() as (selector_url, selector_requests):
        policy = _policy(selector_url)
        with selector_httpx_client(policy, callsite_id="test.httpx", timeout=2) as client:
            response = client.get(f"http://{hostname}/resource?secret=must-not-be-logged")

    assert response.status_code == 200
    assert selector_requests == [("GET", f"http://{hostname}/resource?secret=must-not-be-logged")]


def test_loopback_httpx_is_explicit_direct_and_does_not_touch_selector() -> None:
    with _server() as (selector_url, selector_requests), _server() as (origin_url, origin_requests):
        policy = _policy(selector_url)
        with selector_httpx_client(
            policy,
            callsite_id="test.loopback",
            request_url=origin_url,
            timeout=2,
        ) as client:
            response = client.get(f"{origin_url}/healthz")

    assert response.status_code == 200
    assert origin_requests == [("GET", "/healthz")]
    assert selector_requests == []


def test_loopback_httpx_does_not_require_selector_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "airadar.egress.require_selector_policy",
        lambda: (_ for _ in ()).throw(EgressPreflightError("selector unavailable")),
    )
    with _server() as (origin_url, origin_requests):
        with selector_httpx_client(
            callsite_id="test.loopback.no_status",
            request_url=origin_url,
            timeout=2,
        ) as client:
            response = client.get(f"{origin_url}/healthz")

    assert response.status_code == 200
    assert origin_requests == [("GET", "/healthz")]


def test_external_urllib_uses_selector_and_loopback_remains_direct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")
    with _server() as (selector_url, selector_requests), _server() as (origin_url, origin_requests):
        policy = _policy(selector_url)
        with open_external_url(
            "http://feeds.example.org/rss?token=hidden",
            policy=policy,
            callsite_id="test.urllib.external",
            timeout=2,
        ) as response:
            assert response.read() == b"ok"
        with open_external_url(
            f"{origin_url}/healthz",
            policy=policy,
            callsite_id="test.urllib.loopback",
            timeout=2,
        ) as response:
            assert response.read() == b"ok"

    assert selector_requests == [("GET", "http://feeds.example.org/rss?token=hidden")]
    assert origin_requests == [("GET", "/healthz")]


def test_loopback_urllib_redirect_cannot_escape_direct_opener() -> None:
    with _server() as (selector_url, selector_requests), _server() as (origin_url, origin_requests):
        policy = _policy(selector_url)
        with pytest.raises(EgressRouteBoundaryError):
            open_external_url(
                f"{origin_url}/redirect-external",
                policy=policy,
                callsite_id="test.urllib.loopback.redirect",
                timeout=2,
            )

    assert origin_requests == [("GET", "/redirect-external")]
    assert selector_requests == []


def test_external_urllib_redirect_audits_the_final_hostname() -> None:
    messages: list[str] = []

    class CaptureHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            messages.append(record.getMessage())

    logger = logging.getLogger("airadar.egress.audit")
    handler = CaptureHandler()
    logger.addHandler(handler)
    try:
        with _server() as (selector_url, selector_requests):
            with open_external_url(
                "http://redirect.example/start",
                policy=_policy(selector_url),
                callsite_id="test.urllib.redirect.audit",
                timeout=2,
            ) as response:
                assert response.read() == b"ok"
    finally:
        logger.removeHandler(handler)

    assert selector_requests == [
        ("GET", "http://redirect.example/start"),
        ("GET", "http://destination.example/final"),
    ]
    records = [json.loads(message) for message in messages]
    assert records[-1]["hostname"] == "destination.example"


def test_external_urllib_redirect_failure_audits_the_failing_hostname() -> None:
    messages: list[str] = []

    class CaptureHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            messages.append(record.getMessage())

    logger = logging.getLogger("airadar.egress.audit")
    handler = CaptureHandler()
    logger.addHandler(handler)
    try:
        with _server() as (selector_url, selector_requests):
            with pytest.raises(urllib.error.HTTPError, match="503"):
                open_external_url(
                    "http://redirect.example/fail-start",
                    policy=_policy(selector_url),
                    callsite_id="test.urllib.redirect.failure.audit",
                    timeout=2,
                )
    finally:
        logger.removeHandler(handler)

    assert selector_requests == [
        ("GET", "http://redirect.example/fail-start"),
        ("GET", "http://destination.example/fail"),
    ]
    records = [json.loads(message) for message in messages]
    assert records[-1]["hostname"] == "destination.example"


def test_loopback_urllib_does_not_require_selector_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "airadar.egress.require_selector_policy",
        lambda: (_ for _ in ()).throw(EgressPreflightError("selector unavailable")),
    )
    with _server() as (origin_url, origin_requests):
        with open_external_url(
            f"{origin_url}/healthz",
            callsite_id="test.urllib.loopback.no_status",
            timeout=2,
        ) as response:
            assert response.read() == b"ok"

    assert origin_requests == [("GET", "/healthz")]


def test_selector_refused_fails_closed_without_direct_retry() -> None:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    host, port = sock.getsockname()
    sock.close()
    policy = _policy(f"http://{host}:{port}")

    with selector_httpx_client(policy, callsite_id="test.refused", timeout=0.2) as client:
        with pytest.raises(httpx.ConnectError):
            client.get("http://feeds.example.org/rss")


def test_redirects_remain_on_selector_and_client_closes() -> None:
    with _server() as (selector_url, selector_requests):
        policy = _policy(selector_url)
        client = selector_httpx_client(
            policy,
            callsite_id="test.redirect",
            timeout=2,
            follow_redirects=True,
        )
        with client:
            response = client.get("http://redirect.example/start")

    assert response.status_code == 200
    assert selector_requests == [
        ("GET", "http://redirect.example/start"),
        ("GET", "http://destination.example/final"),
    ]
    assert client.is_closed


def test_loopback_redirect_cannot_escape_direct_client() -> None:
    with _server() as (selector_url, selector_requests), _server() as (origin_url, origin_requests):
        policy = _policy(selector_url)
        with selector_httpx_client(
            policy,
            callsite_id="test.loopback.redirect",
            request_url=origin_url,
            timeout=2,
            follow_redirects=True,
        ) as client:
            with pytest.raises(EgressRouteBoundaryError):
                client.get(f"{origin_url}/redirect-external")

    assert origin_requests == [("GET", "/redirect-external")]
    assert selector_requests == []


def test_openai_connection_retry_never_falls_back_direct() -> None:
    with _server() as (selector_url, selector_requests):
        policy = _policy(selector_url)
        client = selector_openai_client(
            policy,
            callsite_id="test.openai.retry",
            api_key="test-key",
            max_retries=2,
            timeout=0.2,
        )
        try:
            with pytest.raises(openai.APIConnectionError):
                client.models.list()
        finally:
            client.close()

    assert selector_requests == [("CONNECT", "api.openai.com:443")] * 3


def test_loopback_openai_does_not_require_selector_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "airadar.egress.require_selector_policy",
        lambda: (_ for _ in ()).throw(EgressPreflightError("selector unavailable")),
    )
    with _server(response_body=b'{"object":"list","data":[]}') as (origin_url, origin_requests):
        client = selector_openai_client(
            callsite_id="test.openai.loopback",
            api_key="test-key",
            base_url=f"{origin_url}/v1",
            max_retries=0,
            timeout=1,
        )
        try:
            response = client.models.list()
        finally:
            client.close()

    assert response.data == []
    assert origin_requests == [("GET", "/v1/models")]


@pytest.mark.parametrize(
    ("base_url", "expected_authority"),
    [
        (None, "api.openai.com:443"),
        ("https://ark.cn-beijing.volces.com/api/v3", "ark.cn-beijing.volces.com:443"),
    ],
)
def test_openai_default_and_ark_base_url_reach_actual_selector_listener(
    base_url: str | None,
    expected_authority: str,
) -> None:
    with _server() as (selector_url, selector_requests):
        policy = _policy(selector_url)
        client = selector_openai_client(
            policy,
            callsite_id="test.openai",
            api_key="test-key",
            base_url=base_url,
            max_retries=0,
            timeout=0.5,
        )
        try:
            with pytest.raises(openai.APIConnectionError):
                client.models.list()
        finally:
            client.close()

    assert selector_requests == [("CONNECT", expected_authority)]
    assert client.is_closed()


def test_managed_subprocess_env_overwrites_all_six_ambient_proxy_vars() -> None:
    policy = _policy("http://selector.invalid:1")
    parent = {
        name: "http://127.0.0.1:9"
        for name in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")
    }
    parent.update({"NO_PROXY": "internal.example", "no_proxy": "other.example", "KEEP": "yes"})

    messages: list[str] = []

    class CaptureHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            messages.append(record.getMessage())

    logger = logging.getLogger("airadar.egress.audit")
    handler = CaptureHandler()
    logger.addHandler(handler)
    try:
        child = managed_subprocess_env(policy, parent, callsite_id="test.managed_subprocess")
    finally:
        logger.removeHandler(handler)

    for name in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        assert child[name] == policy.agent_proxy
    assert child["NO_PROXY"] == child["no_proxy"] == "localhost,127.0.0.1,::1"
    assert child["KEEP"] == "yes"
    record = json.loads(messages[-1])
    assert record["hostname"] is None
    assert record["launch"] == "managed-standard-env"
    assert record["local_outcome"] == "subprocess_env:prepared"


def test_direct_subprocess_env_removes_all_six_ambient_proxy_vars() -> None:
    parent = {
        name: "http://127.0.0.1:9"
        for name in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")
    }
    parent.update({"NO_PROXY": "internal.example", "no_proxy": "other.example", "KEEP": "yes"})

    child = direct_subprocess_env(parent)

    for name in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        assert name not in child
    assert child["NO_PROXY"] == child["no_proxy"] == "localhost,127.0.0.1,::1"
    assert child["KEEP"] == "yes"


def test_cli_preflight_reports_policy_identity_without_proxy_url(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    policy = _policy("http://selector.invalid:1")
    monkeypatch.setattr(cli, "require_selector_policy", lambda: policy)

    assert cli._egress_preflight() == 0

    output = capsys.readouterr().out
    assert "egress-preflight status=healthy" in output
    assert f"policy_id={EGRESS_POLICY_ID}" in output
    assert policy.policy_sha256 in output
    assert policy.agent_proxy not in output


def test_cli_preflight_failure_names_impact_and_next_action(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def unavailable() -> SelectorPolicy:
        raise EgressPreflightError("synthetic malformed status")

    monkeypatch.setattr(cli, "require_selector_policy", unavailable)

    assert cli._egress_preflight() == 1

    output = capsys.readouterr().out
    assert "status=unavailable" in output
    assert "no managed external pipeline stage was started" in output
    # Not pinned to a literal: what must hold is that the line tells the reader
    # what to do about the egress port, which is what the reason line names.
    assert "Next:" in output
    assert "listening on the egress port" in output
    assert "domain-routing selector" not in output


def test_playwright_proxy_split_is_explicit_for_external_and_loopback() -> None:
    policy = _policy("http://selector.invalid:1")

    assert playwright_launch_proxy(
        "https://mp.weixin.qq.com/",
        policy=policy,
        callsite_id="wechat.login",
    ) == {"server": policy.agent_proxy}
    assert (
        playwright_launch_proxy(
            "http://localhost/healthz",
            callsite_id="performance.local",
        )
        is None
    )


def test_playwright_audit_describes_proxy_preparation_not_navigation() -> None:
    policy = _policy("http://selector.invalid:1")
    messages: list[str] = []

    class CaptureHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            messages.append(record.getMessage())

    logger = logging.getLogger("airadar.egress.audit")
    handler = CaptureHandler()
    logger.addHandler(handler)
    try:
        playwright_launch_proxy(
            "https://playwright.external.invalid/probe",
            policy=policy,
            callsite_id="test.playwright.audit",
        )
    finally:
        logger.removeHandler(handler)

    records = [json.loads(message) for message in messages]
    assert records[-1]["local_outcome"] == "playwright_proxy_config:prepared"


def test_playwright_external_and_loopback_reach_the_selected_listener() -> None:
    playwright_api = pytest.importorskip("playwright.sync_api")
    chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if not chrome.is_file():
        pytest.skip("system Chrome is unavailable")

    with _server() as (selector_url, selector_requests), _server() as (origin_url, origin_requests):
        policy = _policy(selector_url)
        with playwright_api.sync_playwright() as playwright:
            external = playwright.chromium.launch(
                executable_path=str(chrome),
                headless=True,
                proxy=playwright_launch_proxy(
                    "http://playwright.external.invalid/probe",
                    policy=policy,
                    callsite_id="test.playwright.external",
                ),
            )
            try:
                page = external.new_page()
                page.goto("http://playwright.external.invalid/probe", wait_until="domcontentloaded")
            finally:
                external.close()

            local = playwright.chromium.launch(
                executable_path=str(chrome),
                headless=True,
                proxy=playwright_launch_proxy(
                    f"{origin_url}/probe",
                    policy=policy,
                    callsite_id="test.playwright.loopback",
                ),
                args=["--no-proxy-server"],
            )
            try:
                page = local.new_page()
                page.goto(f"{origin_url}/probe", wait_until="domcontentloaded")
            finally:
                local.close()

    assert ("GET", "http://playwright.external.invalid/probe") in selector_requests
    assert ("GET", "/probe") in origin_requests


def test_audit_json_excludes_sensitive_request_and_proxy_material() -> None:
    """Query strings and headers stay out; the proxy URL only *textually* so.

    Since 2026-09-09 `policy_sha256` is sha256 over the policy id and the exit
    URL, so its input space is the 65535 loopback ports -- publicly derivable
    and brute-forced in milliseconds. The `selector_url not in serialized`
    assertion below therefore holds by text, not by secrecy, and this test does
    not establish that the exit address is unrecoverable from an audit record.
    That is accepted rather than fixed: the address is loopback, and salting the
    digest would move it and invalidate the external interpret receipt again.
    Recorded in docs/issues/general.md.
    """

    messages: list[str] = []

    class CaptureHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            messages.append(record.getMessage())

    logger = logging.getLogger("airadar.egress.audit")
    handler = CaptureHandler()
    logger.addHandler(handler)
    try:
        with _server() as (selector_url, _requests):
            policy = _policy(selector_url)
            with selector_httpx_client(policy, callsite_id="fetch.feed", timeout=2) as client:
                client.get(
                    "http://api.anthropic.com/private/path?token=secret",
                    headers={"Authorization": "Bearer hidden"},
                )
    finally:
        logger.removeHandler(handler)

    records = [json.loads(message) for message in messages]
    assert records
    serialized = json.dumps(records, sort_keys=True)
    assert "fetch.feed" in serialized
    assert "api.anthropic.com" in serialized
    assert policy.policy_sha256 in serialized
    for secret in ("private", "token", "secret", "Authorization", "Bearer", selector_url):
        assert secret not in serialized
    assert set(records[-1]) == {
        "callsite_id",
        "hostname",
        "launch",
        "local_outcome",
        "policy_id",
        "policy_sha256",
    }
