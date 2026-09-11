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
    hist: Counter = Counter()
    live = {c: overrides.get(BUCKET_TO_OURS.get(c, c), 1.0) for c in CATS}
    print(f"\n{'上海日':12}{'并集':>7}{'页内有标签':>11}{'当日发布占比':>13}  逐类（AIHOT 标签）")
    for day in days:
        # 先把**所有**严格早于本窗口、且尚未并入的参照日补进历史（含精选 <5 的那些）。
        for rd in all_ref_days:
            if rd < day and rd not in merged_ref:
                hist += reference_by_day[rd]
                merged_ref.add(rd)
        y, m, d = (int(x) for x in day.split("-"))
        for k in range(args.per_day):
            # **`blocked_b` 每轮重置，不是每天。** 放在天这一层时，第 1 轮找不到该类候选就把
            # 当天余下 47 轮全部封死——而每一轮的已选集合不同、可换入的候选也不同。
            # 台账警告过「一类补不到就停掉整个下限机制」，我换了个尺度又犯了一次。
            blocked_b: set = set()
            hour = round(24 * (k + 1) / args.per_day)
            t = datetime(y, m, d, tzinfo=UTC) - SH + timedelta(hours=hour)
            iso = t.isoformat().replace("+00:00", "Z")
            avail = [c for c in candidates if c.published_at and c.published_at <= iso]
            if not avail:
                continue
            tday = sel._shanghai_date(iso)
            pool = [c for c in avail if sel._shanghai_date(c.published_at) == tday]
            if args.closed_loop and sum(hist.values()) >= 8:
                # 反馈取**归档页此刻的构成**，不是这一轮选了什么——后者是上游量，
                # 而用户看到的是前者；本 session 已实测两者的失败类不同。
                seen = sorted(union.values(), key=lambda c: (c.published_at, c.item_id),
                              reverse=True)[: args.page]
                cur = Counter()
                for c in seen:
                    b = label_by_url.get(url_by_id.get(c.item_id, ""))
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
                    b = label_by_url.get(url_by_id.get(c.item_id, ""))
                    if b:
                        if seen_b[b] >= capn.get(b, args.limit):
                            continue
                        seen_b[b] += 1
                    capped.append(c)
                pool = capped
            picked = _comp.replay_day(pool, avail, args.limit, rank, gate_score)
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
                        b = label_by_url.get(url_by_id.get(c.item_id, ""))
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
                            if label_by_url.get(url_by_id.get(c.item_id, "")) == b_out]
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
                                and label_by_url.get(url_by_id.get(c.item_id, "")) == b_in
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
                    b = label_by_url.get(url_by_id.get(c.item_id, ""))
                    if b:
                        cur[b] += 1
                kept = []
                for c in picked:
                    b = label_by_url.get(url_by_id.get(c.item_id, ""))
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
        print(f"{day:12}{len(union):>7}{sum(labelled.values()):>11}"
              f"{100 * same / max(len(page), 1):>12.1f}%  "
              + " ".join(f"{c[:3]}:{labelled[c]}" for c in CATS if labelled[c]))
        ours_pooled += labelled
        reference_pooled += reference_by_day[day]
        # **顺序不能反**：本窗口的 AIHOT 直到这里才并进历史，之上的每一次调参都只看得到
        # 严格更早的窗口。反过来就是拿当天的真值去调当天的系数。
        if day not in merged_ref:
            hist += reference_by_day[day]
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
