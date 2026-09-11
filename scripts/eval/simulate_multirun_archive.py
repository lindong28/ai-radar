#!/usr/bin/env python3
"""在**归档面**上回放多轮 curate，用来 A/B 替代排序策略。

为什么需要它（2026-09-11）：两个既有量具各自缺一半——
`measure_archive_composition.py` 量的是真实消费者面（跨 run 累积、按 `published_at` 取前 40），
但它读**真实历史**，重放不了替代排序；`measure_curated_composition.py` 能重放，但只重放**单轮**，
而用户首页是约 **48 轮/天**的并集。要回答「把 model 排高一点，用户看到的构成会怎样」就两个都不够。

**这不是新发明**：`plans/20260820-content-align/artifacts/sim_quota.py`（ADR-bc36 的证据来源）
已经在做多时点回放 + 跨轮并集 + 策略 variants。本脚本是把那份能力搬进 `scripts/eval/`
（它在 `plans/` 下、不入 git），并改三处：参照物改用 `load_aihot()`、窗口改为跟随参照语料、
节奏改成可调。**底座换成 `replay_day`**——那一份已验证与生产逐字节同形，
而 sim_quota 自带一份重实现，两份会各自漂移。

用法：
    uv run python scripts/eval/simulate_multirun_archive.py --per-day 4
    uv run python scripts/eval/simulate_multirun_archive.py --per-day 4 --multiplier model=1.15
"""

from __future__ import annotations

import argparse
import importlib.util
import sqlite3
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "src"))

_spec = importlib.util.spec_from_file_location(
    "_composition", HERE / "measure_curated_composition.py"
)
assert _spec and _spec.loader
_comp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_comp)

