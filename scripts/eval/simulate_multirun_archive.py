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
    ap.add_argument("--closed-loop", action="store_true",
                    help="闭环模式：**不用固定系数**。目标 = AIHOT 的历史均值构成（只用严格早于当前时点的"
                         "窗口算，不读当日），反馈 = **归档页此刻的实际构成**（用户真看到的那一面），"
                         "每个时点按 gap 调一次系数。固定系数是它在某份语料上的收敛点，"
                         "而池子构成一漂那个点就失准——闭环的价值就在能重新找到它。")
    ap.add_argument("--actuator", choices=("rank", "admit", "quota", "quota+floor"), default="rank",
                    help="闭环拧哪个旋钮。rank=逐类排序系数（低节奏有效）；"
                         "**admit=控制谁进得了跨轮并集**——高节奏下并集会吃掉过阈值候选的大半"
                         "（实测 per-day 12 时 713/1202 = 59%%，外推到生产 ~48/天接近全部），"
                         "此时归档页的构成主要由准入决定、排序的话语权被稀释。"
                         "**admit 已实测否决**：它是直接丢弃，丢掉的格位由更旧、AIHOT 没见过的"
                         "条目补上——页内有标签由 275/400 掉到 164/400、当日占比塌到 47.5%，"
                         "构成只在剩下那一小撮里好看。"
                         "quota=**在选择内部按类封顶**，空出的格位交给 `_fill` 由别类与尾部回填，"
                         "格位数与时效分段都不被绕开。"
                         "**quota+floor=再补下限**：封顶只压得住超配的类，**造不出短缺的类**；"
                         "欠配的类从未选的合规候选里拉它排名最高的那条进来，换掉超配类排名最低的那条。")
    ap.add_argument("--gain", type=float, default=0.6,
                    help="闭环增益：m *= (target/actual)**gain。>1 会过冲振荡，本仓实测过一次"
                         "（`alpha=1.0` 的比例控制器逐窗口 5/5 掉到 0/7）。")
    ap.add_argument("--clip", type=float, default=2.5, help="系数夹在 [1/clip, clip]")
    ap.add_argument("--enrich-stamp", default=None, metavar="PREFIX",
                    help="机制只认该 enrich 戳（前缀匹配 `ruleset_version`）产出的类别。"
                         "ADR-9e21 §一要求任何类别级干预**按戳分层重量一次**，而本池混用三代戳。"
                         "用来判效应是不是某一代戳带来的。")
    ap.add_argument("--score-only-covered", action="store_true",
                    help="**保留累积并集，但只在「机制标签覆盖率 > 0」的窗口上评分。** "
                         "决策评审 blocker 1：全窗 `--label-time replay` 的悲观里含 08-31/09-01 "
                         "两窗覆盖率为 0（产类别的 enrich `.r2` 09-02 才开始扫），"
                         "那个条件**不会再现**却被永久算进读数；而 `--since` 砍头会造出一个"
                         "从未存在过的并集。本开关两头都避开：并集照常从第一天累积，只是"
                         "那些窗口的页面不进 `ours_pooled` / `reference_pooled`。")
    ap.add_argument("--rank-by-aihot-score", action="store_true",
                    help="**上界探针，不是候选方案**：把排序键换成 AIHOT 自己的分数"
                         "（按量具纪律 ③ 走**同池百分位**映射到我方分数尺度，不比绝对分）；"
                         "AIHOT 没打过分的条目保留我方分数。用来判打分轴对**条目重合**的天花板"
                         "——指标档里那条『打分轴已否』是对**构成**的读数，对重合从没量过。")
    ap.add_argument("--no-quota", action="store_true",
                    help="关掉 `_fill` 的全部配额结构（新鲜度段 + 源配额）。用来判 ④ 那格"
                         "「过了闸仍进不了并集」是输在配额结构，还是单纯排不进前 40。")
    ap.add_argument("--per-source", type=float, default=None, metavar="SHARE",
                    help="覆盖 ADR-bc36 的 `per_source`（生产 0.075）。`0` 表示取消该上限。"
                         "AIHOT 自己的最大单源占比实测 15.6%%。**它对构成指标实测无帮助**，"
                         "但条目重合从没量过它——而重合正是源配额该咬的地方。")
    ap.add_argument("--fixed-m-until", default=None, metavar="DATE",
                    help="**固定 M**：只用早于该日发布的双标注条目估一次混淆矩阵，**冻结**，"
                         "此后所有窗口都用它。与 `--causal-m` 的滚动重估互斥——"
                         "决策评审复核轮点名要这条：现有证据绑在滚动策略上，"
                         "若生产用固定 M，缺一份「早期估一次、冻结后在后续窗口验证」的配对读数。")
    ap.add_argument("--causal-m", action="store_true",
                    help="混淆矩阵**只用严格早于当前窗口**的双标注条目估计（与 `hist` 的因果纪律同构）。"
                         "不加则用全量估一次、所有切点复用——那样跨 split 不给 M 留出数据，"
                         "四个切点全是 in-sample（决策评审 2026-09-11 报出）。样本不足时回退到未校正。")
    ap.add_argument("--permute-labels", type=int, default=None, metavar="SEED",
                    help="**零控制**：把机制标签在条目之间随机置换（保持边际分布不变）后重跑。"
                         "混淆矩阵随之在置换后的标签上重估，于是整条链自洽。"
                         "若读数与真标签档无差，说明机制根本没在用标签里的信息——"
                         "「机制有效」与「任何标签都行」在没有这条对照时输出同形。")
    ap.add_argument("--target-space", choices=("ours", "aihot", "corrected"), default="ours",
                    help="机制**比**的目标用谁的标注器数。`ours` = 我方 enrich 在 AIHOT 精选条目上"
                         "重数（离线可得，非 oracle）；`aihot` = AIHOT 自己的标签。"
                         "与 --labels 是**两个正交维度**：只换其一会让 `tgt/act` 的分子分母"
                         "来自两个标注器（review gate 的 blocker 1）。`--labels oracle` 时本项强制为 aihot。")
    ap.add_argument("--label-time", choices=("replay", "final"), default="replay",
                    help="机制**读**条目标签的时点。`replay` = 只看回放时点之前已产出的 enrich 行"
                         "（生产真实可得）；`final` = 快照时刻的最终标签（**会把数小时后才产生的"
                         "标签提前交给机制**，review gate 的 blocker 2）。仅 --labels ours 有效。")
    ap.add_argument("--labels", choices=("ours", "oracle"), default="ours",
                    help="**机制**读谁的类别。`ours` = 我方 enrich（生产唯一可得）；"
                         "`oracle` = AIHOT 自己的标签，**生产不可得**，只作上界对照。"
                         "计分器恒用 AIHOT 标签，与本开关无关——那是判据本身。")
    ap.add_argument("--src-cap", type=float, default=None,
                    help="**并集层面**的单源上限（占并集的比例）。生产的 `per_source=0.075` 是**每轮**的闸"
                         "（3/40），而并集跨 48 轮累积 ⇒ **并集不受它约束**。实测池中带标签的候选里 "
                         "`ithome` 133 条（49% 是 industry）、`x_rohanpaul_ai` 116 条（43% 是 paper），"
                         "两源占 32%，正好灌满我们超配的那两类。")
    ap.add_argument("--threshold", type=float, default=None,
                    help="覆盖 `select.DEFAULT_THRESHOLD`（生产 6.5）。**降它是放大供给、不是放松排序**："
                         "实测窗口内 AIHOT 标为 model 的候选过闸率已有 52.6%（阈值不歧视 model），"
                         "但至 09-05 的并集需要 131 条 model 而池里只有 122 条——差 9 条。"
                         "降阈值同时会多放进 tip/product，**要靠 `--actuator quota` 的封顶压住**，"
                         "两者是成对的，单独降阈值对 model 反而不利。")
    ap.add_argument("--since", default=None,
                    help="只从该日（含）起算。⚠️ **它的读数与全窗不可直接比**："
                         "`--until` 是砍尾（保留累积），本项是砍头，会造出一个从未存在过的并集；"
                         "且 `sum(hist)>=8` 的暖机闸在首窗就满足，控制器会在并集几乎为空时"
                         "做第一次调参，而 `live` 的系数逐窗持续，那次噪声留在被评区间内。"
                         "只用于定位，不用于结论。")
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
    # **AIHOT 精选条目的 URL，按发布日分组。** 机制的目标要用**机制自己的标注器**重新数一遍
    # （见 `target_by_day` 的构建），否则 `tgt/act` 的分子分母来自两个标注器。
    ref_urls_by_day: dict[str, list[str]] = {}
    for r in aihot.values():
        if r.get("category") and r.get("url"):
            label_by_url[_comp.normalize_url(r["url"])[0]] = r["category"]
        if r["selected"] and r["published"] and r.get("category"):
            reference_by_day.setdefault(r["published"], Counter())[r["category"]] += 1
            if r.get("url"):
                ref_urls_by_day.setdefault(r["published"], []).append(
                    _comp.normalize_url(r["url"])[0])
    pub_by_url: dict[str, str] = {}
    for r in aihot.values():
        if r.get("url") and r.get("published"):
            pub_by_url[_comp.normalize_url(r["url"])[0]] = r["published"]
    days = sorted(d for d, c in reference_by_day.items() if sum(c.values()) >= 5)
    if args.until:
        days = [d for d in days if d <= args.until]
    if args.since:
        # **归档面是累积的，所以早期窗口永远留在并集里。** 于是 enrich 覆盖率低的那几天
        # （实测 08-31 6.9%、09-01 14.3%、09-02 40.2%，09-05 起才是 100%）会一直拖着读数，
        # 而机制在那些天**看不见类别**、封顶无从施加。`--since` 把起点挪到覆盖率起来之后，
        # 用来把「机制无效」与「机制被蒙住眼睛」分开——它**不是**用来挑好看的窗口的，
        # 报它必须同时报覆盖率理由与全窗读数。
        if len(args.since) != 10 or args.since.count("-") != 2:
            raise SystemExit(f"--since 要 YYYY-MM-DD（字符串比较），给的是 {args.since!r}；"
                             f"格式不对会静默不过滤")
        days = [d for d in days if d >= args.since]
        print("⚠️ --since 砍头：并集从此日重建，**与全窗读数不可直接比**（见该参数 help）")
    if not days:
        raise SystemExit("--since / --until 过滤后没有窗口了")

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    if args.threshold is not None:
        # 两道绝对闸一起降：`_fill` 的 fresh 段按 freshness_floor 收，尾部段按 threshold 收，
        # 只降一个会让供给只在一段上放大、另一段照旧，读数就不是「降阈值」的效应。
        print(f"阈值：{sel.DEFAULT_THRESHOLD} → {args.threshold}"
              f"（freshness_floor {sel.DEFAULT_FRESHNESS_FLOOR} 同比例降）")
        ratio = args.threshold / sel.DEFAULT_THRESHOLD
        sel.DEFAULT_FRESHNESS_FLOOR = sel.DEFAULT_FRESHNESS_FLOOR * ratio
        sel.DEFAULT_THRESHOLD = args.threshold
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

    # 上界探针：AIHOT 分数 → 同池百分位 → 我方分数尺度上的等百分位值。
    # **绝对分不可比**（两套打分器量纲不同，量具纪律 ③ 就是为此）。
    aihot_rank_score: dict[str, float] = {}
    if args.rank_by_aihot_score:
        a_scores = sorted(r["score"] for r in aihot.values() if r.get("score") is not None)
        o_scores = sorted(c.weighted_score for c in candidates)
        import bisect
        _rev = {u: i for i, u in url_by_id.items()}   # id_by_url 此处尚未定义
        for r in aihot.values():
            if r.get("score") is None or not r.get("url"):
                continue
            iid = _rev.get(_comp.normalize_url(r["url"])[0])
            if iid is None:
                continue
            pct = bisect.bisect_left(a_scores, r["score"]) / max(len(a_scores) - 1, 1)
            aihot_rank_score[iid] = o_scores[
                min(int(pct * (len(o_scores) - 1)), len(o_scores) - 1)]
        print(f"上界探针：{len(aihot_rank_score)} 条用 AIHOT 分数的同池百分位替换排序分"
              f"（AIHOT 分数 n={len(a_scores)}，我方池 n={len(o_scores)}）")

    def factor(c):
        return overrides.get(c.primary_category, 1.0)

    def rank(c):
        # 与 `select.ranking_key` 同形：系数**只进排序键**，不进绝对闸（ADR-3f8b）。
        sc = aihot_rank_score.get(c.item_id, c.weighted_score)
        return (-sc * factor(c), c.published_at, c.item_id)

    OURS_TO_BUCKET = {v: k for k, v in BUCKET_TO_OURS.items()}

    # **机制标签必须按回放时点取，不能取"最终标签"。** review gate 报出的 blocker：
    # `select._load_candidates` 那条子查询是 `ORDER BY en.id DESC LIMIT 1`、**无时间约束**，
    # 于是 `c.primary_category` 是快照时刻的最终标签。实测窗口内只有 62.9% 的条目在发布后 6h 内
    # 被 enrich、9.0% 晚于 72h，每天有 28%–39% 的 enrich 行晚于当天最后一个回放时点
    # ⇒ 用最终标签等于把数小时后才产生的标签提前交给机制（近期窗口开天眼），
    # 而早期窗口又比生产更盲，两个方向叠加。
    # 仓内已有正确量的定义：ADR-9e21 §二 的 `enrich_watermark`；这里等价地按 `evaluated_at` 截断。
    enrich_hist: dict[str, list[tuple[str, str]]] = {}
    for item_id, ev_at, out, rsv in conn.execute(
        "SELECT item_id, evaluated_at, output_json, ruleset_version FROM item_evaluations "
        "WHERE stage='enrich' AND error IS NULL ORDER BY id"
    ):
        if args.enrich_stamp and not str(rsv or "").startswith(args.enrich_stamp):
            continue
        cat = sel._primary_category(out)
        if cat and ev_at:
            enrich_hist.setdefault(str(item_id), []).append((str(ev_at), cat))
    print(f"机制标签时间轴：{len(enrich_hist)} 条目有 enrich 历史"
          f"（共 {sum(len(v) for v in enrich_hist.values())} 行）")

    # **目标也要换到机制的标签空间。** review gate 的 blocker 1：只换机制**读**的标签、
    # 不换它**比**的目标，`tgt/act` 的分子分母就来自两个标注器。实测两者在同一批 571 条
    # 双标注过闸候选上的边际差是 tip −14.01pp / industry +12.78pp / model +7.88pp
    # ⇒ 一张在 AIHOT 口径下恰好达标的页面，机制会读成「industry 超 12.8、tip 欠 14」，
    # 照这个**假 gap** 去拧。model 那一格的偏差（+7.88）与要修的缺口（−9.30）同量级且反向,
    # 于是机制在 `ours` 下**根本看不见 model 缺口**。
    # 修法：目标 = **我方标注器在 AIHOT 精选条目上数出来的构成**。它离线可得
    # （capture + 我方对同一批条目的 enrich），因此不是 oracle；用最终标签即可——
    # 目标是离线算的，没有时点压力，这与机制**读**条目标签必须截断是两回事。
    id_by_url = {u: i for i, u in url_by_id.items()}
    if args.permute_labels is not None:
        # 保边际置换：把 item_id → enrich 历史 的对应关系整体打乱。
        # 打乱的是**对应关系**不是类别取值，所以每个类别的总条数一个不变。
        import random as _rnd
        keys = sorted(enrich_hist)
        vals = [enrich_hist[k] for k in keys]
        _rnd.Random(args.permute_labels).shuffle(vals)
        enrich_hist = dict(zip(keys, vals, strict=True))
        print(f"⚠️ 零控制：机制标签已按 seed={args.permute_labels} 置换（边际不变）")
    final_cat = {i: rows[-1][1] for i, rows in enrich_hist.items() if rows}
    target_by_day: dict[str, Counter] = {}
    _t_hit = _t_miss = 0
    for rday, urls in ref_urls_by_day.items():
        cnt = target_by_day.setdefault(rday, Counter())
        for u in urls:
            cat = final_cat.get(id_by_url.get(u, ""))
            b = OURS_TO_BUCKET.get(cat, cat) if cat else None
            if b in CATS:
                cnt[b] += 1
                _t_hit += 1
            else:
                _t_miss += 1
    # **`corrected` = 用混淆矩阵把 AIHOT 目标**反解**到我方标签空间。**
    # 为什么 `ours`（正向）是错的：我们要的是「页面**用 AIHOT 标签量出来**等于目标」，
    # 即 `M q = t`（M[i][j] = P(AIHOT=i | ours=j)）⇒ `q = M⁻¹t`。
    # 而 `target_space=ours` 数的是 `P(ours | AIHOT 精选)`，那是**正向**映射 `Mᵀt`——
    # 它和 `M⁻¹t` 落在 `t` 的两侧，所以不校正（q=t）反而在中间、读数居中。实测三档
    # 全窗 TV：aihot 0.131 / ours 0.168 / 基线 0.175。
    # M 由**双标注过闸候选**估计（n=681，det 0.207，逆解无负数），不是 oracle：
    # 它只用 capture 和我方对同一批条目的 enrich，离线可得。
    # **按 AIHOT 发布日分桶**：决策评审报出，全量估一次 M 再在所有切点复用 ⇒ 跨 split 根本
    # 没给 M 留出数据，四个切点全是 in-sample。同一类错误台账里记过一次（v2 enrich 过拟合）。
    # `--causal-m` 让每个窗口只用**严格更早**的双标注条目估 M，与 `hist` 的因果纪律同构。
    conf_by_day: dict[str, Counter] = {}
    conf_counts: Counter = Counter()
    for c in candidates:
        if c.weighted_score < sel.DEFAULT_THRESHOLD:
            continue
        u = url_by_id.get(c.item_id, "")
        a = label_by_url.get(u)
        # **读 `final_cat` 而不是 `c.primary_category`**：两者在正常档等价，但零控制置换的是
        # `enrich_hist` ⇒ 只有走 `final_cat` 才让混淆矩阵也跟着置换。不然零控制只置换了一半，
        # 矩阵仍带着真信号，对照就失效了（而它失效的样子与生效完全一样）。
        o0 = final_cat.get(str(c.item_id))
        o = OURS_TO_BUCKET.get(o0, o0) if o0 else None
        if a in CATS and o in CATS:
            conf_counts[(a, o)] += 1
            pd = pub_by_url.get(u)
            if pd:
                conf_by_day.setdefault(pd, Counter())[(a, o)] += 1

    def solve_to_ours(t_counter, conf=None):
        """把 AIHOT 空间的目标计数反解成我方空间的占比。解不出就返回 None（调用方回退）。"""
        conf = conf_counts if conf is None else conf
        nt = sum(t_counter.values())
        if not nt or sum(conf.values()) < 100:
            return None
        colN = [sum(conf[(a, CATS[j])] for a in CATS) for j in range(5)]
        if min(colN) < 10:          # 某列样本太少 ⇒ 那一列的条件概率不可信，不求逆
            return None
        A = [[conf[(CATS[i], CATS[j])] / colN[j] for j in range(5)]
             + [t_counter[CATS[i]] / nt] for i in range(5)]
        for i in range(5):          # 高斯-约当，带部分主元
            pv = max(range(i, 5), key=lambda r: abs(A[r][i]))
            if abs(A[pv][i]) < 1e-9:
                return None
            A[i], A[pv] = A[pv], A[i]
            for r in range(5):
                if r != i:
                    f = A[r][i] / A[i][i]
                    for cc in range(i, 6):
                        A[r][cc] -= f * A[i][cc]
        q = [A[i][5] / A[i][i] for i in range(5)]
        # 负数即外推出了数据支持的范围；夹到一个小正数再归一，别让它把某类彻底封死。
        q = [max(x, 0.01) for x in q]
        tot = sum(q)
        return {CATS[i]: q[i] / tot for i in range(5)}

    if args.labels == "oracle" or args.target_space in ("aihot", "corrected"):
        target_by_day = reference_by_day
        why = ("--labels oracle" if args.labels == "oracle"
               else f"--target-space {args.target_space}")
        extra = ""
        if args.target_space == "corrected" and args.labels != "oracle":
            probe = solve_to_ours(sum(reference_by_day.values(), Counter()))
            extra = ("；反解不可用、将回退 aihot" if probe is None
                     else "；每窗在使用点反解到我方空间")
        print(f"目标空间：AIHOT 标签累积（{why}{extra}）")
    else:
        print(f"目标空间：我方 enrich 在 AIHOT 精选条目上重数"
              f"（命中 {_t_hit}、我方无标签 {_t_miss}）")

    quota_override = None
    if args.per_source is not None:
        quota_override = sel.SourceQuota(
            kind_caps=dict(sel.DEFAULT_SOURCE_QUOTA.kind_caps),
            per_source=(None if args.per_source <= 0 else args.per_source))
        print(f"源配额：per_source {sel.DEFAULT_SOURCE_QUOTA.per_source} → "
              f"{quota_override.per_source}（kind_caps 不动，ADR-bc36 是两条）")

    # **条目重合读数**：模拟器至今只报构成。两者是不同的用户可见指标——
    # 构成可以 5/5 而重合仍然只有 39%，一个达标不蕴含另一个。
    prod_curated_urls = {
        url_by_id[str(r[0])] for r in conn.execute(
            "SELECT DISTINCT item_id FROM curated_items")
        if str(r[0]) in url_by_id}
    sel_urls = {_comp.normalize_url(r["url"])[0]
                for r in aihot.values() if r["selected"] and r.get("url")
                and r["published"] in set(days)}
    # **同一条新闻可以有两个 URL。** `buzzing_hn` 是中文转载聚合，AIHOT 常链它，
    # 而我方去重（`dedup.py` 按 content_hash 或小写 URL）正确地留下了原始源那一份
    # （openai_blog / anthropic_research / nvidia）。于是按 URL 匹配会把**页面上确实有的**
    # 新闻算成漏选——实测 6/6 都能在池里找到同一条。⇒ 重合要按 **URL 或 content_hash** 匹配。
    hash_by_item = {}
    for i, h in conn.execute("SELECT id, content_hash FROM items WHERE content_hash IS NOT NULL"):
        hash_by_item[str(i)] = str(h)
    _rev_url = {u: i for i, u in url_by_id.items()}
    sel_hashes = {hash_by_item[_rev_url[u]] for u in sel_urls
                  if u in _rev_url and _rev_url[u] in hash_by_item}

    clock = {"now": "9999"}  # 由时点循环每轮写入；`mech_label` 读它

    def mech_label(c):
        """**机制**看到的类别——与计分器看到的严格分开，且**按回放时点截断**。

        本 session 踩过的最贵一次：闭环的反馈与 quota 封顶都直接读 `label_by_url`
        （AIHOT 自己的标签），而计分器读的也是它 ⇒ 执行器与记分员共用同一份答案。
        更糟的是 `if b:` 那道守卫——**AIHOT 没发过的条目拿不到标签、于是完全绕过封顶**。
        那样得到的 5/5 在生产里无法复现：生产拿不到 AIHOT 对我方条目的标签。

        默认 `ours` 取我方 enrich 在 `clock["now"]` 之前**已经产出**的最新一行；
        `oracle` 保留为上界探针，**不是候选方案**。
        """
        if args.labels == "oracle":
            return label_by_url.get(url_by_id.get(c.item_id, ""))
        if args.label_time == "final":
            cat = final_cat.get(str(c.item_id))
        else:
            cat = None
            for ev_at, k in enrich_hist.get(str(c.item_id), ()):
                if ev_at <= clock["now"]:
                    cat = k
                else:
                    break
        if cat is None:
            return None
        b = OURS_TO_BUCKET.get(cat, cat)
        return b if b in CATS else None


    def gate_score(c):
        return c.weighted_score

    # 时点按上海时刻均分一天。可用性用 `published_at <= t` 近似——**没发布就选不到**；
    # 更准的是 `fetched_at`，但它不在 `ScoredCandidate` 上，而两个 arm 同样近似 ⇒ 配对不受影响。
    union: dict = {}
    union_lab: Counter = Counter()
    src_in_union: Counter = Counter()
    ours_pooled: Counter = Counter()
    reference_pooled: Counter = Counter()
    # 闭环状态：`hist` 只累计**严格早于当前时点**的窗口（不读当日的 AIHOT——生产里选稿那一刻
    # 今天的 AIHOT 还没发，读它就是泄漏）。`live` 是当前系数，从生产值起步。
    # **暖机用得上的历史不止被评的那几天。** `days` 只留「AIHOT 当日精选 >= 5」的窗口
    # （那是**判据**的门槛，为的是逐日 CI 有意义），但控制器要的只是一个构成估计——
    # 精选 1–4 条的天同样是严格更早的参照数据，丢掉它们纯属浪费，暖机因此白多熬几窗。
    all_ref_days = sorted(reference_by_day)
    merged_ref: set = set()
    # **累加器与使用值必须分开**：`hist_raw` 逐窗累积（恒在 AIHOT 空间），
    # `hist` 是本窗口交给机制的那一份。写成同一个变量时，`corrected` 档会把校正结果
    # 当成下一窗的累加基数，误差逐窗复利——写这一段时就踩了一次。
    hist_raw: Counter = Counter()
    hist: Counter = Counter()
    live = {c: overrides.get(BUCKET_TO_OURS.get(c, c), 1.0) for c in CATS}
    skipped_days: list[str] = []
    m_log: list[tuple[str, int, str]] = []
    frozen_conf = None
    if args.fixed_m_until:
        frozen_conf = Counter()
        for rd, cc in conf_by_day.items():
            if rd < args.fixed_m_until:
                frozen_conf += cc
        print(f"固定 M：只用发布早于 {args.fixed_m_until} 的双标注条目估一次并冻结"
              f"（样本 {sum(frozen_conf.values())} 条）")
    print(f"\n{'上海日':12}{'并集':>7}{'页内有标签':>11}{'当日发布占比':>13}  逐类（AIHOT 标签）")
    for day in days:
        # **机制动作计数**：review gate 报出「机制跑了但没用」与「机制一次也没触发」
        # 在输出上同形——封顶对无标签条目直接放行，于是它的强度正比于机制覆盖率，
        # 而此前没有任何一列报告那个覆盖率（逐日那列和 `union_lab` 用的都是 AIHOT 标签）。
        mech: Counter = Counter()
        # 先把**所有**严格早于本窗口、且尚未并入的参照日补进历史（含精选 <5 的那些）。
        for rd in all_ref_days:
            if rd < day and rd not in merged_ref:
                hist_raw += target_by_day.get(rd, Counter())
                merged_ref.add(rd)
        # `corrected` 档：累积恒在 AIHOT 空间，在**使用点**反解到我方空间。
        # 放在天这一层是因为 `hist` 一天内不变。
        hist = Counter(hist_raw)
        if args.target_space == "corrected" and args.labels != "oracle":
            conf_now = None
            if args.fixed_m_until:
                conf_now = frozen_conf
            elif args.causal_m:
                conf_now = Counter()
                for rd, cc in conf_by_day.items():
                    if rd < day:
                        conf_now += cc
            q = solve_to_ours(hist_raw, conf_now)
            # **逐窗报 M 的状态**：复核轮点名——滚动重估 + 样本不足静默回退，
            # 会把不同机制状态混进一个汇总读数，而输出里看不出是哪一种产生了改善。
            n_m = sum((conf_now if conf_now is not None else conf_counts).values())
            m_log.append((day, n_m, "反解成功" if q is not None else "**回退未校正**"))
            if q is not None:
                nh = sum(hist_raw.values())
                hist = Counter({c: q[c] * nh for c in CATS})
        y, m, d = (int(x) for x in day.split("-"))
        for k in range(args.per_day):
            # **`blocked_b` 每轮重置，不是每天。** 放在天这一层时，第 1 轮找不到该类候选就把
            # 当天余下 47 轮全部封死——而每一轮的已选集合不同、可换入的候选也不同。
            # 台账警告过「一类补不到就停掉整个下限机制」，我换了个尺度又犯了一次。
            blocked_b: set = set()
            hour = round(24 * (k + 1) / args.per_day)
            t = datetime(y, m, d, tzinfo=UTC) - SH + timedelta(hours=hour)
            iso = t.isoformat().replace("+00:00", "Z")
            clock["now"] = iso  # `mech_label` 按它截断；不设就等于用最终标签（blocker 2）
            avail = [c for c in candidates if c.published_at and c.published_at <= iso]
            if not avail:
                continue
            tday = sel._shanghai_date(iso)
            pool = [c for c in avail if sel._shanghai_date(c.published_at) == tday]
            # **覆盖率要在这里数，不能数在 quota 块里**：基线不跑 quota，数在那里会让
            # `--score-only-covered` 把基线的每一天都判成"未覆盖"、整个跳过，配对就不成立了。
            for _c in pool:
                mech["池内有标签" if mech_label(_c) else "池内无标签"] += 1
            if args.closed_loop and sum(hist.values()) >= 8:
                # 反馈取**归档页此刻的构成**，不是这一轮选了什么——后者是上游量，
                # 而用户看到的是前者；本 session 已实测两者的失败类不同。
                seen = sorted(union.values(), key=lambda c: (c.published_at, c.item_id),
                              reverse=True)[: args.page]
                cur = Counter()
                for c in seen:
                    b = mech_label(c)
                    if b:
                        cur[b] += 1
                n_cur, n_hist = sum(cur.values()) or 1, sum(hist.values())
                for b in CATS:
                    tgt = hist[b] / n_hist
                    act = cur[b] / n_cur
                    if tgt <= 0 or act <= 0:
                        continue
                    k = BUCKET_TO_OURS.get(b, b)
                    # **别用 m**：外层 `y, m, d` 的 m 是月份，覆盖它会让下一天的
                    # `datetime(y, m, d)` 拿到浮点数而报 TypeError（实测踩过）。
                    adjusted = live[b] * (tgt / act) ** args.gain
                    live[b] = min(args.clip, max(1 / args.clip, adjusted))
                    overrides[k] = live[b]
                    mech["调参"] += 1
            if args.closed_loop and args.actuator.startswith("quota") and sum(hist.values()) >= 8:
                # **封顶在选择之内**：给每类的当日候选按目标占比设上限，随后照常交 `replay_day`，
                # 由 `_fill` 用别类与尾部把空出的格位填满 ⇒ 格位数不减、时效分段不被绕开。
                # 这是 admit 那版「直接丢弃」的修正：丢弃会让页面被更旧的条目补上。
                # **最大余额法，不是 ceil 也不是截断。** 两头都错过：`ceil` 对小类系统性放水
                # （paper 目标 8.8% ⇒ ceil(40×0.088)=4 ⇒ 实际上限 10%，高 1.2pp，实测它就是
                # paper 在生产节奏下出界 +0.22pp 的来源）；而 `int()` 截断反向把 paper 压到 3.8%
                # 打出界（台账记过）。最大余额让五类上限精确加总到 limit。
                n_hist = sum(hist.values())
                exact = {b: args.limit * hist[b] / n_hist for b in CATS}
                capn = {b: max(1, int(exact[b])) for b in CATS}
                for b in sorted(CATS, key=lambda b: exact[b] - int(exact[b]), reverse=True):
                    if sum(capn.values()) >= args.limit:
                        break
                    capn[b] += 1
                seen_b: Counter = Counter()
                capped = []
                for c in sorted(pool, key=rank):
                    b = mech_label(c)
                    if b:
                        if seen_b[b] >= capn.get(b, args.limit):
                            mech["封顶丢弃"] += 1
                            continue
                        seen_b[b] += 1
                    capped.append(c)
                pool = capped
            picked = _comp.replay_day(pool, avail, args.limit, rank, gate_score,
                                      no_quota=args.no_quota,
                                      source_quota=quota_override)
            if args.closed_loop and args.actuator == "quota+floor" and sum(hist.values()) >= 8:
                # **下限**：封顶是上界，它压得住超配、造不出短缺。实测 model 在早期欠 4.9pp，
                # 而那些条目够得着（AIHOT 的 model 精选我方库里 43 条、19 条过 6.5 闸），
                # 只是排不进前 40 ⇒ 缺的正是这一半。
                # 换入从 `avail` 取（不限当日），换出取超配类里排名最低的那条；
                # 单源与 kind 上限按**换完之后**判（先算 drop 再判，顺序反了会把
                # 「换掉一条 X、换入另一条 X」误判成超限——台账记过）。
                for _ in range(args.limit):
                    have = Counter()
                    for c in picked:
                        b = mech_label(c)
                        if b:
                            have[b] += 1
                    n_lab = sum(have.values()) or args.limit
                    # **最大余额法，别用 int()**——上一处封顶刚因取整错过一次，这里重犯了一次：
                    # 截断让每类 `want` 都偏低 ⇒ 更多类判成超配、更少类判成欠配，
                    # floor 该补的没补、该留的被丢（实测 model 由 −4.90 恶化到 −7.39pp）。
                    nh = sum(hist.values())
                    ex = {b: n_lab * hist[b] / nh for b in CATS}
                    want = {b: int(ex[b]) for b in CATS}
                    for b in sorted(CATS, key=lambda b: ex[b] - int(ex[b]), reverse=True):
                        if sum(want.values()) >= n_lab:
                            break
                        want[b] += 1
                    short = [b for b in CATS if have[b] < want[b] and b not in blocked_b]
                    over = [b for b in CATS if have[b] > want[b]]
                    if not short or not over:
                        break
                    b_in = min(short, key=lambda b: have[b] - want[b])
                    b_out = max(over, key=lambda b: have[b] - want[b])
                    outs = [c for c in picked
                            if mech_label(c) == b_out]
                    if not outs:
                        break
                    drop = max(outs, key=rank)
                    rest = [c for c in picked if c.item_id != drop.item_id]
                    src_n = Counter(c.source_id for c in rest)
                    kind_n = Counter(c.kind for c in rest)
                    ids = {c.item_id for c in picked}

                    def ok(c, _s=src_n, _k=kind_n):
                        sq = sel.DEFAULT_SOURCE_QUOTA
                        if sq.per_source is not None and \
                                (_s[c.source_id] + 1) / args.limit > sq.per_source + 1e-9:
                            return False
                        cap = sq.kind_caps.get(c.kind)
                        return cap is None or (_k[c.kind] + 1) / args.limit <= cap + 1e-9

                    # **换入只从当日池取，不从 `avail`（全部合规候选）取。** 归档页按发布时间
                    # 排序 ⇒ 硬塞进来的**旧**条目进不了第 1 页，却把本来进得去的较新条目挤掉；
                    # 实测用 `avail` 时 model 反而由 −4.90 恶化到 −6.53pp、n_ours 由 169 掉到 155。
                    add = next((c for c in sorted(pool, key=rank)
                                if c.item_id not in ids
                                and mech_label(c) == b_in
                                and ok(c)), None)
                    if add is None:
                        # 这一类今天补不到合规候选，标记后继续补别的类——**不是 break**，
                        # 否则一类补不到就把整个下限机制停掉（台账记过这个形态）。
                        blocked_b.add(b_in)
                        continue
                    picked = rest + [add]
            if args.closed_loop and args.actuator == "admit" and sum(hist.values()) >= 8:
                # **准入控制**：一条只在「收了它之后该类仍不超目标」时才进并集。
                # 并集单调增长，所以这条规则天然把它按住在目标构成上。
                # 与排序系数的区别是作用点：排序决定谁排在前面，准入决定谁**进得来**——
                # 高节奏下几乎人人都排得上，于是只有后者还咬得住。
                n_hist = sum(hist.values())
                cur = Counter()
                for c in union.values():
                    b = mech_label(c)
                    if b:
                        cur[b] += 1
                kept = []
                for c in picked:
                    b = mech_label(c)
                    if b:
                        tot = sum(cur.values()) + 1
                        if (cur[b] + 1) / tot > hist[b] / n_hist + 1e-9:
                            continue
                        cur[b] += 1
                    kept.append(c)
                picked = kept
            for c in picked:
                if args.src_cap is not None and c.item_id not in union:
                    # 并集层面的单源上限。只拦**新进来的**条目，已在并集里的不动——
                    # 并集单调增长，回头删会让"某条曾经在页面上"这件事不可复现。
                    # **比例上限要带一个绝对下限**，否则并集为空时 (0+1)/1 = 1.0 > cap，
                    # 第一条就被拒、整个并集饿死（实测 `tv` 直接变 None）。
                    # 生产的 `per_source` 用 `max(1, round(...))` 正是同一个理由。
                    n_now = len(union) + 1
                    allow = max(3.0, args.src_cap * n_now)
                    if src_in_union[c.source_id] + 1 > allow + 1e-9:
                        continue
                    src_in_union[c.source_id] += 1
                union.setdefault(c.item_id, c)
        page = sorted(union.values(), key=lambda c: (c.published_at, c.item_id), reverse=True)[: args.page]
        labelled = Counter()
        for c in page:
            cat = label_by_url.get(url_by_id.get(c.item_id, ""))
            if cat:
                labelled[cat] += 1
        # **并集与第 1 页的构成要分开量。** 归档页按发布时间取前 40 ⇒ 即使并集里某类很充足，
        # 第 1 页也只看得见最新那 40 条。两者差多少，就是「排序口径」而非「供给」欠的那一截——
        # 这条读数是用来把它俩分开的，别只看页面。
        union_lab = Counter()
        for c in union.values():
            b = label_by_url.get(url_by_id.get(c.item_id, ""))
            if b:
                union_lab[b] += 1
        same = sum(1 for c in page if sel._shanghai_date(c.published_at) == day)
        nb = mech["池内有标签"] + mech["池内无标签"]
        cov = f"{100 * mech['池内有标签'] / nb:.0f}%" if nb else "—"
        print(f"{day:12}{len(union):>7}{sum(labelled.values()):>11}"
              f"{100 * same / max(len(page), 1):>12.1f}%  "
              + " ".join(f"{c[:3]}:{labelled[c]}" for c in CATS if labelled[c])
              + f"   │机制 覆盖{cov} 封顶丢{mech['封顶丢弃']} 调参{mech['调参']}")
        nb_cov = mech["池内有标签"] + mech["池内无标签"]
        covered = (not args.score_only_covered) or mech["池内有标签"] > 0
        # **固定 M 是留出验证**：M 的估计样本来自早于 `--fixed-m-until` 的窗口，
        # 那些窗口自己就不能进评分，否则是 in-sample。并集照常从第一天累积。
        if args.fixed_m_until and day < args.fixed_m_until:
            covered = False
        if covered:
            ours_pooled += labelled
            reference_pooled += reference_by_day[day]
        else:
            skipped_days.append(day)
        # **顺序不能反**：本窗口的 AIHOT 直到这里才并进历史，之上的每一次调参都只看得到
        # 严格更早的窗口。反过来就是拿当天的真值去调当天的系数。
        if day not in merged_ref:
            hist_raw += target_by_day.get(day, Counter())
            merged_ref.add(day)

    if union_lab:
        nu = sum(union_lab.values())
        np_ = sum(ours_pooled.values()) or 1
        print(f"\n>>> 并集 vs 第 1 页（末窗口并集 n={nu}）——把「供给」与「排序口径」分开")
        print(f"{'类别':10}{'并集占比':>10}{'页面占比(合并)':>16}{'AIHOT':>9}")
        for b in CATS:
            print(f"{b:10}{100 * union_lab[b] / nu:>9.1f}%{100 * ours_pooled[b] / np_:>15.1f}%"
                  f"{100 * reference_pooled[b] / (sum(reference_pooled.values()) or 1):>8.1f}%")
        print("    读法：并集里够、页面里不够 ⇒ 差在**按发布时间取前 40**这条排序，不是供给。")
        # **并集的源集中度：ADR-bc36 明确不约束的那个量。** 它的 `per_source ≤ 7.5%` 是
        # **per-run** 的，而归档面是 48 轮的并集——一个源可以每轮都占满 7.5%，在并集里
        # 仍然占 7.5%，也可以更高（它在别的轮里没被挤掉）。所以"并集有没有被源结构带偏"
        # 这件事，生产侧任何读数都答不出来，只能在这里量。
        by_src: Counter = Counter()
        for c in union.values():
            by_src[c.source_id] += 1
        top = by_src.most_common(3)
        n_all = sum(by_src.values()) or 1
        print(f"    并集源集中度：{len(by_src)} 个源，最大单源 "
              + "、".join(f"{s}={100 * n / n_all:.1f}%" for s, n in top))

    if sel_urls:
        in_union = {url_by_id.get(c.item_id, "") for c in union.values()}
        union_hashes = {hash_by_item.get(c.item_id) for c in union.values()}
        # 按 URL 命中的，加上「URL 没中但 content_hash 中了」的那些
        hit_url = sel_urls & in_union
        hit_hash = {u for u in sel_urls - hit_url
                    if u in _rev_url and hash_by_item.get(_rev_url[u]) in union_hashes}
        hit = len(hit_url) + len(hit_hash)
        print(f"\n>>> 条目重合（按 URL 或 content_hash）：URL 命中 {len(hit_url)}"
              f"，另有 {len(hit_hash)} 条 URL 不同但**同一条新闻**（转载/多源）")
        # **把「生产历史没选过」与「今天的代码也不会选」分开。** 两者极易混淆：
        # `curated_items` 是全部历史（多代代码），而本模拟器跑的是今天这份代码。
        # 构成那条线上同一个陷阱已经咬过一次。
        never = {u for u in sel_urls if u not in prod_curated_urls}
        print(f"\n>>> 生产历史从未精选过的 AIHOT 条目：{len(never)}/{len(sel_urls)}；"
              f"其中**今天这份代码会选进并集的**：{len(never & in_union)} 条")
        # **对着当前代码重算漏选拆分**：四格的修法互斥，而按生产历史算分母
        # 会把已经修好的东西排进待办（本轮实测：34 条属于这一类）。
        miss = sel_urls - in_union
        pool_ids = {c.item_id for c in candidates}
        gated = {c.item_id for c in candidates
                 if c.weighted_score >= sel.DEFAULT_THRESHOLD}
        b: Counter = Counter()
        for u in miss:
            iid = id_by_url.get(u)
            if iid is None:
                b["① 不在 items 表（信源够不着）"] += 1
            elif iid not in pool_ids:
                b["② 在 items 但不在候选池（上游过滤）"] += 1
            elif iid not in gated:
                b["③ 在池里但不过闸（打分）"] += 1
            else:
                b["④ 过了闸仍进不了并集（纯排序/配额/新鲜段）"] += 1
        print(f"    当前代码仍漏的 {len(miss)} 条，逐格（互斥，修法不同）：")
        for k in sorted(b):
            print(f"      {k:<40}{b[k]:>4}  {100 * b[k] / max(len(miss), 1):>5.1f}%")
        print(f"\n>>> 条目重合（末窗口并集 vs 窗口内 AIHOT 精选）："
              f"{hit}/{len(sel_urls)} = {100 * hit / len(sel_urls):.1f}%"
              f"   并集 {len(union)} 条")

    if m_log:
        print(f"\n>>> 混淆矩阵 M 逐窗状态（复核轮要求：样本量 / 是否反解成功 / 是否回退）")
        for d, n, st in m_log:
            print(f"    {d:12}M 样本 {n:>5} 条   {st}")
        nf = sum(1 for _, _, st in m_log if "回退" in st)
        print(f"    ⇒ {len(m_log)} 个窗口中 {nf} 个回退到未校正目标"
              + ("（**改善不能全归给校正**）" if nf else "（全部用校正目标，无混合状态）"))

    if skipped_days:
        print(f"\n⚠️ --score-only-covered：{len(skipped_days)} 个窗口机制覆盖率为 0、不计入评分"
              f"（{', '.join(skipped_days)}）；**并集仍从第一天累积**，只是这些天的页面不进 pooled。")

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
