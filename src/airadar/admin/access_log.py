from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlsplit

ACCESS_LOG_RE = re.compile(
    r'^(?:(?P<timestamp>\d{4}-\d{2}-\d{2}[T ][^\s]+)\s+)?\S+:\s+(?P<client>.+?)\s+-\s+"(?P<method>[A-Z]+)\s+(?P<path>\S+)\s+HTTP/(?P<http_version>[^"]+)"\s+(?P<status>\d{3})(?:\s+(?P<tail>.*))?$'
)
USER_AGENT_TAIL_RE = re.compile(r'.*"(?P<user_agent>[^"]*)"\s*$')
BOT_USER_AGENT_RE = re.compile(r"(bot|crawler|spider|slurp|bytespider|headless|monitoring)", re.IGNORECASE)
BOT_PATH_PREFIXES = (
    "/.env",
    "/.git",
    "/api/.env",
    "/wp-",
    "/wordpress",
    "/phpmyadmin",
)
BOT_PATHS = {
    "/robots.txt",
    "/xmlrpc.php",
}
STATIC_SUFFIXES = (
    ".css",
    ".js",
    ".ico",
    ".png",
    ".jpg",
    ".jpeg",
    ".svg",
    ".webp",
    ".woff",
    ".woff2",
    ".map",
)
PUBLIC_PATHS = {"/", "/all", "/about", "/api/v1/curated", "/api/v1/timeline", "/api/v1/sources", "/api/v1/healthz"}
PUBLIC_PATH_PREFIXES = ("/daily", "/api/v1/items/")
SCANNER_PATH_TOKENS = (
    "/.env",
    "/.git",
    "wp-includes/",
    "phpinfo",
    "credentials",
    "service-account",
    "client_secret",
    "secrets",
)
SCANNER_SUFFIXES = (".php", ".sql", ".ini", ".yml", ".yaml", ".tfstate", ".tfvars")
KNOWN_SCANNER_IPS = {
    "103.253.203.69",
    "209.87.169.97",
}


@dataclass(frozen=True)
class AccessLogEntry:
    ip: str
    method: str
    path: str
    status: int
    user_agent: str | None = None
    timestamp: datetime | None = None


@dataclass(frozen=True)
class AccessLogSummary:
    pv: int
    uv: int
    raw_unique_ips: int
    filtered_ip_count: int
    bot_requests: int
    top_pages: list[tuple[str, int]] = field(default_factory=list)
    status_counts: dict[int, int] = field(default_factory=dict)
    status_class_counts: dict[str, int] = field(default_factory=dict)


def strip_client_port(client: str) -> str:
    host, separator, port = client.rpartition(":")
    if separator and port.isdigit():
        return host
    return client


def parse_access_log_line(line: str) -> AccessLogEntry | None:
    match = ACCESS_LOG_RE.match(line.strip())
    if match is None:
        return None
    tail = match.group("tail") or ""
    user_agent_match = USER_AGENT_TAIL_RE.match(tail)
    raw_timestamp = match.group("timestamp")
    return AccessLogEntry(
        ip=strip_client_port(match.group("client")),
        method=match.group("method"),
        path=match.group("path"),
        status=int(match.group("status")),
        user_agent=user_agent_match.group("user_agent") if user_agent_match else None,
        timestamp=datetime.fromisoformat(raw_timestamp) if raw_timestamp else None,
    )


def normalize_page_path(path: str) -> str:
    parsed = urlsplit(path)
    return parsed.path or "/"


def is_bot_request(entry: AccessLogEntry) -> bool:
    path = normalize_page_path(entry.path).lower()
    user_agent = entry.user_agent or ""
    if entry.ip in KNOWN_SCANNER_IPS:
        return True
    if BOT_USER_AGENT_RE.search(user_agent):
        return True
    if path in BOT_PATHS:
        return True
    if any(path.startswith(prefix) for prefix in BOT_PATH_PREFIXES):
        return True
    if path.endswith(STATIC_SUFFIXES):
        return True
    if any(token in path for token in SCANNER_PATH_TOKENS):
        return True
    if path.endswith(SCANNER_SUFFIXES):
        return True
    return path not in PUBLIC_PATHS and not any(path.startswith(prefix) for prefix in PUBLIC_PATH_PREFIXES)


def aggregate_access_log(lines: Iterable[str]) -> AccessLogSummary:
    raw_ips: set[str] = set()
    filtered_ips: set[str] = set()
    page_counts: Counter[str] = Counter()
    status_counts: Counter[int] = Counter()
    status_class_counts: Counter[str] = Counter()
    bot_requests = 0
    pv = 0

    for line in lines:
        entry = parse_access_log_line(line)
        if entry is None:
            continue
        raw_ips.add(entry.ip)
        if is_bot_request(entry):
            bot_requests += 1
            continue
        pv += 1
        filtered_ips.add(entry.ip)
        page_counts[normalize_page_path(entry.path)] += 1
        status_counts[entry.status] += 1
        status_class_counts[f"{entry.status // 100}xx"] += 1

    return AccessLogSummary(
        pv=pv,
        uv=len(filtered_ips),
        raw_unique_ips=len(raw_ips),
        filtered_ip_count=len(raw_ips - filtered_ips),
        bot_requests=bot_requests,
        top_pages=sorted(page_counts.items(), key=lambda item: (-item[1], item[0])),
        status_counts=dict(sorted(status_counts.items())),
        status_class_counts=dict(sorted(status_class_counts.items())),
    )
