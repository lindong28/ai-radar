#!/usr/bin/env python3
"""按 AIHOT 的「选/不选」拟合排序权重，并检验它值不值得上线。

拟合目标是**逐条的决策函数**（selected ~ 六维），不是构成 TV——对着构成拟合就是对着
记分牌拟合。构成只作结果看。

**2026-09-08 的结论是「不上线」**，脚本留着是因为那个结论要能被复核，也因为拟合出的向量
本身有信息量：AIHOT 的挑选比我方更看信源权威（authority 0.413 vs 当前 0.10）、更少看内容
重要性（significance 0.257 vs 0.50）。当时的读数——留出 AUC **0.822 → 0.881**（5 seed 一致），
而两个用户可见的量都没动：top-N 命中 AIHOT 精选 21.5% → 22.1%（打平），构成 TV
0.226 → 0.221（噪声内）。AUC 的增益落在排序的中段与尾部，**只有头部进得了页面**。

    uv run python scripts/eval/fit_selection_weights.py
"""
from __future__ import annotations

import bisect
import collections
import json
import math
import random
import statistics as st
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from airadar.curator.weights import DEFAULT_WEIGHTS  # noqa: E402

DIMS = ["relevance", "density", "recency", "authority", "engineering", "significance"]
CATS = ["tutorial", "model", "product", "industry", "paper"]
QUESTIONS = REPO / "data/eval-fit/evalset-staging/aihot-fit-v1/questions.jsonl"
SCORED_RUN = REPO / "data/eval-fit/runs/FULL3-20260906/outputs.jsonl"
SEEDS = (1, 2, 3, 4, 5)


def load() -> list[tuple[list[float], int, str]]:
    reference: dict[str, tuple[str, bool]] = {}
    for line in QUESTIONS.open():
        row = json.loads(line)
        cat = row["reference"].get("primary_category")
        if cat in CATS:
            reference[row["input"]["item_id"]] = (cat, bool(row["reference"].get("selected")))
    out = []
    for line in SCORED_RUN.open():
        row = json.loads(line)
        item_id = row["item_id"]
        if item_id not in reference:
            continue
        dims = (row.get("score") or {}).get("output") or {}
        if not dims:
            continue
        cat, picked = reference[item_id]
        out.append(([float(dims.get(d) or 0) for d in DIMS], int(picked), cat))
    return out


def auc(scored: list[tuple[float, int]]) -> float:
    pos = [s for s, y in scored if y == 1]
    neg = sorted(s for s, y in scored if y == 0)
    if not pos or not neg:
        return float("nan")
    total = 0.0
    for p in pos:
        lo, hi = bisect.bisect_left(neg, p), bisect.bisect_right(neg, p)
        total += lo + 0.5 * (hi - lo)
    return total / (len(pos) * len(neg))


def composition_tv(items: list[tuple[float, str, int]], k: int) -> float:
    """items: (score, category, selected)。取 top-k 的构成 vs AIHOT 选中项的构成。"""
    picked = collections.Counter(c for _, c, y in items if y == 1)
    top = sorted(items, key=lambda t: -t[0])[:k]
    ours = collections.Counter(c for _, c, _ in top)
    n_ours, n_picked = sum(ours.values()), sum(picked.values())
    return sum(abs(ours[c] / n_ours - picked[c] / n_picked) for c in CATS) / 2


def fit(train: list[tuple[list[float], int, str]], *, l2: float = 0.05, iters: int = 4000, lr: float = 0.05) -> list[float]:
    """非负约束下的 logistic：每步把负分量截到 0，最后归一（尺度不影响排序）。"""
    w = [0.2] * len(DIMS)
    bias = -3.0
    n = len(train)
    n_pos = sum(1 for _, y, _ in train if y == 1)
    pos_weight = (n - n_pos) / max(1, n_pos)  # 78 个正样本，不加权会被负样本淹掉
    for _ in range(iters):
        grad_w = [0.0] * len(DIMS)
        grad_b = 0.0
        for x, y, _ in train:
            z = bias + sum(w[j] * x[j] for j in range(len(DIMS)))
            p = 1 / (1 + math.exp(-max(-30.0, min(30.0, z))))
            err = (p - y) * (pos_weight if y == 1 else 1.0)
            for j in range(len(DIMS)):
                grad_w[j] += err * x[j]
            grad_b += err
        for j in range(len(DIMS)):
            w[j] = max(0.0, w[j] - lr * (grad_w[j] / n + l2 * w[j]))
        bias -= lr * grad_b / n
    total = sum(w)
    return [v / total for v in w] if total > 0 else w


def main() -> None:
    rows = load()
    current = [getattr(DEFAULT_WEIGHTS, d) for d in DIMS]
    print(f"样本 {len(rows)}，AIHOT 选中 {sum(y for _, y, _ in rows)}")
    print("当前权重: " + "  ".join(f"{d}={v:.3f}" for d, v in zip(DIMS, current, strict=True)))

    scores: dict[str, dict[str, list[float]]] = {
        name: {"auc": [], "tv": [], "hit": []} for name in ("cur", "fit")
    }
    fitted: list[list[float]] = []
    for seed in SEEDS:
        random.seed(seed)
        pos = [r for r in rows if r[1] == 1]
        neg = [r for r in rows if r[1] == 0]
        random.shuffle(pos)
        random.shuffle(neg)
        train = pos[: len(pos) // 2] + neg[: len(neg) // 2]
        held = pos[len(pos) // 2 :] + neg[len(neg) // 2 :]
        weights = fit(train)
        fitted.append(weights)
        k = sum(1 for _, y, _ in held if y == 1)
        for name, vec in (("cur", current), ("fit", weights)):
            ranked = [(sum(vec[j] * x[j] for j in range(len(DIMS))), y, c) for x, y, c in held]
            scores[name]["auc"].append(auc([(s, y) for s, y, _ in ranked]))
            scores[name]["tv"].append(composition_tv([(s, c, y) for s, y, c in ranked], k))
            top = sorted(ranked, key=lambda t: -t[0])[:k]
            scores[name]["hit"].append(sum(1 for _, y, _ in top if y == 1) / k)

    print(f"\n留出验证（{len(SEEDS)} seed，各一半训练一半留出）")
    print(f"{'':<12}{'AUC':>16}{'top-N 命中':>16}{'构成 TV':>16}")
    for name, label in (("cur", "当前权重"), ("fit", "拟合权重")):
        s = scores[name]
        print(
            f"{label:<10}{st.mean(s['auc']):>9.3f} ±{st.stdev(s['auc']):.3f}"
            f"{st.mean(s['hit']) * 100:>10.1f}% ±{st.stdev(s['hit']) * 100:.1f}"
            f"{st.mean(s['tv']):>11.3f} ±{st.stdev(s['tv']):.3f}"
        )
    base = sum(y for _, y, _ in rows) / len(rows)
    print(f"{'随机基线':<10}{0.5:>9.3f}       {base * 100:>10.1f}%")

    print("\n拟合权重（seed 均值，已归一）:")
    for j, d in enumerate(DIMS):
        print(f"  {d:<14}{st.mean([f[j] for f in fitted]):.3f}   （当前 {current[j]:.2f}）")
    print("\n>>> 2026-09-08 判定：AUC 涨而命中率与构成不动，不上线。见 ADR-499e 同名节。")


if __name__ == "__main__":
    main()
