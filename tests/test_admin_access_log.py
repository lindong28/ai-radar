from __future__ import annotations

from airadar.admin.access_log import AccessLogEntry, aggregate_access_log, is_bot_request, parse_access_log_line


def test_parse_access_log_line_strips_public_ipv4_zero_port() -> None:
    line = 'INFO:     82.152.91.79:0 - "GET /__task001_ip_probe HTTP/1.1" 404 Not Found'

    entry = parse_access_log_line(line)

    assert entry is not None
    assert entry.ip == "82.152.91.79"
    assert entry.method == "GET"
    assert entry.path == "/__task001_ip_probe"
    assert entry.status == 404


def test_parse_access_log_line_strips_ipv6_zero_port() -> None:
    line = 'INFO:     2a09:bac5:6d49:aa::11:17f:0 - "GET /.git/config HTTP/1.1" 404 Not Found'

    entry = parse_access_log_line(line)

    assert entry is not None
    assert entry.ip == "2a09:bac5:6d49:aa::11:17f"
    assert entry.path == "/.git/config"
    assert entry.status == 404


def test_parse_access_log_line_strips_local_ephemeral_port() -> None:
    line = 'INFO:     127.0.0.1:65001 - "GET /__task001_local_probe HTTP/1.1" 404 Not Found'

    entry = parse_access_log_line(line)

    assert entry is not None
    assert entry.ip == "127.0.0.1"
    assert entry.path == "/__task001_local_probe"
    assert entry.status == 404


def test_parse_access_log_line_accepts_timestamped_persistent_format() -> None:
    line = '2026-06-02T08:00:00+08:00 INFO:     203.0.113.9:0 - "GET /api/v1/healthz HTTP/1.1" 200 OK "curl/8.7"'

    entry = parse_access_log_line(line)

    assert entry is not None
    assert entry.timestamp is not None
    assert entry.timestamp.isoformat() == "2026-06-02T08:00:00+08:00"
    assert entry.ip == "203.0.113.9"
    assert entry.user_agent == "curl/8.7"


def test_parse_access_log_line_accepts_uvicorn_launchd_timestamp_format() -> None:
    line = '2026-06-02T08:22:26+0800 INFO:     127.0.0.1:49695 - "GET /api/v1/healthz HTTP/1.1" 200 OK'

    entry = parse_access_log_line(line)

    assert entry is not None
    assert entry.timestamp is not None
    assert entry.timestamp.isoformat() == "2026-06-02T08:22:26+08:00"
    assert entry.ip == "127.0.0.1"


def test_parse_access_log_line_accepts_machine_readable_duration() -> None:
    line = (
        '2026-06-02T08:00:00+08:00 INFO:     203.0.113.9:0 - '
        '"GET /wechat/fixture-slug HTTP/1.1" 200 OK duration_ms=12.75 "Mozilla/5.0"'
    )

    entry = parse_access_log_line(line)

    assert entry is not None
    assert entry.duration_ms == 12.75


def test_wechat_journey_paths_are_public_metrics_paths() -> None:
    for path in ("/wechat", "/api/v1/wechat?page=2", "/wechat/fixture-slug"):
        entry = AccessLogEntry(ip="203.0.113.9", method="GET", path=path, status=200)
        assert is_bot_request(entry) is False


def test_wechat_api_route_matching_is_segment_aware() -> None:
    secret = AccessLogEntry(ip="203.0.113.9", method="GET", path="/api/v1/wechat-secret", status=200)

    assert is_bot_request(secret) is True


def test_unwired_public_timing_records_are_ignored() -> None:
    lines = [
        'INFO:     203.0.113.9:0 - "GET /wechat HTTP/1.1" 200 OK "Mozilla/5.0"',
        "INFO airadar.public-path: method=GET path=/wechat status=200 duration_ms=12.5",
    ]

    summary = aggregate_access_log(lines)

    assert summary.pv == 1
    assert summary.path_duration_ms == {}


def test_aggregate_access_log_filters_bots_scanners_and_static_assets() -> None:
    lines = [
        'INFO:     82.152.91.79:0 - "GET / HTTP/1.1" 200 OK "Mozilla/5.0"',
        'INFO:     82.152.91.79:0 - "GET /all?channel=news HTTP/1.1" 200 OK "Mozilla/5.0"',
        'INFO:     203.0.113.7:0 - "GET /daily HTTP/1.1" 500 Internal Server Error "Mozilla/5.0"',
        'INFO:     66.249.66.1:0 - "GET / HTTP/1.1" 200 OK "Googlebot/2.1"',
        'INFO:     51.68.111.219:0 - "GET /robots.txt HTTP/1.1" 404 Not Found "curl/8.0"',
        'INFO:     209.87.169.97:0 - "GET /wp-includes/css/buttons.css HTTP/1.1" 404 Not Found "Mozilla/5.0"',
        'INFO:     2a09:bac5:6d49:aa::11:17f:0 - "GET /.git/config HTTP/1.1" 404 Not Found "Mozilla/5.0"',
        'INFO:     82.152.91.79:0 - "GET /style.css?v=20260515-ux5 HTTP/1.1" 200 OK "Mozilla/5.0"',
        'INFO:     198.51.100.8:0 - "GET /api/.env HTTP/1.1" 404 Not Found "Mozilla/5.0"',
        'INFO:     198.51.100.9:0 - "GET /login HTTP/1.1" 404 Not Found "Mozilla/5.0"',
        'INFO:     198.51.100.10:0 - "GET /blog/wp-includes/wlwmanifest.xml HTTP/1.1" 404 Not Found "Mozilla/5.0"',
        'INFO:     198.51.100.11:0 - "GET /service-account.json HTTP/1.1" 404 Not Found "Mozilla/5.0"',
        'INFO:     198.51.100.12:0 - "GET /api/v1/items/abc123 HTTP/1.1" 200 OK "Mozilla/5.0"',
    ]

    summary = aggregate_access_log(lines)

    assert summary.pv == 4
    assert summary.uv == 3
    assert summary.raw_unique_ips == 11
    assert summary.filtered_ip_count == 8
    assert summary.bot_requests == 9
    assert summary.top_pages == [("/", 1), ("/all", 1), ("/api/v1/items/abc123", 1), ("/daily", 1)]
    assert summary.status_counts == {200: 3, 500: 1}
    assert summary.status_class_counts == {"2xx": 3, "5xx": 1}
