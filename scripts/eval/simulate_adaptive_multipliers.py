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
import random
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



def make_target_fn(args, by_day, labels, sel, shares, CATS):
    """目标占比的单一实现。**两个 mode 共用它**——先前 quota 与 quota-in-fill 各算一遍时，
    改了一处忘了另一处就会让两组读数不可比，而那种不一致在输出里看不出来。"""

    def target_shares(day, ema_ref, ema_pool, weight, err_mean, err_pool, chosen):
        pool_now = shares(Counter(
            labels[x.item_id] for x in by_day[day]
            if x.item_id in labels and x.weighted_score >= sel.DEFAULT_THRESHOLD
        ))
        tgt = {}
        for c in CATS:
            hist_ref = ema_ref[c] / weight
            if args.target == "pool-raw":
                tgt[c] = pool_now[c] if pool_now[c] > 0 else hist_ref
            elif args.target == "per-class-online":
                use_pool = err_pool[c] < err_mean[c]
                chosen[c] = "pool" if use_pool else "mean"
                tgt[c] = (pool_now[c] if pool_now[c] > 0 else hist_ref) if use_pool else hist_ref
            else:
                tgt[c] = hist_ref
        return tgt

    return target_shares


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(C.REPO / "data" / "radar.db"))
    ap.add_argument("--alpha", type=float, default=1.0, help="控制增益：m ← (target/ours)**alpha")
    ap.add_argument("--half-life", type=float, default=3.0, help="EMA 半衰期（天）")
    ap.add_argument("--clip", type=float, default=1.6, help="乘数夹在 [1/clip, clip]")
    ap.add_argument("--warmup", type=int, default=2, help="前 N 个窗口用静态配置，只喂历史不评")
    ap.add_argument(
        "--source-quota",
        type=float,
        default=None,
        help="覆盖 `_fill` 的 per_source 上限（生产 0.075）。**目标值不是「取消」而是参照物自己的水平**："
        "实测 AIHOT 自己最大单源占比均值 15.6%，我方 7.5% 反而是偏离——我们比它多样一倍。"
        "而时效那条相反：AIHOT 当日条目占比 82.2%、我方静态 86.2% 本就接近，裸 quota 的 100% 是走远，"
        "所以时效分段要**保留**。同一条「契约让位于目标」的裁定，两条契约判向相反方向。",
    )
    ap.add_argument(
        "--mode",
        choices=("multiplier", "quota", "quota-in-fill"),
        default="multiplier",
        help="multiplier=比例控制器（软）；quota=按类配额（硬，直接把格位按目标占比分配，类内取排序前列）。"
        "**先跑 multiplier 再跑 quota 是有依据的**：实测 AIHOT 的逐窗口占比对自身近期过去无正预测力"
        "（五类一致，滞后1 平均绝对误差 12.9pp vs 历史均值 11.6pp）⇒ 追最近观测必然更差，"
        "该跟的是均值；而配额是把构成直接锁到目标均值上的最短路径。"
        "**quota-in-fill=可上线的那一版**：类别配额只给每类的候选封顶，随后仍交 `_fill` 走它自己的"
        "时效分段与单源上限，两套约束叠加而不是互相顶替。裸 quota 替换了 `_fill`，实测把最大单源"
        "占比从 7.5%（= 生产的 per_source 0.075）推到 19.1%、当日条目占比推到 100%（尾部段消失）"
        "——那两条是已上线的契约，**裸 quota 因此只是上界探针，不是候选实现**。",
    )
    ap.add_argument(
        "--target",
        choices=("mean", "ema", "pool-raw", "pool-calibrated", "per-class-online"),
        default="mean",
        help="目标怎么取。mean=历史均值；ema=历史 EMA；**pool-calibrated=当日候选池给形状、"
        "历史给立场**——`target_c ∝ 当日池占比_c × (AIHOT 历史占比_c / 池历史占比_c)`。"
        "选它的依据是一次实测：预测 AIHOT 当日构成时，当日池的平均绝对误差 9.8pp < 历史均值 11.6pp，"
        "而且分类别看出了机制——tip/model/product（约 78% 质量）跟当天新闻流走（池更准），"
        "industry/paper 跟 AIHOT 自己稳定的编辑立场走（历史更准）。校准比正是把这两半合起来。"
        "**当日池在生产里是看得见的，不构成泄漏**；泄漏的是当日的 AIHOT，本脚本从不读它。"
        "per-class-online=**逐类在线选择**：每个窗口对每个类，用「到此为止累计绝对误差更小」的那个"
        "预测器（历史均值 vs 当日池校准）。**不是按看到的结果硬编码哪类用哪个**——那会把评测集"
        "用进模型；这里的选择只依据严格早于该窗口的误差记录，与预测本身同一条因果线。",
    )
    ap.add_argument(
        "--mech-labels",
        choices=("aihot", "ours"),
        default="aihot",
        help="**机制**读谁的类别。aihot=AIHOT 的标签（只覆盖它见过的条目，池子小）；"
        "ours=我方 enrich 的 `primary_category`（**生产里只有这个可用**，池子大得多）。"
        "判据那一侧**始终**用 AIHOT 的标签，与 `--labels off` 同口径——所以换的是机制的输入，"
        "不是评分的尺子。**上线前必须用 ours 重跑**：aihot 那一版机制看得见的候选比生产少一半以上。",
    )
    ap.add_argument(
        "--cap",
        choices=("both", "off"),
        default="both",
        help="`quota-in-fill` 里要不要给候选按目标占比**封顶**。both=封（上界+下界都在）；"
        "off=**只留再平衡这个下界**。分出这一档是因为封顶与再平衡对时效的作用方向相反："
        "再平衡的换入只从当日池取，故只会让当日占比持平或上升；而封顶削掉当日合规候选后，"
        "`_fill` 的尾部段会从 `all_eligible` 捞旧条目补满 40 格，当日占比必然下降。"
        "实测 both 把它从静态的 86.2% 推到 69.1%（AIHOT 自己 82.2%），是四个面里退得最狠的一个。",
    )
    ap.add_argument(
        "--label-offset",
        action="store_true",
        help="把目标从 AIHOT 标签空间换算到**我方标签空间**再施加。机制只看得到我方 enrich 的类别，"
        "而达标线按 AIHOT 的标签算，两者不恒等（AIHOT 的 tip 桶里 21.1% 被我方叫 industry）"
        "⇒ 机制在自己的标签空间上完美收敛时，AIHOT 标签空间上仍有系统性偏移。"
        "补偿项 = 同一批版面在两套标签下占比差的历史均值，只用严格早于本窗口的记录。",
    )
    ap.add_argument(
        "--mech-stamp",
        default=None,
        help="只让机制看 enrich 戳以此开头的条目。ADR-9e21 要求给 paper 以外的类别做类别级干预前"
        "按戳分层——industry/tip 跨标注器极不稳（旧戳判 industry 的精确率 30.1% vs 新戳 68.2%）。",
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

    ref_ids_by_day: dict[str, set[str]] = defaultdict(set)
    for record in aihot.values():
        if record["selected"] and record["url"] and record["published"]:
            oid = index.get(C.normalize_url(record["url"])[0])
            if oid:
                ref_ids_by_day[record["published"]].add(oid)

    # 机制用的标签（与判据那一侧分开）。判据恒用 AIHOT 标签 = `--labels off` 口径。
    mech_labels = dict(labels)
    if args.mech_labels == "ours":
        import json as _json

        mech_labels = {}
        q = ("SELECT e.item_id, e.output_json, e.ruleset_version FROM item_evaluations e "
             "WHERE e.stage='enrich' AND e.error IS NULL ORDER BY e.id")
        for item_id, out, stamp in conn.execute(q):
            if args.mech_stamp and not str(stamp or "").startswith(args.mech_stamp):
                continue
            try:
                cat = (_json.loads(out) or {}).get("primary_category")
            except Exception:
                continue
            if cat in CATS:
                mech_labels[str(item_id)] = cat  # ORDER BY id ⇒ 后写的（更新的戳）胜出
        print(f"机制标签: ours ⇒ {len(mech_labels)} 条带类别"
              f"{f'（戳前缀 {args.mech_stamp}）' if args.mech_stamp else ''}"
              f"；判据仍用 AIHOT 标签（{len(labels)} 条）")

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
    target_shares = make_target_fn(args, by_day, mech_labels, sel, shares, CATS)
    # `SourceQuota` 是 dataclass 不是 dict——`dict(...)` 会抛 TypeError。用 replace 造副本，
    # 既不改生产常量，也保住 kind_caps 那一半。
    import dataclasses

    source_quota = sel.DEFAULT_SOURCE_QUOTA
    if args.source_quota is not None:
        source_quota = dataclasses.replace(source_quota, per_source=args.source_quota)
        print(f"source quota: per_source {sel.DEFAULT_SOURCE_QUOTA.per_source} "
              f"-> {args.source_quota}（AIHOT 自己 0.156）")

    def run(adaptive: bool) -> list[dict]:
        """按时间顺序重放。adaptive=False 即当前生产（静态 CATEGORY_MULTIPLIERS）。"""

        ema_ref: dict[str, float] = {c: 0.0 for c in CATS}
        ema_ours: dict[str, float] = {c: 0.0 for c in CATS}
        ema_pool: dict[str, float] = {c: 0.0 for c in CATS}
        err_mean: dict[str, float] = {c: 0.0 for c in CATS}
        err_pool: dict[str, float] = {c: 0.0 for c in CATS}
        # H-labelspace：机制只看得到我方 enrich 的类别，判据按 AIHOT 的标签算，而两个标签空间
        # **不是恒等**（AIHOT 的 tip 桶里 21.1% 被我方叫 industry）。于是机制在自己的标签空间
        # 上完美收敛时，AIHOT 标签空间上仍有系统性偏移。这里累计同一批版面在两套标签下的占比差，
        # 取严格早于本窗口的历史均值作补偿项——只用我方自己的两套标签，不读当日 AIHOT。
        ema_gap: dict[str, float] = {c: 0.0 for c in CATS}
        chosen: dict[str, str] = {c: "mean" for c in CATS}
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
                    -cand.weighted_score * _m.get(mech_labels.get(cand.item_id) or "", 1.0),
                    cand.published_at,
                    cand.item_id,
                )

            def gate_score(cand):
                return cand.weighted_score  # 生产只在排序键上施加系数

            if adaptive and args.mode == "quota-in-fill" and weight > 0:
                # 给每类的候选按目标占比封顶，然后**照常**走 replay_day → `_fill`。
                # 封顶用 ceil 而不是 floor：某类被削到 0 会让它彻底消失，而判据量的是占比、
                # 不是"至少一条"——0 是比超配更远的失配。
                import math

                tgt = target_shares(day, ema_ref, ema_pool, weight, err_mean, err_pool, chosen)
                if args.label_offset and weight > 0:
                    # 目标是"我方标签空间下取什么值，才能让 AIHOT 标签空间下等于 AIHOT"。
                    # 负值夹到 0：占比不可能为负，而补偿项是估出来的、会过冲。
                    tgt = {c: max(0.0, tgt[c] + ema_gap[c] / weight) for c in CATS}
                tot = sum(tgt.values()) or 1.0
                cap = {c: max(1, math.ceil(sel.DEFAULT_LIMIT * tgt[c] / tot)) for c in CATS}
                def cap_list(items):
                    seen: Counter = Counter()
                    out = []
                    for cand in sorted(items, key=rank):
                        lab = mech_labels.get(cand.item_id)
                        if lab:
                            if seen[lab] >= cap.get(lab, sel.DEFAULT_LIMIT):
                                continue
                            seen[lab] += 1
                        out.append(cand)
                    return out

                # **两段都要封顶。** 先前只封了当日段，`_fill` 的尾部段仍从未封顶的
                # `all_eligible` 里捞，于是被削掉的类又回来了——n_ours 由 210 掉到 167 却仍
                # 只到 4/5，就是这个漏洞的形态：配额看着生效了，实际被尾部段抵消掉一半。
                # `--cap off` 把上界整个拿掉，只留下面那段再平衡做下界。**这不是"少做一步"**：
                # 封顶是唯一能把当日占比推下去的部件（再平衡的换入只从当日池取），所以关掉它
                # 换来的是时效，付出的是超配那一侧没人收——超配只能靠再平衡的 drop 一条条挤。
                shortlist = cap_list(by_day[day]) if args.cap == "both" else by_day[day]
                tail = cap_list(all_eligible) if args.cap == "both" else all_eligible
                picked = C.replay_day(shortlist, tail,
                                      sel.DEFAULT_LIMIT, rank, gate_score,
                                      source_quota=source_quota)
                # **封顶只是上界，不保证达到目标**——`_fill` 可以把某类填得不足，而判据量的是占比，
                # 少填与多填一样是失配。裸 quota 之所以到 5/5、封顶版只到 4/5，差的就是这个下界。
                # 再平衡：欠额的类拉它排名最高的未选条目，超额的类丢排名最低的那条。
                # **单源上限在这里逐条兑现**，所以时效分段与源上限都不被绕开。
                # **最大余额法，不是截断**：`int()` 五类合计最多丢 4 个格位，而这些格位随后按排名
                # 回填、系统性偏向高分类别（实测把 paper 从目标 8.8% 压到 3.8%、打出界）。
                # **基数必须与 `have` 一致**：`have` 只数有标签的那部分（约 22/40），而先前 `want`
                # 按 40 个格位算 ⇒ 每一类都被判成欠额、`over` 恒空、再平衡第一轮就 break，
                # 整个保底机制从未生效。读数上它长得像"结构到顶了"，实际是没跑。
                n_labelled = sum(1 for x in picked if x.item_id in mech_labels) or sel.DEFAULT_LIMIT
                exact = {c: n_labelled * tgt[c] / tot for c in CATS}
                want = {c: int(exact[c]) for c in CATS}
                import os
                if os.environ.get("DEBUG_QUOTA"):
                    print(f"    [dbg {day}] tgt={ {c: round(tgt[c],3) for c in CATS} } "
                          f"exact={ {c: round(exact[c],1) for c in CATS} }")
                for c in sorted(CATS, key=lambda c: exact[c] - want[c], reverse=True):
                    if sum(want.values()) >= n_labelled:
                        break
                    want[c] += 1
                blocked: set = set()
                for _ in range(sel.DEFAULT_LIMIT):
                    have = Counter(mech_labels[x.item_id] for x in picked if x.item_id in mech_labels)
                    short = [c for c in CATS if have[c] < want[c] and c not in blocked]
                    over = [c for c in CATS if have[c] > want[c]]
                    if not short or not over:
                        break
                    c_in = min(short, key=lambda c: have[c] - want[c])
                    c_out = max(over, key=lambda c: have[c] - want[c])
                    ids = {x.item_id for x in picked}
                    # **ADR-bc36 的配额是两条，不是一条。** 先前只兑现了 `per_source`，
                    # `kind_caps={"x":0.20}` 漏掉了——而「最大单源占比」这个读数看不见它，
                    # 它量的是另一个维度：换入的若都是 X 推文，单源仍可以 7.5%、X 却过 20%。
                    #
                    # **判的是换完之后合不合规，不是"再加一条"合不合规。** 一次再平衡是
                    # 换入 + 换出，`drop` 只由 `c_out` 决定、与 `add` 无关，所以先算它、再按
                    # 扣掉它之后的计数判。顺序反了会把「换掉一条 X、换入另一条 X」（净数不变）
                    # 误判成超限——而实测静态版面的 X 占比**恒等于上限 20.0%**，于是那个误判
                    # 不是边角情形，它会把整类换入无谓地封死。
                    drop = max((x for x in picked if mech_labels.get(x.item_id) == c_out), key=rank)
                    rest = [x for x in picked if x.item_id != drop.item_id]
                    src_now = Counter(x.source_id for x in rest)
                    kind_now = Counter(x.kind for x in rest)

                    def ok(x, _s=src_now, _k=kind_now):
                        if (_s[x.source_id] + 1) / sel.DEFAULT_LIMIT > source_quota.per_source + 1e-9:
                            return False
                        cap = source_quota.kind_caps.get(x.kind)
                        return cap is None or (_k[x.kind] + 1) / sel.DEFAULT_LIMIT <= cap + 1e-9

                    add = next(
                        (x for x in sorted(by_day[day], key=rank)
                         if x.item_id not in ids and mech_labels.get(x.item_id) == c_in and ok(x)),
                        None,
                    )
                    if add is None:
                        # 这一类今天补不到合规候选（池里没有、或会撞单源上限）。
                        # **只把它标记掉、继续补别的类**——先前这里是 break，一类补不到就把
                        # 整个再平衡停掉，其余类的欠额一并留着。
                        blocked.add(c_in)
                        continue
                    picked = rest + [add]
            elif adaptive and args.mode == "quota" and weight > 0:
                # 按类配额：把 40 个格位按目标占比分配，类内按排序键取前列；配不满的类把余额
                # 交还给一个共同池，按排序键补齐。**它绕开了 `_fill` 的时效/源配额**——那是代价，
                # 不是疏忽：本模式用来测「构成被锁死时判据能到哪」，是上界探针不是候选实现。
                if args.target == "pool-raw":
                    pool_now = shares(Counter(
                        labels[x.item_id] for x in by_day[day]
                        if x.item_id in labels and x.weighted_score >= sel.DEFAULT_THRESHOLD
                    ))
                    tgt = {c: (pool_now[c] if pool_now[c] > 0 else ema_ref[c] / weight)
                           for c in CATS}
                elif args.target == "per-class-online":
                    pool_now = shares(Counter(
                        labels[x.item_id] for x in by_day[day]
                        if x.item_id in labels and x.weighted_score >= sel.DEFAULT_THRESHOLD
                    ))
                    tgt = {}
                    for c in CATS:
                        hist_ref, hist_pool = ema_ref[c] / weight, ema_pool[c] / weight
                        ratio = (hist_ref / hist_pool) if hist_pool > 0 else 1.0
                        # **原始池，不是校准池**：乘法校准在修正均值的同时把方差也放大了，
                        # 实测 model 由 13.9pp 劣化到 38.5pp、整体 9.8 → 15.5pp。要搬均值得用
                        # 收缩/加法，乘一个大比值做不到。这一版直接用原始池占比。
                        cand_pool = pool_now[c] if pool_now[c] > 0 else hist_ref
                        # 谁到此为止更准就用谁。err_* 只累计**早于本窗口**的误差。
                        tgt[c] = cand_pool if err_pool[c] < err_mean[c] else hist_ref
                        chosen[c] = "pool" if err_pool[c] < err_mean[c] else "mean"
                elif args.target == "pool-calibrated":
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
                picked = C.replay_day(by_day[day], all_eligible, sel.DEFAULT_LIMIT, rank, gate_score,
                                      source_quota=source_quota)
            mine = Counter(labels[c.item_id] for c in picked if c.item_id in labels)
            verdict = C.class_verdicts(mine, reference_by_day[day])
            out.append(
                {
                    "day": day,
                    "evaluated": i >= args.warmup,
                    "inside": verdict["inside_count"],
                    "n_ref": verdict["n_reference"],
                    "mult": {c: round(mult.get(c, 1.0), 3) for c in CATS},
                    "chosen": dict(chosen),
                    "counts": (Counter(mine), Counter(reference_by_day[day])),
                    # 配额绕开了 `_fill` 的时效与单源上限，**代价要量出来才能谈上线**：
                    # hit = 版面里 AIHOT 也选了的条数（条目重合）；src = 最大单源占比（垄断）；
                    # fresh = 当日条目占比（时效）。三者都是用户可见面。
                    "hit": sum(1 for x in picked if x.item_id in ref_ids_by_day[day]),
                    "n": len(picked),
                    "src": max(Counter(x.source_id for x in picked).values()) / max(len(picked), 1),
                    # ADR-bc36 的第二条配额。**单独量**：它与最大单源占比是两个维度，
                    # 一个守住不代表另一个守住（换入的全是 X 时单源可以很低而 X 过 20%）。
                    "xshare": sum(1 for x in picked if x.kind == "x") / max(len(picked), 1),
                    "fresh": sum(1 for x in picked if sel._shanghai_date(x.published_at) == day)
                    / max(len(picked), 1),
                }
            )
            # 反馈：本窗口的读数进入历史，供**之后**的窗口用。顺序不能反。
            ref_s, our_s = shares(reference_by_day[day]), shares(mine)
            # **必须取两套标签都有的交集**，否则量到的不是标签空间的错位。
            # 第一版写成各取各的子集（AIHOT 标了 216/320、enrich 标了另一批），两个分母是
            # 不同的条目集合，差值里混进了"谁标了哪些条目"——实测它把 industry 的补偿号搞反，
            # 出界由 +0.92pp 放大到 +3.49pp。注释当时写着"同一批条目"，而代码没做到。
            both = [x for x in picked if x.item_id in mech_labels and x.item_id in labels]
            mech_s = shares(Counter(mech_labels[x.item_id] for x in both))
            aihot_s_on_both = shares(Counter(labels[x.item_id] for x in both))
            d = 1.0 if args.target == "mean" else decay
            weight = weight * d + 1.0
            pool_s = shares(Counter(
                x.item_id and labels[x.item_id] for x in by_day[day]
                if x.item_id in labels and x.weighted_score >= sel.DEFAULT_THRESHOLD
            ))
            for c in CATS:
                # 先用**本窗口之前**的状态给两个预测器记一次分，再把本窗口并进历史。
                # 顺序反了就是泄漏：拿本窗口的真值去更新历史、再用更新后的历史"预测"它。
                if weight > 0:
                    hist_ref_prev = ema_ref[c] / weight
                    hist_pool_prev = ema_pool[c] / weight
                    ratio = (hist_ref_prev / hist_pool_prev) if hist_pool_prev > 0 else 1.0
                    err_mean[c] += abs(hist_ref_prev - ref_s[c])
                    err_pool[c] += abs(pool_s[c] - ref_s[c])
                ema_gap[c] = ema_gap[c] * d + (mech_s[c] - aihot_s_on_both[c])
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
        detail = (
            "".join(f"{c[:3]}:{b['chosen'][c][0]} " for c in C.CATEGORIES)
            if args.target == "per-class-online"
            else str({c: v for c, v in b["mult"].items() if abs(v - 1.0) > 0.02})
        )
        print(f"{a['day']:12} {a['n_ref']:>5} {sa:>6} {sb:>7}{mark}  {detail}")

    # 逐窗口只有 7 个可评单元，判不动 0.2/5 量级的差。合并读数用的是同一批窗口的**全部**样本
    # （n_ref≈120），分辨力高得多——**但它不能替代逐窗口**：用户裁定「任何给定的同一段时期内」都要
    # 接近，而合并恰好把参照物的窗口间摆动平均掉。两个都报，谁也不顶替谁。
    def pooled(rows, picks):
        ours, refs = Counter(), Counter()
        for r, pk in zip(rows, picks):
            if not r["evaluated"]:
                continue
            ours += pk[0]
            refs += pk[1]
        v = C.class_verdicts(ours, refs)
        tv = C.total_variation(ours, refs)
        # 与逐窗口那一面同一条纪律：`k/5` 的绝对值没有零假设就读不动。合并口径 n 大得多，
        # 所以它是目前唯一判得动的面——但仍要把 P(5/5) 打出来，否则"真达标"与"n 就这么大"同形。
        nd = C.verdict_null(refs, v["n_ours"])
        pn = f"   P(5/5)={nd['p_all_inside']:.3f}" if nd else ""
        out = [f"{v['inside_count']}/{v['total']} 类落进区间   TV {tv:.3f}   "
               f"n_ours={v['n_ours']} n_ref={v['n_reference']}{pn}"]
        # **点名出界的是哪一类**：只报个数时，"还差一类"给不出下一步该动什么。
        for row in v["rows"]:
            if not row["inside"]:
                out.append(f"      OUT {row['category']}: 我方 {100 * row['ours_share']:.1f}% "
                           f"vs AIHOT {100 * row['reference_share']:.1f}% "
                           f"[{100 * row['reference_ci'][0]:.1f},{100 * row['reference_ci'][1]:.1f}] "
                           f"离区间 {row['gap_pp']:+.2f}pp")
        return "\n".join(out)

    def summary(rows):
        ev = [r for r in rows if r["evaluated"] and r["inside"] is not None]
        if not ev:
            return "无可评窗口"
        full = sum(1 for r in ev if r["inside"] == 5)
        mean = sum(r["inside"] for r in ev) / len(ev)
        return f"{full}/{len(ev)} 个窗口 5/5   平均落进 {mean:.2f}/5"

    # **逐窗口这一面此前没有零假设。** 合并口径早就补过了（`verdict_null`），而被当成最终判据的
    # 逐窗口均值一直只看绝对值——于是"真有差距"与"窗口 n_ref 只有 10–34、CI 宽得谁都进得去"
    # 在输出上同形。这里按逐窗口重算一次：一张**构成完全等于 AIHOT** 的页面在这批窗口上
    # 期望拿到多少。它同时是天花板与分辨力刻度。
    # **点估计不够，要散布。** 天花板给的是"完美页面平均拿多少"，判 4.29 与 4.00 分不分得开
    # 还要知道这个均值本身抖多大——7 个窗口的均值是 7 次抽样的平均，散布由各窗口的 null
    # 分布卷积而来。这里直接按各窗口的 null 分布重抽 7 元组、取均值，得到它的 5–95 分位。
    def null_band(rows):
        dists = []
        for r in rows:
            if not (r["evaluated"] and r["inside"] is not None):
                continue
            nd = C.verdict_null(r["counts"][1], sum(r["counts"][0].values()))
            if nd:
                dists.append(nd["distribution"])
        if not dists:
            return None
        mean = sum(sum(k * p for k, p in d.items()) for d in dists) / len(dists)
        rng = random.Random(20260911)
        ks = list(range(6))
        draws = sorted(
            sum(rng.choices(ks, weights=[d[k] for k in ks])[0] for d in dists) / len(dists)
            for _ in range(20000)
        )
        return mean, draws[1000], draws[19000]

    print(f"\n逐窗口（用户裁定的单位）")
    print(f"  静态   {summary(static)}")
    print(f"  自适应 {summary(adaptive)}")
    nb = null_band(adaptive)
    if nb is not None:
        nm, lo, hi = nb
        print(f"  零假设 完全符合 AIHOT 构成的页面在这批窗口上期望 {nm:.2f}/5，"
              f"90% 落在 [{lo:.2f}, {hi:.2f}]")
        print(f"         ⇒ **天花板不是 5.00**，而且这个均值自己就抖 ±{(hi - lo) / 2:.2f}；"
              f"落进这个区间的读数与「完美」分不开")
    def cost(rows):
        ev = [r for r in rows if r["evaluated"]]
        hit = sum(r["hit"] for r in ev); n = sum(r["n"] for r in ev)
        return (f"条目重合 {hit}/{n} = {100 * hit / max(n, 1):.1f}%   "
                f"最大单源占比 均值 {100 * sum(r['src'] for r in ev) / len(ev):.1f}%   "
                f"X 占比 均值 {100 * sum(r['xshare'] for r in ev) / len(ev):.1f}% "
                f"峰值 {100 * max(r['xshare'] for r in ev):.1f}%（ADR-bc36 上限 20%）   "
                f"当日条目占比 均值 {100 * sum(r['fresh'] for r in ev) / len(ev):.1f}%")

    print(f"\n代价（配额绕开了 _fill 的时效与单源上限）")
    print(f"  静态   {cost(static)}")
    print(f"  自适应 {cost(adaptive)}")
    print(f"\n合并同一批窗口（大 n，分辨力高，但会平均掉参照物的摆动）")
    print(f"  静态   {pooled(static, [r['counts'] for r in static])}")
    print(f"  自适应 {pooled(adaptive, [r['counts'] for r in adaptive])}")

    # **时间劈是本仓杀掉候选方案的那把刀**：同日那个 industry 乘数在合并口径上看着成立，
    # 一分前后半就散了。改善要跨 split 同向才算效应（CLAUDE.md 量具纪律第 ④ 条），所以合并
    # 这一面必须再劈一次——它也是唯一还判得动的面，劈完仍同向才谈得上上线。
    ev_idx = [i for i, r in enumerate(static) if r["evaluated"]]
    half = len(ev_idx) // 2
    print("\n时间劈（改善要跨 split 同向才算效应，不是 POOLED 好看就算）")
    for name, idx in (("前半", ev_idx[:half]), ("后半", ev_idx[half:])):
        sub_s = [static[i] for i in idx]
        sub_a = [adaptive[i] for i in idx]
        print(f"  {name} {static[idx[0]]['day']}–{static[idx[-1]]['day']}")
        print(f"    静态   {pooled(sub_s, [r['counts'] for r in sub_s])}")
        print(f"    自适应 {pooled(sub_a, [r['counts'] for r in sub_a])}")
    print("\n**读的是逐窗口达标率，不是 POOLED**：POOLED 会把参照物的摆动平均掉，"
          "而那正是这个结构要跟上的东西。")


if __name__ == "__main__":
    main()
