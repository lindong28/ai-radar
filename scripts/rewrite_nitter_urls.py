#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from airadar.egress import open_external_url  # noqa: E402
from airadar.fetcher.urls import canonicalize_item_url  # noqa: E402


def _probe_status(url: str) -> str:
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "ai-radar-url-rewrite/1.0"})
    try:
        with open_external_url(
            request,
            callsite_id="scripts.rewrite_nitter_urls.probe",
            timeout=8,
        ) as response:
            return "ok" if response.status in {200, 301, 302, 303, 307, 308} else f"http_{response.status}"
    except urllib.error.HTTPError as exc:
        return "broken" if exc.code == 404 else f"http_{exc.code}"
    except Exception as exc:  # pragma: no cover - network diagnostics only
        return f"probe_error:{type(exc).__name__}"


def rewrite_nitter_urls(db_path: Path, *, probe: bool = True) -> int:
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT id, url, extra_json FROM items WHERE url LIKE '%nitter.net%'").fetchall()
    rewritten = 0
    for item_id, url, extra_json in rows:
        try:
            extra = json.loads(extra_json or "{}")
        except json.JSONDecodeError:
            extra = {}
        canonical, updated_extra = canonicalize_item_url(str(url), extra)
        if canonical == url:
            continue
        if probe:
            updated_extra["url_status"] = _probe_status(canonical)
        conn.execute(
            "UPDATE items SET url=?, extra_json=? WHERE id=?",
            (
                canonical,
                json.dumps(updated_extra, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                item_id,
            ),
        )
        rewritten += 1
    conn.commit()
    conn.close()
    return rewritten


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/radar.db")
    parser.add_argument("--no-probe", action="store_true")
    args = parser.parse_args()

    rewritten = rewrite_nitter_urls(Path(args.db), probe=not args.no_probe)
    print(f"rewritten={rewritten}")


if __name__ == "__main__":
    main()
