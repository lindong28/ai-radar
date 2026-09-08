#!/usr/bin/env python3
"""把构成差距拆成「分类错」与「选择偏」两层。全只读、零 LLM 调用。

做法：在 AIHOT 自己标注过的题集上，**两边都用 AIHOT 的标签**比构成——我方的分类
对不对因此被完全消掉，剩下的纯粹是「我方的排序偏好什么」。少了这一步就分不清一次
构成改善是来自分类修好了还是来自排序变了，而这两层的下一步动作完全不同。

    uv run python scripts/eval/measure_selection_bias.py

已知限制：这里按分数取全题集的前 N，而真实 curate 还叠加 48 小时新鲜窗与每源配额。
它回答「我方排序偏好什么」，不回答「首页会长什么样」——后者用
`measure_live_composition.py --local`。
"""
from __future__ import annotations

import collections
import json
from pathlib import Path

CATS = ["tutorial", "model", "product", "industry", "paper"]
DIMS = ["relevance", "density", "recency", "authority", "engineering", "significance"]
REPO = Path(__file__).resolve().parents[2]
QUESTIONS = REPO / "data/eval-fit/evalset-staging/aihot-fit-v1/questions.jsonl"
# A full run that carries every scored dimension for the whole evalset. Any run with
# the same coverage works; the dimensions are re-weighted here with the *current*
# weights, so the run's own weighted_score is deliberately ignored.
SCORED_RUN = REPO / "data/eval-fit/runs/FULL3-20260906/outputs.jsonl"


def _tv(a: dict[str, float], b: dict[str, float]) -> float:
    return sum(abs(a[c] - b[c]) for c in CATS) / 2


def _share(counter: collections.Counter[str]) -> dict[str, float]:
    n = sum(counter.values())
    return {c: counter[c] / n for c in CATS}


def main() -> None:
    import sys

    sys.path.insert(0, str(REPO / "src"))
    from airadar.curator.weights import DEFAULT_WEIGHTS as W

    reference: dict[str, str] = {}
    selected: collections.Counter[str] = collections.Counter()
    pool: collections.Counter[str] = collections.Counter()
    for line in QUESTIONS.open():
        row = json.loads(line)
        cat = row["reference"].get("primary_category")
        if cat not in CATS:
            continue
        reference[row["input"]["item_id"]] = cat
        pool[cat] += 1
        if row["reference"].get("selected"):
            selected[cat] += 1

    ranked: list[tuple[float, str]] = []
    for line in SCORED_RUN.open():
        row = json.loads(line)
        item_id = row["item_id"]
        if item_id not in reference:
            continue
        dims = (row.get("score") or {}).get("output") or {}
        if not dims:
            continue
        ranked.append((sum(getattr(W, d) * (dims.get(d) or 0) for d in DIMS), item_id))
    ranked.sort(reverse=True)

    n_selected = sum(selected.values())
    ours = collections.Counter(reference[i] for _, i in ranked[:n_selected])
    aihot_share, our_share, pool_share = _share(selected), _share(ours), _share(pool)

    print(f"题集 {len(reference)} 条 · AIHOT 精选 {n_selected} 条 · 我方已评分 {len(ranked)} 条\n")
    print(f"{'口径':<34}{'对 AIHOT 精选构成的 TV':>22}")
    print(f"{'我方按当前权重取 top-N':<30}{_tv(our_share, aihot_share):>22.3f}")
    print(f"{'整个题集，完全不做选择':<30}{_tv(pool_share, aihot_share):>22.3f}")
    print("   ↑ 前者大于后者，就说明排序把构成推得比不选还远\n")

    print(f"{'类别':<10}{'池占比':>9}{'AIHOT精选率':>13}{'我方入选率':>12}{'相对':>8}")
    for c in CATS:
        rate_ai = selected[c] / pool[c]
        rate_us = ours[c] / pool[c]
        rel = f"{rate_us / rate_ai:.2f}x" if rate_ai else "-"
        print(f"{c:<10}{pool[c] / sum(pool.values()) * 100:>8.1f}%{rate_ai * 100:>12.1f}%{rate_us * 100:>11.1f}%{rel:>8}")

    live = [d for d in DIMS if getattr(W, d) > 0]
    print(f"\n各类在承重维度上的均值（当前非零权重：{'  '.join(f'{d}={getattr(W, d):.2f}' for d in live)}）")
    print(f"{'类别':<10}{'加权分':>9}" + "".join(f"{d[:11]:>12}" for d in live))
    means: dict[str, dict[str, list[float]]] = {c: {d: [] for d in DIMS} for c in CATS}
    totals: dict[str, list[float]] = {c: [] for c in CATS}
    for line in SCORED_RUN.open():
        row = json.loads(line)
        item_id = row["item_id"]
        if item_id not in reference:
            continue
        dims = (row.get("score") or {}).get("output") or {}
        if not dims:
            continue
        cat = reference[item_id]
        for d in DIMS:
            means[cat][d].append(dims.get(d) or 0)
        totals[cat].append(sum(getattr(W, d) * (dims.get(d) or 0) for d in DIMS))
    for c in CATS:
        avg = sum(totals[c]) / len(totals[c])
        print(f"{c:<10}{avg:>9.2f}" + "".join(f"{sum(means[c][d]) / len(means[c][d]):>12.2f}" for d in live))


if __name__ == "__main__":
    main()
