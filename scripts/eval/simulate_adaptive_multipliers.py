#!/usr/bin/env python3
"""模拟「随时间调整的类别乘数」——给静态配置加自由度，去跟一个会移动的参照物。

**为什么需要它**（用户 2026-09-11 裁定，原文见 docs/references/aihot-approximation-metrics.md）：
达标线仍是逐窗口的，不放宽；要变的是被评系统的自由度。而实测 AIHOT 自己的构成周间摆动
TV = 0.240（model 42.0% → 17.9%，Wilson 区间不相交），**大于我方与它的整体距离 0.129**
⇒ 一组固定权重结构上追不上它。

**为什么选比例控制器**：观察到的三件事各自排除了别的形状——
① 它的选择解释不了它自己的分数（按其真分数排序也只召回 39.7%）⇒ 拟合它的打分函数封顶很低；
② 但**分位内的类别偏好是稳定的**（score≥70：industry 19.6% vs model 47.6%）⇒ 类别这一层有可学结构；
③ 那一层的**取值随时间变** ⇒ 需要的是跟踪，不是再拟合一组常数。
比例控制器复用已上线的 `CATEGORY_MULTIPLIERS` 通道（该通道能移动判据、反向对照咬得住，均已实测），
是加自由度的最小结构。不够再升级到按类配额。

**因果约束（违反即读数作废）**：生产里给今天选稿时，今天的 AIHOT 还没发。所以控制器
**只读严格早于该窗口的历史**。本脚本按这个约束实现，warmup 期用静态配置。

**评的是逐窗口达标率，不是 POOLED**：POOLED 恰好把参照物的摆动平均掉，而那正是要被跟上的东西。

用法：
    uv run python scripts/eval/simulate_adaptive_multipliers.py --db <frozen.db>
    uv run python scripts/eval/simulate_adaptive_multipliers.py --db <frozen.db> --alpha 0.5 --half-life 3
"""

from __future__ import annotations

import argparse
import importlib.util
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("_comp", HERE / "measure_curated_composition.py")
assert _spec and _spec.loader
C = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(C)

import sys

sys.path.insert(0, str(C.REPO / "src"))
from airadar.curator import select as sel  # noqa: E402
from airadar.curator.weights import DEFAULT_WEIGHTS  # noqa: E402

CATS = C.CATEGORIES


