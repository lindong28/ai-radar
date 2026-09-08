#!/usr/bin/env python3
"""北极星指标：**线上真实页面**的类别构成离 AIHOT 自精选有多远。

为什么单独一个脚本、而不是从库里算：库里的 enrich 行是历次 prompt 的混合沉积
（2026-09-08 实测：候选池 83.2% 来自 5 月那版、1.8% 来自当时生产现行版），
从它推断「用户看到什么」会读出一个既不是过去也不是现在的数。这里直接读
读者实际打开的那个页面——`~/.claude/references/evidence-sufficiency.md`
所说的消费者通道观察面。

    uv run python scripts/eval/measure_live_composition.py [URL]

已知限制：一次首页只渲染几十条，TV 的抽样噪声不小；它给方向与量级，
不适合用来分辨 0.05 以内的差别。要更稳的读数请多取几次或改用候选池口径
（`measure_composition_gap.py` 的 ③b），代价是那个口径量的是池子不是页面。
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


def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    live, ref = live_distribution(url), reference_distribution()
    n, m = sum(live.values()), sum(ref.values())
    if not n:
        print(f"页面上没有取到任何 primary_category（{url}）——页面结构可能变了，先看一眼再信这个 0")
        raise SystemExit(1)
    tv = sum(abs(live[c] / n - ref[c] / m) for c in CATS) / 2
    print(f"{url}\n线上渲染 n={n} 条 · AIHOT 自精选 n={m} 条\n")
    print(f"{'类别':<10}{'线上':>10}{'AIHOT':>10}{'差':>10}")
    for c in CATS:
        print(f"{c:<10}{live[c] / n * 100:>9.1f}%{ref[c] / m * 100:>9.1f}%{(live[c] / n - ref[c] / m) * 100:>+9.1f}pp")
    print(f"\n>>> 用户可见构成总变差 TV = {tv:.3f}")


if __name__ == "__main__":
    main()
