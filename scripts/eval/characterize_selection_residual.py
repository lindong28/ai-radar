#!/usr/bin/env python3
"""刻画 AIHOT 的「选择残差」——它选了什么，而它自己的分数解释不了的那部分。

为什么需要它：2026-09-11 把条目重合的 gap 拆成三份（读数见 docs/issues/aihot-fit-eval.md
同日那节），**残差占 48%，比排序键 15% 与我方机制 9% 加起来还大一倍**。即使按 AIHOT 自己的
真分数排序、且完全绕开我方选择机制，也只拿得回它 47.9% 的精选。所以"它见过、打了高分、没选"
的那批条目是下一轮的主要对象，而在能拟合它之前先要知道它有没有结构。

**它不产出达标线读数，也不该进趋势序列**——那是 measure_curated_composition.py 的事。
本脚本只刻画参照物自己，属 prompt-distribution-fitting 那条纪律说的「参照物是研究对象、
不是记分牌」：第一个动作是刻画它的决策函数，不是换个位置再量一次差距。

用法：
    uv run python scripts/eval/characterize_selection_residual.py
    uv run python scripts/eval/characterize_selection_residual.py --by category
"""

from __future__ import annotations

import argparse
import collections
import importlib.util
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "_composition", HERE / "measure_curated_composition.py"
)
assert _spec and _spec.loader
_comp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_comp)


def wilson(k: int, n: int) -> tuple[float, float]:
    """复用判据那侧同一个区间，别另写一个——两处口径分叉过一次就再也对不上。"""

    return _comp.wilson_ci(k, n)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bands", type=int, default=10, help="分数分箱数（默认 10 = 十分位）")
    args = ap.parse_args()

    items = [v for v in _comp.load_aihot().values() if v.get("score") is not None]
    scored = sorted(items, key=lambda v: float(v["score"]))
    n = len(scored)
    sel_total = sum(1 for v in scored if v["selected"])
    print(f"AIHOT 有分条目 {n} 条，其中精选 {sel_total} 条（{100 * sel_total / max(n, 1):.1f}%）")

    # --- 1. 分数能解释多少 --------------------------------------------------
    # 分箱按**分位**不按分值：分数分布右偏，等宽箱会把大半条目塞进一两个箱里，
    # 于是"高分箱选中率高"读起来成立、而它只是那个箱里样本太少。
    print(f"\n>>> 选中率 × 分数{args.bands}分位  ——「分数解释了多少」")
    print(f"{'分位':>4} {'分数区间':>16} {'条数':>6} {'精选':>5} {'选中率':>8}  95% CI")
    per_band = max(1, n // args.bands)
    bands: list[tuple[float, float, int, int]] = []
    for b in range(args.bands):
        lo = b * per_band
        hi = n if b == args.bands - 1 else (b + 1) * per_band
        chunk = scored[lo:hi]
        if not chunk:
            continue
        k = sum(1 for v in chunk if v["selected"])
        s0, s1 = float(chunk[0]["score"]), float(chunk[-1]["score"])
        bands.append((s0, s1, k, len(chunk)))
        lo_ci, hi_ci = wilson(k, len(chunk))
        print(
            f"{b + 1:>4} {f'{s0:.1f}-{s1:.1f}':>16} {len(chunk):>6} {k:>5} "
            f"{100 * k / len(chunk):>7.1f}%  [{100 * lo_ci:5.1f},{100 * hi_ci:5.1f}]"
        )

    # 最高分位的选中率就是"纯按分数排序"的天花板附近；它离 100% 有多远，就是残差有多大。
    if bands:
        s0, s1, k, tot = bands[-1]
        print(
            f"    最高分位选中率 {100 * k / tot:.1f}% —— **这一格离 100% 的距离就是残差**："
            f"同样是它自己打的最高分，它也只选了这些。"
        )

    # --- 2. 控制分数之后，类别还剩多少作用 ----------------------------------
    # 不控制分数直接看类别选中率会被分数混淆（本仓 2026-09-10 已在这上面翻过一次车：
    # 控制分数后类别效应方向反转）。所以逐分位内部比。
    print("\n>>> 控制分数之后的类别效应 —— 每个分位内部各类的选中率")
    cats = sorted({v["category"] for v in scored if v["category"]})
    print(f"{'分位':>4} " + " ".join(f"{c[:7]:>10}" for c in cats))
    flips = collections.Counter()
    for b in range(args.bands):
        lo = b * per_band
        hi = n if b == args.bands - 1 else (b + 1) * per_band
        chunk = [v for v in scored[lo:hi] if v["category"]]
        if len(chunk) < 20:
            continue
        base = sum(1 for v in chunk if v["selected"]) / len(chunk)
        cells = []
        for c in cats:
            sub = [v for v in chunk if v["category"] == c]
            if len(sub) < 5:
                cells.append(f"{'n<5':>10}")
                continue
            rate = sum(1 for v in sub if v["selected"]) / len(sub)
            # 只在这个分位**有人被选中**时才投票。基线为 0 时每一类都等于基线，把平局记成负票
            # 会让所有类别一律拿到大负数——那读起来像"全都被压制"，实际只是低分位没人入选。
            if base > 0:
                flips[c] += 1 if rate > base else (-1 if rate < base else 0)
            cells.append(f"{100 * rate:>9.1f}%")
        print(f"{b + 1:>4} " + " ".join(cells))
    print(
        "    末行读法：某类在多数分位里都高于该分位基线，才算它有分数之外的加成；"
        f"逐类净票数 {dict(flips)}"
    )

    # --- 3. 残差有没有时间结构 ---------------------------------------------
    print("\n>>> 高分未选条目的发布日分布 —— 残差是不是某几天的现象")
    cut = scored[int(n * 0.8)]["score"] if n else 0
    by_day: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0])
    for v in scored:
        if float(v["score"]) < float(cut) or not v["published"]:
            continue
        by_day[v["published"]][1] += 1
        if v["selected"]:
            by_day[v["published"]][0] += 1
    print(f"    （高分 = 分数 >= {float(cut):.1f}，即顶部 20%）")
    print(f"{'发布日':12} {'高分条数':>8} {'其中精选':>8} {'选中率':>8}")
    for day in sorted(by_day):
        k, tot = by_day[day]
        print(f"{day:12} {tot:>8} {k:>8} {100 * k / max(tot, 1):>7.1f}%")
    rates = [k / tot for k, tot in by_day.values() if tot >= 10]
    if len(rates) >= 2:
        mean = sum(rates) / len(rates)
        sd = math.sqrt(sum((r - mean) ** 2 for r in rates) / (len(rates) - 1))
        print(
            f"    逐日选中率 均值 {100 * mean:.1f}%  标准差 {100 * sd:.1f}pp —— "
            "标准差小即残差是稳定现象、不是某几天的异常，那才值得去拟合它。"
        )

    slice_by_source(scored, args.bands)


