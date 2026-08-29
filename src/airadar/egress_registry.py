"""Exact checked-in network/subprocess callsite closure for the T2 egress boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

RouteContract = Literal[
    "compatibility-wrapper",
    "independent-adr057",
    "local-git-process",
    "local-process",
    "local-test-process",
    "local-worker-process",
    "loopback-explicit-direct",
    "managed-direct-env",
    "managed-standard-env",
    "selector-implementation",
    "selector-or-loopback",
    "selector-owned",
    "split-selector-loopback",
    "status-interface",
]

ROUTE_CONTRACTS = frozenset(get_args(RouteContract))


@dataclass(frozen=True, slots=True)
class Callsite:
    path: str
    callee: str
    count: int
    route_contract: RouteContract


@dataclass(frozen=True, slots=True)
class AuditCallsite:
    path: str
    callsite_id: str
    count: int = 1


CALLSITE_REGISTRY = (
    Callsite("src/airadar/admin/alerts.py", "direct_subprocess_env", 2, "managed-direct-env"),
    Callsite("src/airadar/admin/alerts.py", "selector_httpx_client", 1, "selector-owned"),
    Callsite("src/airadar/admin/alerts.py", "subprocess.run", 2, "managed-standard-env"),
    Callsite("src/airadar/admin/cost_report.py", "direct_subprocess_env", 1, "managed-direct-env"),
    Callsite("src/airadar/admin/cost_report.py", "subprocess.run", 1, "managed-standard-env"),
    Callsite("src/airadar/admin/performance.py", "subprocess.run", 1, "local-process"),
    Callsite("src/airadar/admin/x_media_backfill.py", "selector_httpx_client", 1, "selector-owned"),
    Callsite("src/airadar/egress.py", "OpenAI", 1, "selector-implementation"),
    Callsite("src/airadar/egress.py", "selector_httpx_client", 1, "selector-implementation"),
    Callsite("src/airadar/egress.py", "subprocess.run", 1, "status-interface"),
    Callsite("src/airadar/egress.py", "urllib.request.build_opener", 1, "selector-implementation"),
    Callsite("src/airadar/eval/aihot_dataset.py", "selector_httpx_client", 1, "selector-owned"),
    Callsite("src/airadar/eval/aihot_dataset.py", "subprocess.run", 3, "local-git-process"),
    Callsite("src/airadar/eval/judge.py", "selector_httpx_client", 1, "selector-owned"),
    Callsite("src/airadar/fetcher/http_client.py", "selector_httpx_client", 1, "selector-owned"),
    Callsite("src/airadar/fetcher/wechat.py", "playwright_launch_proxy", 1, "selector-owned"),
    Callsite("src/airadar/fetcher/wechat.py", "self.playwright.chromium.launch", 1, "selector-owned"),
    Callsite("src/airadar/fetcher/wechat.py", "sync_playwright", 1, "selector-owned"),
    Callsite("src/airadar/fetcher/x_api.py", "selector_httpx_client", 1, "selector-owned"),
    Callsite("src/airadar/interpret/runner.py", "managed_subprocess_env", 1, "managed-standard-env"),
    Callsite("src/airadar/interpret/runner.py", "subprocess.run", 1, "managed-standard-env"),
    Callsite("src/airadar/performance/browser_probe.py", "playwright.chromium.launch", 2, "split-selector-loopback"),
    Callsite("src/airadar/performance/browser_probe.py", "playwright_launch_proxy", 1, "selector-owned"),
    Callsite("src/airadar/performance/browser_probe.py", "subprocess.run", 1, "local-process"),
    Callsite("src/airadar/performance/browser_probe.py", "sync_playwright", 2, "split-selector-loopback"),
    Callsite("src/airadar/performance/context.py", "subprocess.run", 4, "local-process"),
    Callsite("src/airadar/performance/http_probe.py", "open_external_url", 1, "selector-or-loopback"),
    Callsite("src/airadar/performance/journey_monitor.py", "subprocess.Popen", 2, "local-worker-process"),
    Callsite("src/airadar/performance/journey_monitor.py", "subprocess.run", 4, "local-process"),
    Callsite("src/airadar/performance/remediation.py", "subprocess.Popen", 1, "local-worker-process"),
    Callsite("src/airadar/performance/remediation.py", "subprocess.run", 5, "local-process"),
    Callsite("src/airadar/performance/runner.py", "subprocess.run", 1, "local-git-process"),
    Callsite("src/airadar/performance/stage_ledger.py", "subprocess.run", 2, "local-process"),
    Callsite("src/airadar/pricing.py", "open_external_url", 1, "selector-owned"),
    Callsite("src/airadar/provider/codex_gpt_mini.py", "OpenAI", 1, "selector-owned"),
    Callsite("src/airadar/provider/deepseek_chat.py", "OpenAI", 1, "selector-owned"),
    Callsite("src/airadar/web/routes/media.py", "httpx.Client", 1, "independent-adr057"),
    Callsite(
        "src/airadar/wechat_discovery/login.py", "playwright.chromium.launch_persistent_context", 1, "selector-owned"
    ),
    Callsite("src/airadar/wechat_discovery/login.py", "playwright_launch_proxy", 1, "selector-owned"),
    Callsite("src/airadar/wechat_discovery/login.py", "sync_playwright", 1, "selector-owned"),
    Callsite("src/airadar/wechat_discovery/protocol.py", "open_external_url", 1, "selector-owned"),
    Callsite("scripts/audit_aihot_sources.py", "selector_httpx_client", 1, "selector-owned"),
    Callsite("scripts/generate_x_offline_proof.py", "subprocess.run", 1, "local-test-process"),
    Callsite("scripts/rewrite_nitter_urls.py", "open_external_url", 1, "selector-owned"),
    Callsite("scripts/verify_admin_metrics.py", "open_external_url", 1, "selector-or-loopback"),
    Callsite("scripts/verify_admin_metrics.py", "urlopen", 1, "compatibility-wrapper"),
    Callsite("scripts/web_contract_golden.py", "open_external_url", 1, "selector-or-loopback"),
    Callsite("scripts/web_contract_golden.py", "urlopen", 1, "compatibility-wrapper"),
    Callsite("tests/playwright/conftest.py", "playwright.chromium.launch", 1, "loopback-explicit-direct"),
    Callsite("tests/playwright/conftest.py", "subprocess.Popen", 1, "local-test-process"),
    Callsite("tests/playwright/conftest.py", "sync_playwright", 1, "loopback-explicit-direct"),
    Callsite("tests/playwright/conftest.py", "urllib.request.urlopen", 2, "loopback-explicit-direct"),
    Callsite("tests/playwright/test_aihot_source_alignment.py", "httpx.Client", 2, "loopback-explicit-direct"),
    Callsite("tests/playwright/test_aihot_source_alignment.py", "subprocess.Popen", 1, "local-test-process"),
)


AUDIT_CALLSITE_REGISTRY = (
    AuditCallsite("src/airadar/admin/alerts.py", "admin.alerts.healthz"),
    AuditCallsite("src/airadar/admin/x_media_backfill.py", "admin.x_media_backfill"),
    AuditCallsite("src/airadar/eval/aihot_dataset.py", "eval.aihot_dataset.capture"),
    AuditCallsite("src/airadar/eval/judge.py", "eval.judge.deepseek"),
    AuditCallsite("src/airadar/fetcher/http_client.py", "fetcher.http_client.fetch_document"),
    AuditCallsite("src/airadar/fetcher/wechat.py", "fetcher.wechat.browser"),
    AuditCallsite("src/airadar/fetcher/x_api.py", "fetcher.x_api.fetch_x_timeline"),
    AuditCallsite("src/airadar/interpret/runner.py", "interpret.runner.check_url"),
    AuditCallsite("src/airadar/interpret/runner.py", "interpret.runner.summarize", 2),
    AuditCallsite("src/airadar/interpret/runner.py", "interpret.runner.save_from_batch"),
    AuditCallsite("src/airadar/interpret/runner.py", "interpret.runner.save_from_batch_retry"),
    AuditCallsite("src/airadar/performance/browser_probe.py", "performance.browser_probe.journey"),
    AuditCallsite("src/airadar/performance/http_probe.py", "performance.http_probe.measure"),
    AuditCallsite("src/airadar/pricing.py", "pricing.fetch_litellm"),
    AuditCallsite("src/airadar/provider/codex_gpt_mini.py", "provider.codex_gpt_mini.score"),
    AuditCallsite("src/airadar/provider/deepseek_chat.py", "provider.deepseek_chat.chat_json"),
    AuditCallsite("src/airadar/wechat_discovery/login.py", "wechat_discovery.login.browser"),
    AuditCallsite("src/airadar/wechat_discovery/protocol.py", "wechat_discovery.protocol.request_json"),
    AuditCallsite("scripts/audit_aihot_sources.py", "scripts.audit_aihot_sources"),
    AuditCallsite("scripts/rewrite_nitter_urls.py", "scripts.rewrite_nitter_urls.probe"),
    AuditCallsite("scripts/verify_admin_metrics.py", "scripts.verify_admin_metrics"),
    AuditCallsite("scripts/web_contract_golden.py", "scripts.web_contract_golden"),
)
