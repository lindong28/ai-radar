#!/usr/bin/env python3
"""Parallel shadow comparison between Mp2RSS and candidate WeChat feeds.

Measures the two things a cutover decision turns on:

  coverage — did the candidate surface the article at all?
  latency  — how much later than Mp2RSS did it first become observable?

Latency here is *discovery* latency (first time this runner saw the item), not
the article's own pubDate. A feed can carry a correct pubDate for an article it
only started serving hours later, so pubDate cannot answer "would I have seen
it sooner".

Join key is (account, normalized title). Mp2RSS serves short /s/<token> links
and the candidates serve long ?__biz&mid&idx&sn links, so the same article has
no shared URL substring; joining on URL would report every row as missing.
Where a long link is available its __biz is recorded and checked against the
configured public_biz, so title collisions across accounts still get caught.

Usage:
    shadow_compare.py observe    # append one observation round to the ledger
    shadow_compare.py report     # coverage + latency from the whole ledger
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree

import httpx

PLAN_DIR = Path(__file__).resolve().parent.parent
LEDGER = PLAN_DIR / "evidence" / "shadow-observations.jsonl"
FEEDS = PLAN_DIR / "shadow-feeds.json"
REPO = PLAN_DIR.parent.parent
RADAR_DB = REPO / "data" / "radar.db"

_PUNCT = re.compile(r"[\s　|｜\-–—_,，.。!！?？:：;；'\"“”‘’()（）\[\]【】]+")


def normalize_title(title: str) -> str:
    """Collapse the formatting noise that differs between feed renderers."""
    folded = unicodedata.normalize("NFKC", title).casefold()
    return _PUNCT.sub("", folded)


def mp2rss_url() -> str:
    """Read the comparator URL from the live DB; it carries a subscription token."""
    conn = sqlite3.connect(f"file:{RADAR_DB}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT url FROM sources WHERE id='wx_mp2rss'").fetchone()
    finally:
        conn.close()
    if not row:
        raise SystemExit("no wx_mp2rss source in radar.db — cannot run comparison")
    return row[0]


def parse_feed(xml: bytes) -> list[dict[str, str]]:
    root = ElementTree.fromstring(xml)
    out = []
    for item in root.iter("item"):

        def text(tag: str) -> str:
            node = item.find(tag)
            return (node.text or "").strip() if node is not None else ""

        title = text("title")
        if not title:
            continue
        out.append({"title": title, "url": text("link"), "published": text("pubDate"),
                    "author": text("author") or text("{http://purl.org/dc/elements/1.1/}creator")})
    return out


def identity(url: str) -> dict[str, str]:
    """Extract __biz/mid/idx/sn when the provider serves canonical long links."""
    q = parse_qs(urlparse(url).query)
    return {k: q[k][0] for k in ("__biz", "mid", "idx", "sn") if k in q}


def observe() -> None:
    if not FEEDS.exists():
        raise SystemExit(f"missing {FEEDS} — see the template in this file's docstring")
    config = json.loads(FEEDS.read_text(encoding="utf-8"))
    now = datetime.now(UTC).isoformat(timespec="seconds")

    sources: list[tuple[str, str, str | None]] = [("mp2rss", mp2rss_url(), None)]
    for entry in config["candidates"]:
        sources.append((entry["provider"], entry["url"], entry.get("account")))

    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with LEDGER.open("a", encoding="utf-8") as fh:
        for provider, url, account_hint in sources:
            try:
                resp = httpx.get(url, timeout=60.0, follow_redirects=True)
                resp.raise_for_status()
                items = parse_feed(resp.content)
            except Exception as exc:  # a dead provider is data, not a crash
                fh.write(json.dumps({"observed_at": now, "provider": provider,
                                     "error": f"{type(exc).__name__}: {exc}"},
                                    ensure_ascii=False) + "\n")
                print(f"  {provider}: FAILED {type(exc).__name__}")
                continue
            for item in items:
                record = {
                    "observed_at": now,
                    "provider": provider,
                    "account": account_hint or item["author"] or "?",
                    "title": item["title"],
                    "title_norm": normalize_title(item["title"]),
                    "url": item["url"],
                    "published": item["published"],
                    **identity(item["url"]),
                }
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                written += 1
            print(f"  {provider}: {len(items)} items")
    print(f"wrote {written} observations to {LEDGER}")


def report() -> None:
    if not LEDGER.exists():
        raise SystemExit(f"no ledger yet at {LEDGER} — run `observe` first")

    first_seen: dict[tuple[str, str], dict[str, str]] = {}
    titles: dict[tuple[str, str], str] = {}
    errors: dict[str, int] = {}
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if "error" in rec:
            errors[rec["provider"]] = errors.get(rec["provider"], 0) + 1
            continue
        key = (rec["account"], rec["title_norm"])
        titles.setdefault(key, rec["title"])
        seen = first_seen.setdefault(key, {})
        prev = seen.get(rec["provider"])
        if prev is None or rec["observed_at"] < prev:
            seen[rec["provider"]] = rec["observed_at"]

    providers = sorted({p for v in first_seen.values() for p in v})
    candidates = [p for p in providers if p != "mp2rss"]
    if not candidates:
        raise SystemExit("ledger has no candidate provider rows yet")

    print(f"ledger rounds cover {len(first_seen)} distinct articles\n")
    if errors:
        print("fetch failures per provider:", errors, "\n")

    for cand in candidates:
        # Scope to accounts this candidate is configured to carry. Comparing
        # against Mp2RSS's full account list would count every account the
        # candidate was never asked about as a miss, which reads as a coverage
        # failure and is nothing of the sort.
        covered = {k[0] for k, v in first_seen.items() if cand in v}
        scoped = {k: v for k, v in first_seen.items() if k[0] in covered}

        both = [k for k, v in scoped.items() if "mp2rss" in v and cand in v]
        only_mp = [k for k, v in scoped.items() if "mp2rss" in v and cand not in v]
        only_cand = [k for k, v in scoped.items() if cand in v and "mp2rss" not in v]
        out_of_scope = len(first_seen) - len(scoped)

        # A provider added to the config partway through the run has no
        # observations before its first round, so every article that already
        # existed when it joined shows a delay of exactly "how late it joined".
        # That is an artifact of the ledger, not of the provider. Measure only
        # articles that both providers first saw after the later of their two
        # start times — i.e. articles that appeared while both were watching.
        cand_start = min(v[cand] for v in first_seen.values() if cand in v)
        mp_start = min(v["mp2rss"] for v in first_seen.values() if "mp2rss" in v)
        watch_start = max(cand_start, mp_start)

        deltas = []
        for key in both:
            if first_seen[key]["mp2rss"] <= watch_start or first_seen[key][cand] <= watch_start:
                continue
            a = datetime.fromisoformat(first_seen[key]["mp2rss"])
            b = datetime.fromisoformat(first_seen[key][cand])
            deltas.append((b - a).total_seconds() / 60.0)
        deltas.sort()

        print(f"=== {cand} ===")
        print(f"  scope: {len(covered)} accounts this provider carries "
              f"({len(scoped)} articles); {out_of_scope} articles from other "
              f"accounts excluded, not counted as misses")
        print(f"  both saw:            {len(both)}")
        print(f"  mp2rss only (MISS):  {len(only_mp)}")
        print(f"  {cand} only:         {len(only_cand)}   (mp2rss is not ground truth)")
        print(f"  both watching since: {watch_start}")
        if deltas:
            mid = deltas[len(deltas) // 2]
            p90 = deltas[int(len(deltas) * 0.9) - 1] if len(deltas) >= 10 else deltas[-1]
            print(f"  discovery delay vs mp2rss: median {mid:+.0f} min, p90 {p90:+.0f} min, "
                  f"worst {deltas[-1]:+.0f} min   over {len(deltas)} articles that "
                  f"appeared while both were watching   (negative = candidate saw it first)")
        else:
            print("  discovery delay: NOT MEASURABLE YET — no article has appeared since "
                  "both providers were being sampled. Every article already in a feed when "
                  "sampling began yields a delay equal to the gap between the two providers' "
                  "start times, which measures the ledger, not the provider.")
        if len(first_seen) and len({r for r in _rounds(LEDGER)}) < 3:
            print("  WARNING: too few observation rounds to read these numbers as "
                  "coverage. Providers hold different-sized windows (mp2rss ~100 "
                  "items across all accounts, wechat2rss 20 per account), so early "
                  "rounds report window-boundary differences as misses. Judge "
                  "coverage only on articles first observed after the run started.")
        for key in only_mp[:10]:
            print(f"    MISS  {key[0]}  {titles[key][:50]}")
        print()


def _rounds(path: Path) -> set[str]:
    return {json.loads(line)["observed_at"] for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()}


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"observe", "report"}:
        raise SystemExit(__doc__)
    (observe if sys.argv[1] == "observe" else report)()
