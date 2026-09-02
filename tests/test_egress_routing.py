from __future__ import annotations

import json
import logging
import socket
import subprocess
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
    STATUS_COMMAND,
    EgressPreflightError,
    EgressRouteBoundaryError,
    SelectorPolicy,
    direct_subprocess_env,
    managed_subprocess_env,
    open_external_url,
    parse_proxy_status,
    playwright_launch_proxy,
    require_selector_policy,
    reset_selector_policy_cache,
    selector_httpx_client,
    selector_openai_client,
)

POLICY_SHA = "a" * 64


def _healthy_status(*, proxy: str) -> str:
    return "\n".join(
        (
            "stored_mode=domain-routing",
            "effective_mode=domain-routing",
            f"agent_proxy={proxy}",
            "status_schema_id=agent-domain-routing-status-v2",
            "policy_id=domain-routing-v2",
            f"policy_sha256={POLICY_SHA}",
            "policy_projection=matched",
            "router_status=running",
            "route_attribution=available",
            "gcp_sg_standard_status=healthy",
            "tencent_status=healthy",
            "tencent_status_scope=openai-provider-route-aggregate",
            "direct_status=healthy",
            "overall_status=healthy",
        )
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
    return parse_proxy_status(_healthy_status(proxy=proxy), expected_agent_proxy=proxy)


def test_parse_proxy_status_accepts_only_complete_healthy_contract() -> None:
    proxy = "http://selector.invalid:1"

    policy = parse_proxy_status(_healthy_status(proxy=proxy), expected_agent_proxy=proxy)

    assert policy == SelectorPolicy(
        agent_proxy=proxy,
        policy_id="domain-routing-v2",
        policy_sha256=POLICY_SHA,
    )


def test_production_status_contract_requires_the_stable_selector_address() -> None:
    contract_proxy = "http://127.0.0.1:59521"

    assert parse_proxy_status(_healthy_status(proxy=contract_proxy)).agent_proxy == contract_proxy
    with pytest.raises(EgressPreflightError, match="agent_proxy"):
        parse_proxy_status(_healthy_status(proxy="http://selector.invalid:1"))


def test_status_command_loads_the_shell_function_without_interactive_startup_output() -> None:
    assert STATUS_COMMAND == (
        "/bin/zsh",
        "-fc",
        'source "$HOME/.zshrc" >/dev/null 2>&1; check-proxy-status --format=kv',
    )


def test_status_command_failure_is_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "airadar.egress._run_status",
        lambda _command: subprocess.CompletedProcess([], 7, stdout="", stderr="unhealthy"),
    )
    reset_selector_policy_cache()
    try:
        with pytest.raises(EgressPreflightError, match="returned 7"):
            require_selector_policy()
    finally:
        reset_selector_policy_cache()


def test_missing_status_command_is_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(_command: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("synthetic missing status command")

    monkeypatch.setattr("airadar.egress._run_status", missing)
    reset_selector_policy_cache()
    try:
        with pytest.raises(EgressPreflightError, match="FileNotFoundError"):
            require_selector_policy()
    finally:
        reset_selector_policy_cache()


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda rows: [row for row in rows if not row.startswith("policy_id=")], "missing"),
        (lambda rows: [row for row in rows if not row.startswith("status_schema_id=")], "missing"),
        (lambda rows: [row for row in rows if not row.startswith("tencent_status_scope=")], "missing"),
        (lambda rows: [*rows, "policy_id=domain-routing-v2"], "duplicate"),
        (lambda rows: [*rows, "not-kv"], "malformed"),
        (
            lambda rows: ["overall_status=degraded" if row.startswith("overall_status=") else row for row in rows],
            "overall_status",
        ),
        (
            lambda rows: [
                "policy_projection=mismatch" if row.startswith("policy_projection=") else row for row in rows
            ],
            "policy_projection",
        ),
        (
            lambda rows: ["policy_sha256=ABC" if row.startswith("policy_sha256=") else row for row in rows],
            "policy_sha256",
        ),
        (
            lambda rows: [
                "status_schema_id=agent-domain-routing-status-v1"
                if row.startswith("status_schema_id=")
                else row
                for row in rows
            ],
            "status_schema_id",
        ),
        (
            lambda rows: [
                "tencent_status_scope=tencent-primary-only"
                if row.startswith("tencent_status_scope=")
                else row
                for row in rows
            ],
            "tencent_status_scope",
        ),
    ],
)
def test_parse_proxy_status_rejects_missing_duplicate_malformed_and_nonhealthy(
    mutation: object,
    message: str,
) -> None:
    proxy = "http://selector.invalid:1"
    rows = _healthy_status(proxy=proxy).splitlines()

    with pytest.raises(EgressPreflightError, match=message):
        parse_proxy_status("\n".join(mutation(rows)), expected_agent_proxy=proxy)  # type: ignore[operator]


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
    assert "policy_id=domain-routing-v2" in output
    assert POLICY_SHA in output
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
    assert "restore a healthy domain-routing selector" in output


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
    assert POLICY_SHA in serialized
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