CATS = list(_comp.CATEGORIES)
SH = timedelta(hours=8)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(REPO / "data" / "radar.db"))
    ap.add_argument("--per-day", type=int, default=4,
                    help="每天回放几个时点。**生产实测约 48 个 run/天**（`curated_items` 9292 run / "
                         "371653 行），4 是 sim_quota 的原值、也是本脚本的起点；"
                         "并集规模随它增长，读绝对值前先对齐这个参数。")
    ap.add_argument("--limit", type=int, default=40, help="每轮选多少条（生产 40）")
    ap.add_argument("--page", type=int, default=40, help="首页每页条数（生产 40）")
    ap.add_argument("--multiplier", action="append", default=[], metavar="CATEGORY=FACTOR",
                    help="覆盖 `select.CATEGORY_MULTIPLIERS` 里的一项，可重复。"
                         "**这是本脚本存在的理由**：它让替代排序在归档面上可比。")
    ap.add_argument("--until", default=None,
                    help="只读到这一天为止的归档面。**归档是累积的，所以时间劈只能这么切**——"
                         "把窗口对半砍成两段各自重放会让后半段丢掉前半段的并集，那不是它真实的样子。")
    args = ap.parse_args()

    from airadar.curator import select as sel
    from airadar.curator.weights import DEFAULT_WEIGHTS

    overrides = dict(sel.CATEGORY_MULTIPLIERS)
    for spec in args.multiplier:
        k, _, v = spec.partition("=")
        overrides[k.strip()] = float(v)

    aihot = _comp.load_aihot()
    reference_by_day: dict[str, Counter] = {}
    label_by_url: dict[str, str] = {}
    for r in aihot.values():
        if r.get("category") and r.get("url"):
            label_by_url[_comp.normalize_url(r["url"])[0]] = r["category"]
        if r["selected"] and r["published"] and r.get("category"):
            reference_by_day.setdefault(r["published"], Counter())[r["category"]] += 1
    days = sorted(d for d, c in reference_by_day.items() if sum(c.values()) >= 5)
    if args.until:
        days = [d for d in days if d <= args.until]

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    candidates = sel.deduplicate_candidates(sel._load_candidates(conn, DEFAULT_WEIGHTS))
    url_by_id = {}
    for i, u in conn.execute("SELECT id, url FROM items WHERE url IS NOT NULL"):
        url_by_id[str(i)] = _comp.normalize_url(str(u))[0]
    print(f"候选 {len(candidates)} 条；窗口 {len(days)} 个：{days[0]}..{days[-1]}；"
          f"每天 {args.per_day} 个时点")

    # **系数要认两套词表。** 我方兜底桶在 `prompts_v2.py` 里叫 `tutorial`，AIHOT 的同一个桶叫 `tip`，
    # 而判据、本脚本的输出、以及人写命令行时用的都是 `tip`。只查 `primary_category` 的话
    # `--multiplier tip=X` **永不命中**，而读数一声不响地与不加系数逐位相同——
    # 实测 tip=1.40/2.0/3.0 三次输出完全一样，那不是饱和，是没生效。
    BUCKET_TO_OURS = {"tip": "tutorial"}
    for k in list(overrides):
        if k in BUCKET_TO_OURS:
            overrides[BUCKET_TO_OURS[k]] = overrides.pop(k)
    seen_cats = {c.primary_category for c in candidates}
    unknown = [k for k in overrides if k not in seen_cats]
    if unknown:
        # 不命中就报错退出：一个打不中的系数与「这一类不响应」在读数上完全同形。
        raise SystemExit(
            f"这些系数在候选池里没有对应类别、打不中任何条目：{unknown}；"
            f"池里实际有的是 {sorted(seen_cats)}"
        )

    if overrides != sel.CATEGORY_MULTIPLIERS:
        print(f"类别系数：{sel.CATEGORY_MULTIPLIERS} → {overrides}")

    def factor(c):
        return overrides.get(c.primary_category, 1.0)

    def rank(c):
        # 与 `select.ranking_key` 同形：系数**只进排序键**，不进绝对闸（ADR-3f8b）。
        return (-c.weighted_score * factor(c), c.published_at, c.item_id)

    def gate_score(c):
        return c.weighted_score

    # 时点按上海时刻均分一天。可用性用 `published_at <= t` 近似——**没发布就选不到**；
    # 更准的是 `fetched_at`，但它不在 `ScoredCandidate` 上，而两个 arm 同样近似 ⇒ 配对不受影响。
    union: dict = {}
    ours_pooled: Counter = Counter()
    reference_pooled: Counter = Counter()
    print(f"\n{'上海日':12}{'并集':>7}{'页内有标签':>11}{'当日发布占比':>13}  逐类（AIHOT 标签）")
    for day in days:
        y, m, d = (int(x) for x in day.split("-"))
        for k in range(args.per_day):
            hour = round(24 * (k + 1) / args.per_day)
            t = datetime(y, m, d, tzinfo=UTC) - SH + timedelta(hours=hour)
            iso = t.isoformat().replace("+00:00", "Z")
            avail = [c for c in candidates if c.published_at and c.published_at <= iso]
            if not avail:
                continue
            tday = sel._shanghai_date(iso)
            pool = [c for c in avail if sel._shanghai_date(c.published_at) == tday]
            for c in _comp.replay_day(pool, avail, args.limit, rank, gate_score):
                union.setdefault(c.item_id, c)
        page = sorted(union.values(), key=lambda c: (c.published_at, c.item_id), reverse=True)[: args.page]
        labelled = Counter()
        for c in page:
            cat = label_by_url.get(url_by_id.get(c.item_id, ""))
            if cat:
                labelled[cat] += 1
        same = sum(1 for c in page if sel._shanghai_date(c.published_at) == day)
        print(f"{day:12}{len(union):>7}{sum(labelled.values()):>11}"
              f"{100 * same / max(len(page), 1):>12.1f}%  "
              + " ".join(f"{c[:3]}:{labelled[c]}" for c in CATS if labelled[c]))
        ours_pooled += labelled
        reference_pooled += reference_by_day[day]

    tv = _comp.total_variation(ours_pooled, reference_pooled)
    v = _comp.class_verdicts(ours_pooled, reference_pooled)
    print(f"\n>>> 归档面达标线（合并，TV {tv:.3f}）")
    if v["inside_count"] is None:
        print(f"    判不了：{v['reason']}")
        return
    print(f"{'类别':8}{'我方占比':>12}{'AIHOT 占比':>13}{'AIHOT 95% CI':>20}{'判定':>6}{'离区间':>10}")
    for row in v["rows"]:
        lo, hi = row["reference_ci"]
        gap = "" if row["inside"] else f"{row['gap_pp']:+.2f}pp"
        print(f"{row['category']:8}{100 * row['ours_share']:11.2f}%{100 * row['reference_share']:12.2f}%"
              f"   [{100 * lo:5.2f},{100 * hi:5.2f}]{'IN' if row['inside'] else 'OUT':>6}{gap:>10}")
    nd = _comp.verdict_null(reference_pooled, v["n_ours"])
    print(f"    ⇒ {v['inside_count']}/{v['total']} 类落进区间（我方 n={v['n_ours']}，AIHOT n={v['n_reference']}）"
          + (f"   P(5/5)={nd['p_all_inside']:.3f}" if nd else ""))
    print("    **这是模拟的归档面，不是真实历史**——真实历史用 measure_archive_composition.py。"
          "两者的差里含时点数（本次 %d/天 vs 生产约 48/天）与可用性近似。" % args.per_day)


if __name__ == "__main__":
    main()