def shares(counter: Counter) -> dict[str, float]:
    total = sum(counter[c] for c in CATS)
    return {c: (counter[c] / total if total else 0.0) for c in CATS}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(C.REPO / "data" / "radar.db"))
    ap.add_argument("--alpha", type=float, default=1.0, help="控制增益：m ← (target/ours)**alpha")
    ap.add_argument("--half-life", type=float, default=3.0, help="EMA 半衰期（天）")
    ap.add_argument("--clip", type=float, default=1.6, help="乘数夹在 [1/clip, clip]")
    ap.add_argument("--warmup", type=int, default=2, help="前 N 个窗口用静态配置，只喂历史不评")
    ap.add_argument(
        "--mode",
        choices=("multiplier", "quota"),
        default="multiplier",
        help="multiplier=比例控制器（软）；quota=按类配额（硬，直接把格位按目标占比分配，类内取排序前列）。"
        "**先跑 multiplier 再跑 quota 是有依据的**：实测 AIHOT 的逐窗口占比对自身近期过去无正预测力"
        "（五类一致，滞后1 平均绝对误差 12.9pp vs 历史均值 11.6pp）⇒ 追最近观测必然更差，"
        "该跟的是均值；而配额是把构成直接锁到目标均值上的最短路径。",
    )
    ap.add_argument(
        "--target",
        choices=("mean", "ema", "pool-calibrated"),
        default="mean",
        help="目标怎么取。mean=历史均值；ema=历史 EMA；**pool-calibrated=当日候选池给形状、"
        "历史给立场**——`target_c ∝ 当日池占比_c × (AIHOT 历史占比_c / 池历史占比_c)`。"
        "选它的依据是一次实测：预测 AIHOT 当日构成时，当日池的平均绝对误差 9.8pp < 历史均值 11.6pp，"
        "而且分类别看出了机制——tip/model/product（约 78% 质量）跟当天新闻流走（池更准），"
        "industry/paper 跟 AIHOT 自己稳定的编辑立场走（历史更准）。校准比正是把这两半合起来。"
        "**当日池在生产里是看得见的，不构成泄漏**；泄漏的是当日的 AIHOT，本脚本从不读它。",
    )
    args = ap.parse_args()

    aihot = C.load_aihot()
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn.execute("PRAGMA busy_timeout=60000")

    index: dict[str, str] = {}
    for item_id, url in conn.execute("SELECT id, url FROM items"):
        index.setdefault(C.normalize_url(str(url))[0], str(item_id))
    labels: dict[str, str] = {}
    reference_by_day: dict[str, Counter] = defaultdict(Counter)
    for record in aihot.values():
        if not record["category"]:
            continue
        if record["selected"]:
            reference_by_day[record["published"]][record["category"]] += 1
        if record["url"]:
            our_id = index.get(C.normalize_url(record["url"])[0])
            if our_id:
                labels[our_id] = record["category"]

    candidates = sel.deduplicate_candidates(sel._load_candidates(conn, DEFAULT_WEIGHTS))
    by_day: dict[str, list] = defaultdict(list)
    for cand in candidates:
        day = sel._shanghai_date(cand.published_at)
        if day:
            by_day[day].append(cand)
    days = sorted(
        d
        for d in by_day
        if sum(reference_by_day[d][c] for c in CATS) >= 5 and len(by_day[d]) >= 200
    )
    all_eligible = [c for c in candidates if c.weighted_score >= sel.DEFAULT_THRESHOLD]

    decay = 0.5 ** (1.0 / max(args.half_life, 1e-9))

    def run(adaptive: bool) -> list[dict]:
        """按时间顺序重放。adaptive=False 即当前生产（静态 CATEGORY_MULTIPLIERS）。"""

        ema_ref: dict[str, float] = {c: 0.0 for c in CATS}
        ema_ours: dict[str, float] = {c: 0.0 for c in CATS}
        ema_pool: dict[str, float] = {c: 0.0 for c in CATS}
        weight = 0.0
        out = []
        for i, day in enumerate(days):
            mult = dict(sel.CATEGORY_MULTIPLIERS)
            # quota 模式下**不叠加控制器**：否则量到的是「配额 + 一个已知会过冲的控制器」，
            # 两个干预叠在一起，谁的作用都判不出来。第一次跑 quota 时正是这样，打印出的
            # `{'model': 1.6, 'industry': 0.625}` 暴露了它。
            if adaptive and weight > 0 and args.mode == "multiplier":
                # **只用 weight>0 的历史**：warmup 之前没有可依据的过去，此时退回静态配置。
                for c in CATS:
                    tgt, cur = ema_ref[c] / weight, ema_ours[c] / weight
                    if tgt <= 0 or cur <= 0:
                        continue  # 任一侧近期为零时比值无定义；不动它比瞎猜好
                    factor = (tgt / cur) ** args.alpha
                    mult[c] = min(args.clip, max(1.0 / args.clip, mult.get(c, 1.0) * factor))

            def rank(cand, _m=mult):
                return (
                    -cand.weighted_score * _m.get(labels.get(cand.item_id) or "", 1.0),
                    cand.published_at,
                    cand.item_id,
                )

            def gate_score(cand):
                return cand.weighted_score  # 生产只在排序键上施加系数

            if adaptive and args.mode == "quota" and weight > 0:
                # 按类配额：把 40 个格位按目标占比分配，类内按排序键取前列；配不满的类把余额
                # 交还给一个共同池，按排序键补齐。**它绕开了 `_fill` 的时效/源配额**——那是代价，
                # 不是疏忽：本模式用来测「构成被锁死时判据能到哪」，是上界探针不是候选实现。
                if args.target == "pool-calibrated":
                    pool_now = shares(Counter(
                        labels[x.item_id] for x in by_day[day]
                        if x.item_id in labels and x.weighted_score >= sel.DEFAULT_THRESHOLD
                    ))
                    tgt = {}
                    for c in CATS:
                        hist_ref, hist_pool = ema_ref[c] / weight, ema_pool[c] / weight
                        # 校准比无定义时退回历史均值——它是这两者里更保守的那个。
                        ratio = (hist_ref / hist_pool) if hist_pool > 0 else 1.0
                        tgt[c] = pool_now[c] * ratio if pool_now[c] > 0 else hist_ref
                else:
                    tgt = {c: ema_ref[c] / weight for c in CATS}
                tot = sum(tgt.values()) or 1.0
                ranked = sorted(by_day[day], key=rank)
                quota = {c: int(sel.DEFAULT_LIMIT * tgt[c] / tot) for c in CATS}
                picked, used = [], set()
                for cand in ranked:
                    lab = labels.get(cand.item_id)
                    if lab and quota.get(lab, 0) > 0:
                        picked.append(cand); used.add(cand.item_id); quota[lab] -= 1
                for cand in ranked:  # 余额：未标注的条目也在这里进来，与生产同形
                    if len(picked) >= sel.DEFAULT_LIMIT:
                        break
                    if cand.item_id not in used:
                        picked.append(cand); used.add(cand.item_id)
            else:
                picked = C.replay_day(by_day[day], all_eligible, sel.DEFAULT_LIMIT, rank, gate_score)
            mine = Counter(labels[c.item_id] for c in picked if c.item_id in labels)
            verdict = C.class_verdicts(mine, reference_by_day[day])
            out.append(
                {
                    "day": day,
                    "evaluated": i >= args.warmup,
                    "inside": verdict["inside_count"],
                    "n_ref": verdict["n_reference"],
                    "mult": {c: round(mult.get(c, 1.0), 3) for c in CATS},
                }
            )
            # 反馈：本窗口的读数进入历史，供**之后**的窗口用。顺序不能反。
            ref_s, our_s = shares(reference_by_day[day]), shares(mine)
            d = 1.0 if args.target == "mean" else decay
            weight = weight * d + 1.0
            pool_s = shares(Counter(
                x.item_id and labels[x.item_id] for x in by_day[day]
                if x.item_id in labels and x.weighted_score >= sel.DEFAULT_THRESHOLD
            ))
            for c in CATS:
                ema_ref[c] = ema_ref[c] * d + ref_s[c]
                ema_ours[c] = ema_ours[c] * d + our_s[c]
                ema_pool[c] = ema_pool[c] * d + pool_s[c]
        return out

    static, adaptive = run(False), run(True)
    print(f"窗口 {len(days)} 个 | warmup {args.warmup} | alpha {args.alpha} | "
          f"half-life {args.half_life} | clip [{1 / args.clip:.2f}, {args.clip:.2f}]")
    print(f"{'日期':12} {'n_ref':>5} {'静态':>6} {'自适应':>7}  自适应当日乘数")
    for a, b in zip(static, adaptive):
        if not a["evaluated"]:
            print(f"{a['day']:12} {a['n_ref']:>5} {'warmup':>6} {'warmup':>7}")
            continue
        sa = "n<8" if a["inside"] is None else f"{a['inside']}/5"
        sb = "n<8" if b["inside"] is None else f"{b['inside']}/5"
        mark = ""
        if a["inside"] is not None and b["inside"] is not None:
            mark = "  ↑" if b["inside"] > a["inside"] else ("  ↓" if b["inside"] < a["inside"] else "")
        nz = {c: v for c, v in b["mult"].items() if abs(v - 1.0) > 0.02}
        print(f"{a['day']:12} {a['n_ref']:>5} {sa:>6} {sb:>7}{mark}  {nz}")

    def summary(rows):
        ev = [r for r in rows if r["evaluated"] and r["inside"] is not None]
        if not ev:
            return "无可评窗口"
        full = sum(1 for r in ev if r["inside"] == 5)
        mean = sum(r["inside"] for r in ev) / len(ev)
        return f"{full}/{len(ev)} 个窗口 5/5   平均落进 {mean:.2f}/5"

    print(f"\n静态   {summary(static)}")
    print(f"自适应 {summary(adaptive)}")
    print("\n**读的是逐窗口达标率，不是 POOLED**：POOLED 会把参照物的摆动平均掉，"
          "而那正是这个结构要跟上的东西。")


if __name__ == "__main__":
    main()
