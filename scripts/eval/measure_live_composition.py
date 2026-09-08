#!/usr/bin/env python3
"""北极星指标：**线上真实页面**的类别构成离 AIHOT 自精选有多远。

为什么单独一个脚本、而不是从库里算：库里的 enrich 行是历次 prompt 的混合沉积
（2026-09-08 实测：候选池 83.2% 来自 5 月那版、1.8% 来自当时生产现行版），
从它推断「用户看到什么」会读出一个既不是过去也不是现在的数。这里直接读
读者实际打开的那个页面——`~/.claude/references/evidence-sufficiency.md`
所说的消费者通道观察面。

    uv run python scripts/eval/measure_live_composition.py [URL]

    uv run python scripts/eval/measure_live_composition.py --local

**两条已知限制，读数之前先看**：

1. **线上口径比本地滞后最多 5 小时。** 生产读的是它自己那份 `radar.db`，由
   `deploy/sync/sync-db-cron.sh` 在 01:41/06:41/11:41/16:41/21:41 同步过去
   （一次同步含 5G 快照，本身要跑几分钟）。所以本地刚改完 enrich 立刻测线上，
   量到的是上一次同步的旧数据——2026-09-08 实测踩过这一脚，连测三次都是
   0.385 才想起来查同步。要立刻看效果用 `--local`：它按同一口径读本地库最新一次
   curate 的条目，也就是下一次同步会送上去的那批。
2. **首页还有 90 秒边缘缓存**（`cache-control: max-age=90`，响应头
   `eo-cache-status` 可看），且 query 参数不进缓存键，加 `?_cb=` 绕不过去。
   刚部署完要等过 TTL 再测。

另有抽样限制：一次首页只渲染几十条，TV 的抽样噪声不小；它给方向与量级，
分辨不了 0.05 以内的差别。
"""
from __future__ import annotations

import collections
import json
import re
import sys
import urllib.request
from pathlib import Path

CATS = ["tutorial", "model", "product", "industry", "paper"]
DEFAULT_URL = "https://news.aiplanet.live/"
REPO = Path(__file__).resolve().parents[2]
EVALSET = REPO / "data/eval-fit/evalset-staging/aihot-fit-v1/questions.jsonl"


def live_distribution(url: str) -> collections.Counter[str]:
    """页面把逐条数据以 JSON 内嵌在 HTML 里，直接数其中的 primary_category。"""
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 - fixed https host
        html = resp.read().decode("utf-8", errors="replace")
    found = re.findall(r'"primary_category"\s*:\s*"([^"]+)"', html)
    return collections.Counter(c for c in found if c in CATS)


def reference_distribution() -> collections.Counter[str]:
    """AIHOT 自己选进榜的那些条目的类别构成——拟合的目标。"""
    ref: collections.Counter[str] = collections.Counter()
    for line in EVALSET.open():
        r = json.loads(line)["reference"]
        if r.get("selected") and r.get("primary_category") in CATS:
            ref[r["primary_category"]] += 1
    return ref


def local_distribution() -> collections.Counter[str]:
    """Same measurement against the local database's newest curated run.

    This is what the next sync will put in front of readers, so it is the reading to
    use right after changing enrich — the live page cannot show it yet.
    """
    import json as _json
    import sqlite3

    con = sqlite3.connect(f"file:{REPO / 'data/radar.db'}?mode=ro", uri=True)
    con.execute("PRAGMA busy_timeout=30000")
    run = con.execute(
        "SELECT id FROM curation_runs ORDER BY datetime(created_at) DESC LIMIT 1"
    ).fetchone()[0]
    counts: collections.Counter[str] = collections.Counter()
    for (item_id,) in con.execute("SELECT item_id FROM curated_items WHERE run_id=?", (run,)):
        row = con.execute(
            "SELECT output_json FROM item_evaluations WHERE item_id=? AND stage='enrich'"
            " AND error IS NULL ORDER BY id DESC LIMIT 1",
            (item_id,),
        ).fetchone()
        if not row:
            continue
        try:
            cat = _json.loads(row[0]).get("primary_category")
        except (ValueError, TypeError):
            continue
        if cat in CATS:
            counts[cat] += 1
    print(f"本地最新 curate run: {run}")
    return counts


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--local"]
    if "--local" in sys.argv[1:]:
        live, ref = local_distribution(), reference_distribution()
        url = "<本地库最新 curate>"
    else:
        url = args[0] if args else DEFAULT_URL
        live, ref = live_distribution(url), reference_distribution()
    n, m = sum(live.values()), sum(ref.values())
    if not n:
        print(f"页面上没有取到任何 primary_category（{url}）——页面结构可能变了，先看一眼再信这个 0")
        raise SystemExit(1)
    tv = sum(abs(live[c] / n - ref[c] / m) for c in CATS) / 2
    label = "本地待同步" if url.startswith("<") else "线上渲染"
    print(f"{url}\n{label} n={n} 条 · AIHOT 自精选 n={m} 条\n")
    print(f"{'类别':<10}{label:>10}{'AIHOT':>10}{'差':>10}")
    for c in CATS:
        print(f"{c:<10}{live[c] / n * 100:>9.1f}%{ref[c] / m * 100:>9.1f}%{(live[c] / n - ref[c] / m) * 100:>+9.1f}pp")
    print(f"\n>>> 用户可见构成总变差 TV = {tv:.3f}")


if __name__ == "__main__":
    main()