def slice_by_source(scored: list, bands: int) -> None:
    """按信源切残差。类别与时段都切过了，源是最后一维，也是唯一要跨库 join 才拿得到的。

    为什么要控制分数：不控制直接看各源的选中率，读到的多半是"这个源的内容本来就打分高"。
    与类别那一节同一个理由，本仓 2026-09-10 已在不控制分数这件事上翻过一次车。
    """

    import sqlite3

    conn = sqlite3.connect(f"file:{_comp.REPO / 'data' / 'radar.db'}?mode=ro", uri=True)
    url_to_source = {}
    for url, source_id in conn.execute("SELECT url, source_id FROM items"):
        if url and source_id:
            url_to_source.setdefault(_comp.normalize_url(str(url))[0], str(source_id))

    cut = float(scored[int(len(scored) * 0.8)]["score"])
    rows = []
    for v in scored:
        if float(v["score"]) < cut or not v["url"]:
            continue
        src = url_to_source.get(_comp.normalize_url(v["url"])[0])
        if src:
            rows.append((src, bool(v["selected"])))
    if not rows:
        print("\n>>> 按信源切：我方库里匹配不到任何高分条目的源，跳过")
        return

    agg: dict[str, list[int]] = {}
    for src, sel in rows:
        cell = agg.setdefault(src, [0, 0])
        cell[1] += 1
        cell[0] += 1 if sel else 0
    base = sum(k for k, _ in agg.values()) / sum(t for _, t in agg.values())
    print(f"\n>>> 高分条目（分数 >= {cut:.1f}）的选中率 × 信源  —— 整体基线 {100 * base:.1f}%")
    print(f"    只列 n>=10 的源；**匹配得到 {len(rows)} 条**，其余高分条目我方库里没有或无 source_id")
    print(f"{'source_id':24} {'高分':>5} {'精选':>5} {'选中率':>8}  95% CI")
    for src, (k, tot) in sorted(agg.items(), key=lambda kv: -kv[1][0] / max(kv[1][1], 1)):
        if tot < 10:
            continue
        lo, hi = wilson(k, tot)
        mark = "  ↑" if lo > base else ("  ↓" if hi < base else "")
        print(f"{src:24} {tot:>5} {k:>5} {100 * k / tot:>7.1f}%  [{100 * lo:5.1f},{100 * hi:5.1f}]{mark}")
    print("    ↑/↓ = 该源的 95% CI 整个落在基线一侧，才算它有分数之外的偏好；没有标记的即判不动。")


if __name__ == "__main__":
    main()
