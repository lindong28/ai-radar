from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

from airadar.egress_registry import AUDIT_CALLSITE_REGISTRY, CALLSITE_REGISTRY, ROUTE_CONTRACTS

ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOTS = (ROOT / "src" / "airadar", ROOT / "scripts", ROOT / "tests" / "playwright")
NETWORK_CALLEES = {
    "OpenAI",
    "direct_subprocess_env",
    "managed_subprocess_env",
    "open_external_url",
    "playwright_launch_proxy",
    "selector_httpx_client",
    "selector_openai_client",
    "urlopen",
}
NETWORK_API_PREFIXES = {
    "httpx": {
        "Client",
        "AsyncClient",
        "delete",
        "get",
        "head",
        "options",
        "patch",
        "post",
        "put",
        "request",
        "stream",
    },
    "subprocess": {"Popen", "call", "check_call", "check_output", "run"},
    "urllib.request": {"build_opener", "urlopen", "urlretrieve"},
}

HELPER_ROUTE_CONTRACTS = {
    "direct_subprocess_env": {"managed-direct-env"},
    "managed_subprocess_env": {"managed-standard-env"},
    "open_external_url": {"selector-or-loopback", "selector-owned"},
    "playwright_launch_proxy": {"selector-owned"},
    "selector_httpx_client": {"selector-implementation", "selector-owned"},
}


def _qualified_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _qualified_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _import_aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for name in node.names:
                bound_name = name.asname or name.name.split(".")[0]
                aliases[bound_name] = name.name if name.asname else bound_name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for name in node.names:
                aliases[name.asname or name.name] = f"{node.module}.{name.name}"
    return aliases


def _canonical_name(raw: str, aliases: dict[str, str]) -> str:
    head, separator, tail = raw.partition(".")
    resolved = aliases.get(head, head)
    return f"{resolved}.{tail}" if separator else resolved


def _is_network_callee(raw: str, canonical: str) -> bool:
    if raw in NETWORK_CALLEES:
        return True
    if canonical == "openai.OpenAI" or canonical == "playwright.sync_api.sync_playwright":
        return True
    for prefix, methods in NETWORK_API_PREFIXES.items():
        if canonical in {f"{prefix}.{method}" for method in methods}:
            return True
    return canonical.endswith(".launch") or canonical.endswith(".launch_persistent_context")


def _observed_callsites() -> Counter[tuple[str, str]]:
    observed: Counter[tuple[str, str]] = Counter()
    for root in SCAN_ROOTS:
        for path in root.rglob("*.py"):
            relative = str(path.relative_to(ROOT))
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
            aliases = _import_aliases(tree)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                callee = _qualified_name(node.func)
                canonical = _canonical_name(callee, aliases)
                if _is_network_callee(callee, canonical):
                    observed[(relative, callee)] += 1
    return observed


def test_checked_in_network_callsites_match_classified_registry_exactly() -> None:
    expected = Counter({(entry.path, entry.callee): entry.count for entry in CALLSITE_REGISTRY})

    assert _observed_callsites() == expected
    assert all(entry.route_contract in ROUTE_CONTRACTS for entry in CALLSITE_REGISTRY)
    for entry in CALLSITE_REGISTRY:
        if entry.callee in HELPER_ROUTE_CONTRACTS:
            assert entry.route_contract in HELPER_ROUTE_CONTRACTS[entry.callee]


def test_network_entrypoint_detection_resolves_import_aliases() -> None:
    tree = ast.parse(
        "from httpx import Client as H\n"
        "from subprocess import check_output as execute\n"
        "from urllib.request import urlopen as fetch\n"
        "from openai import OpenAI as ModelClient\n"
        "H(); execute([]); fetch('https://example.test'); ModelClient()\n"
    )
    aliases = _import_aliases(tree)
    observed = {
        _canonical_name(_qualified_name(node.func), aliases)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and _is_network_callee(
            _qualified_name(node.func),
            _canonical_name(_qualified_name(node.func), aliases),
        )
    }

    assert observed == {
        "httpx.Client",
        "openai.OpenAI",
        "subprocess.check_output",
        "urllib.request.urlopen",
    }


def _observed_audit_callsite_ids() -> Counter[tuple[str, str]]:
    observed: Counter[tuple[str, str]] = Counter()
    for root in SCAN_ROOTS:
        for path in root.rglob("*.py"):
            relative = str(path.relative_to(ROOT))
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                for keyword in node.keywords:
                    if (
                        keyword.arg == "callsite_id"
                        and isinstance(keyword.value, ast.Constant)
                        and isinstance(keyword.value.value, str)
                    ):
                        observed[(relative, keyword.value.value)] += 1
    return observed


def test_checked_in_audit_callsite_ids_match_registry_exactly() -> None:
    expected = Counter({(entry.path, entry.callsite_id): entry.count for entry in AUDIT_CALLSITE_REGISTRY})

    assert _observed_audit_callsite_ids() == expected
