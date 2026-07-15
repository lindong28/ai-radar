#!/usr/bin/env python3
"""Capture and verify the refactor golden baseline deterministically."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import struct
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class HttpSpec:
    artifact: str
    path: str
    kind: str


API_SPECS = (
    HttpSpec("timeline_page_1.json", "/api/v1/timeline?page=1", "api-json"),
    HttpSpec("timeline_page_2_channel_news.json", "/api/v1/timeline?page=2&channel=news", "api-json"),
    HttpSpec("timeline_page_99999.json", "/api/v1/timeline?page=99999", "api-json"),
    HttpSpec("timeline_q_Codex.json", "/api/v1/timeline?q=Codex", "api-json"),
    HttpSpec("timeline_category_ai-models.json", "/api/v1/timeline?category=ai-models", "api-json"),
    HttpSpec("timeline_category_ai-products.json", "/api/v1/timeline?category=ai-products", "api-json"),
    HttpSpec("timeline_category_industry.json", "/api/v1/timeline?category=industry", "api-json"),
    HttpSpec("timeline_category_paper.json", "/api/v1/timeline?category=paper", "api-json"),
    HttpSpec("timeline_category_tip.json", "/api/v1/timeline?category=tip", "api-json"),
    HttpSpec("curated.json", "/api/v1/curated", "api-json"),
    HttpSpec("curated_page_2.json", "/api/v1/curated?page=2", "api-json"),
    HttpSpec("curated_page_99999.json", "/api/v1/curated?page=99999", "api-json"),
    HttpSpec("curated_date_2026-07-14.json", "/api/v1/curated?date=2026-07-14", "api-json"),
    HttpSpec("curated_category_ai-models.json", "/api/v1/curated?category=ai-models", "api-json"),
    HttpSpec("curated_category_ai-products.json", "/api/v1/curated?category=ai-products", "api-json"),
    HttpSpec("curated_category_industry.json", "/api/v1/curated?category=industry", "api-json"),
    HttpSpec("curated_category_paper.json", "/api/v1/curated?category=paper", "api-json"),
    HttpSpec("curated_category_tip.json", "/api/v1/curated?category=tip", "api-json"),
    HttpSpec(
        "curated_run_id_20260714T090059Z-0c35.json",
        "/api/v1/curated?run_id=20260714T090059Z-0c35",
        "api-json",
    ),
    HttpSpec(
        "curated_run_id_20260528T230502Z-5719.json",
        "/api/v1/curated?run_id=20260528T230502Z-5719",
        "api-json",
    ),
    HttpSpec("curated_q_Codex.json", "/api/v1/curated?q=Codex", "api-json"),
    HttpSpec("wechat.json", "/api/v1/wechat", "api-json"),
    HttpSpec("wechat_q_AI.json", "/api/v1/wechat?q=AI", "api-json"),
    HttpSpec("wechat_page_99999.json", "/api/v1/wechat?page=99999", "api-json"),
    HttpSpec("items_a6b78843d641a9db.json", "/api/v1/items/a6b78843d641a9db", "api-json"),
    HttpSpec("sources.json", "/api/v1/sources", "api-json"),
)
SSR_SPECS = (
    HttpSpec("ssr_index_preload.json", "/", "ssr-preload-json"),
    HttpSpec("ssr_all_preload.json", "/all", "ssr-preload-json"),
    HttpSpec("ssr_wechat_preload.json", "/wechat", "ssr-preload-json"),
)
WECHAT_SLUG = "视频解说-skill-教你分清柯基与吐司面包"
WECHAT_TITLE = "视频解说 Skill：教你分清「柯基」与「吐司面包」"
HTML_SPECS = (
    HttpSpec("wechat_detail.html", f"/wechat/{quote(WECHAT_SLUG, safe='')}", "html"),
)
HTTP_SPECS = API_SPECS + SSR_SPECS + HTML_SPECS
PRELOAD_RE = re.compile(rb'<script id="__PRELOAD__"[^>]*>(.*?)</script>', re.S)


def _json_bytes(value: object, *, api: bool) -> bytes:
    if api:
        return (json.dumps(value, indent=4, ensure_ascii=True) + "\n").encode()
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True).encode()


def _fetch(base_url: str, spec: HttpSpec) -> tuple[HttpSpec, bytes, dict[str, object]]:
    url = f"{base_url.rstrip('/')}{spec.path}"
    request = Request(url, headers={"Accept": "application/json,text/html"})
    with urlopen(request, timeout=180) as response:  # noqa: S310 - fixed local test URL
        raw = response.read()
        status = response.status
        content_type = response.headers.get("Content-Type", "")
    if spec.kind == "api-json":
        artifact = _json_bytes(json.loads(raw), api=True)
    elif spec.kind == "ssr-preload-json":
        match = PRELOAD_RE.search(raw)
        if match is None:
            raise AssertionError(f"missing __PRELOAD__ in {url}")
        artifact = _json_bytes(json.loads(match.group(1)), api=False)
    else:
        artifact = raw
    metadata: dict[str, object] = {
        "artifact": spec.artifact,
        "kind": spec.kind,
        "url": url,
        "status": status,
        "content_type": content_type,
        "bytes": len(artifact),
        "sha256": hashlib.sha256(artifact).hexdigest(),
    }
    return spec, artifact, metadata


def capture(base_url: str, output: Path, concurrency: int) -> None:
    output.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        results = list(executor.map(lambda spec: _fetch(base_url, spec), HTTP_SPECS))
    entries = []
    for spec, artifact, metadata in results:
        (output / spec.artifact).write_bytes(artifact)
        entries.append(metadata)
    manifest = {"version": 1, "artifacts": sorted(entries, key=lambda entry: str(entry["artifact"]))}
    (output / "request-manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    )
    print(f"CAPTURE PASS http={len(results)} api={len(API_SPECS)} ssr={len(SSR_SPECS)} html={len(HTML_SPECS)}")


def _put(hasher: Any, value: object) -> None:
    if value is None:
        tag, raw = b"n", b""
    elif isinstance(value, bytes):
        tag, raw = b"b", value
    elif isinstance(value, str):
        tag, raw = b"s", value.encode()
    elif isinstance(value, int):
        tag, raw = b"i", str(value).encode()
    elif isinstance(value, float):
        tag, raw = b"f", struct.pack(">d", value)
    else:
        raise TypeError(f"unsupported SQLite value: {type(value)!r}")
    hasher.update(tag)
    hasher.update(len(raw).to_bytes(8, "big"))
    hasher.update(raw)


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _rows(conn: sqlite3.Connection, table: str, columns: list[str]) -> Iterable[tuple[object, ...]]:
    table_sql = _quote_identifier(table)
    column_sql = ", ".join(_quote_identifier(column) for column in columns)
    try:
        conn.execute(f"SELECT rowid FROM {table_sql} LIMIT 0")
    except sqlite3.OperationalError:
        info = conn.execute(f"PRAGMA table_xinfo({table_sql})").fetchall()
        primary_key = [row[1] for row in sorted(info, key=lambda row: row[5]) if row[5]]
        order = primary_key or columns
        order_sql = ", ".join(_quote_identifier(column) for column in order)
        query = f"SELECT {column_sql} FROM {table_sql} ORDER BY {order_sql}"
    else:
        query = f"SELECT rowid, {column_sql} FROM {table_sql} ORDER BY rowid"
    yield from conn.execute(query)


def logical_db_invariant(db_path: Path) -> dict[str, object]:
    wal = Path(f"{db_path}-wal")
    if wal.exists() and wal.stat().st_size:
        raise RuntimeError(f"refusing to digest database with WAL present: {wal}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA query_only=ON")
        schema_rows = conn.execute(
            "SELECT type, name, tbl_name, COALESCE(sql, '') FROM sqlite_schema "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
        schema_hasher = hashlib.sha256()
        for row in schema_rows:
            for value in row:
                _put(schema_hasher, value)
        table_names = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        tables: dict[str, object] = {}
        for table in table_names:
            info = conn.execute(f"PRAGMA table_xinfo({_quote_identifier(table)})").fetchall()
            columns = [row[1] for row in info if row[6] == 0]
            hasher = hashlib.sha256()
            row_count = 0
            for row in _rows(conn, table, columns):
                for value in row:
                    _put(hasher, value)
                row_count += 1
            tables[table] = {"columns": columns, "rows": row_count, "sha256": hasher.hexdigest()}
    finally:
        conn.close()
    invariant: dict[str, object] = {
        "algorithm": "sha256-length-prefixed-sqlite-logical-v1",
        "schema_sha256": schema_hasher.hexdigest(),
        "tables": tables,
    }
    canonical = json.dumps(invariant, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    invariant["overall_sha256"] = hashlib.sha256(canonical).hexdigest()
    return invariant


def record_db(db_path: Path, output: Path) -> None:
    invariant = logical_db_invariant(db_path)
    output.write_text(json.dumps(invariant, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    print(
        f"DB RECORD PASS tables={len(invariant['tables'])} "
        f"digest={invariant['overall_sha256']}"
    )


def _manifest_by_artifact(directory: Path) -> dict[str, dict[str, object]]:
    manifest = json.loads((directory / "request-manifest.json").read_text())
    assert manifest["version"] == 1
    entries = {entry["artifact"]: entry for entry in manifest["artifacts"]}
    assert set(entries) == {spec.artifact for spec in HTTP_SPECS}
    for artifact, entry in entries.items():
        raw = (directory / artifact).read_bytes()
        assert entry["bytes"] == len(raw), artifact
        assert entry["sha256"] == hashlib.sha256(raw).hexdigest(), artifact
        assert entry["status"] == 200, artifact
        expected_type = "application/json" if entry["kind"] == "api-json" else "text/html; charset=utf-8"
        assert entry["content_type"] == expected_type, artifact
    return entries


def _assert_nonempty(directory: Path) -> None:
    for spec in API_SPECS:
        data = json.loads((directory / spec.artifact).read_text())
        assert data["success"] is True and data["error"] is None, spec.artifact
        payload = data["data"]
        if spec.artifact == "sources.json":
            assert payload["sources"], spec.artifact
        elif spec.artifact.startswith("items_"):
            assert payload["item"]["summary_zh"], spec.artifact
            assert payload["item"]["related_discussions"], spec.artifact
            assert payload["evaluations"], spec.artifact
        else:
            assert payload["items"], spec.artifact
    for spec in SSR_SPECS:
        assert json.loads((directory / spec.artifact).read_text())["items"], spec.artifact
    html = (directory / "wechat_detail.html").read_bytes()
    assert WECHAT_TITLE.encode() in html and b"summary-body markdown-body" in html


def verify(db_path: Path, golden: Path, actual: Path | None) -> None:
    expected_invariant = json.loads((golden / "db-invariants.json").read_text())
    current_invariant = logical_db_invariant(db_path)
    assert current_invariant == expected_invariant, "logical DB digest mismatch; re-baseline required"
    golden_manifest = _manifest_by_artifact(golden)
    _assert_nonempty(golden)
    if actual is not None:
        actual_manifest = _manifest_by_artifact(actual)
        _assert_nonempty(actual)
        for spec in HTTP_SPECS:
            expected_meta = golden_manifest[spec.artifact]
            actual_meta = actual_manifest[spec.artifact]
            for field in ("artifact", "kind", "url", "status", "content_type"):
                assert actual_meta[field] == expected_meta[field], f"{spec.artifact}: {field} mismatch"
            expected = (golden / spec.artifact).read_bytes()
            observed = (actual / spec.artifact).read_bytes()
            if spec.kind == "html":
                assert observed == expected, f"{spec.artifact}: HTML byte mismatch"
            else:
                assert json.loads(observed) == json.loads(expected), f"{spec.artifact}: JSON semantic mismatch"
        print(
            f"REPEAT PASS json_semantic={len(API_SPECS) + len(SSR_SPECS)} html_byte={len(HTML_SPECS)} "
            f"metadata={len(HTTP_SPECS)} whitelist=0"
        )
    print(
        f"VERIFY PASS db_tables={len(current_invariant['tables'])} "
        f"db_digest={current_invariant['overall_sha256']} http={len(HTTP_SPECS)}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    capture_parser = subparsers.add_parser("capture")
    capture_parser.add_argument("--base-url", default="http://127.0.0.1:8011")
    capture_parser.add_argument("--output", type=Path, required=True)
    capture_parser.add_argument("--concurrency", type=int, default=4)
    record_parser = subparsers.add_parser("record-db")
    record_parser.add_argument("--db", type=Path, required=True)
    record_parser.add_argument("--output", type=Path, required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--db", type=Path, required=True)
    verify_parser.add_argument("--golden", type=Path, required=True)
    verify_parser.add_argument("--actual", type=Path)
    args = parser.parse_args()
    if args.command == "capture":
        capture(args.base_url, args.output, args.concurrency)
    elif args.command == "record-db":
        record_db(args.db, args.output)
    else:
        verify(args.db, args.golden, args.actual)


if __name__ == "__main__":
    main()
