#!/usr/bin/env python3
"""精选构成 vs AIHOT 自己的精选构成，**两边都用 AIHOT 的类别标签**。

为什么要有这个脚本：此前所有构成读数都是拿**我方**分类器的标签比**AIHOT**的标签，而两个标注器
在同一批内容上的逐条一致率只有 68.5%，光是这份分歧就值 0.129 的总变差——比要测的差距还大。
本脚本只用 AIHOT 自己发布的类别，因此没有标注器混淆。代价见下面「边界」。

它同时是 `select.CATEGORY_MULTIPLIERS` 的**发现通道**：改了系数、或 enrich prompt 改版之后，
跑它一次就能看出页面构成往哪边走。

用法（不写库、不出网；**传 `--record` 时会追加一行到历史序列文件**，那是唯一的写盘路径）：

    uv run python scripts/eval/measure_curated_composition.py
    uv run python scripts/eval/measure_curated_composition.py --multiplier paper=1.0   # 对照
    uv run python scripts/eval/measure_curated_composition.py --depth ours --labels off --record
                                                            # ^ 达标线的**权威口径** + 记一轮迭代

**权威口径是 `--depth ours`，不是 `--depth aihot`。** 目标写的是"用户可见的指标"，用户看到的就是
生产那 40 条；而且它的 n 是对齐深度的 2.6 倍，**判据只在这里有分辨力**——实测判据自身零假设
P(5/5) 生产深度 0.949（`3/5` ⇒ p=0.002，真信号）vs 对齐深度 0.602（`3/5` ⇒ p=0.102，判不出）。
对齐深度留作归因辅助：它分离"深度选择"与"排序质量"，但样本量不够判达标。
（本 docstring 第一版把 `--depth aihot` 写成权威口径，与同一轮定下的决策相反——复核轮报出。
文件头是下一个操作者唯一会照抄的东西，照抄它就会把一个不具分辨力的读数写进趋势文件。）

**边界，读数之前必须知道三条**：

1. **覆盖率现在是打印出来的读数，不是写死在这里的一句话。** 每次运行都报「标注/版面」逐窗与
   合并两档；未达 100% 时 POOLED 是**已标注子集**的构成，不是整页构成——脚本会自己这么说。
   `--labels` 指向的补充标注补上 AIHOT 未收录的那些条目，覆盖率随标注累积而涨。
   补充标注**不等于 AIHOT 的标签**：它的标注者逐条记在文件里（当前那一栏是个模型名，
   不是人），校准读数只在它与 AIHOT 有重叠条目时才产生，脚本会说有没有。
   （历史订正：这里原先写死「约 30%」。那个数取自**当天的实时页面**——最新的条目 AIHOT 往往
   还没发布或匹配不上。9 个历史日窗重放实测是 **234/360 = 65.0%**。一个写死的常数会在两种
   口径上各错一次，所以改成每次现算。）
2. **逐日构成 TV 饱和于抽样噪声**：AIHOT 自己每天与它自己的合并均值就差 ~0.27，而按当日条数
   （5–35）从合并分布重抽的纯噪声已有 ~0.25。所以只读 `POOLED` 那一行，逐日行仅供查异常。
3. 两边的**截断深度不同**（我方 40 条/天、AIHOT 十几条），而类别在排序上分布不均，故深度本身就
   造成构成差。`--depth aihot` 用 AIHOT 当日条数重取一遍，供对齐深度比较。
"""

from __future__ import annotations

import argparse
import dataclasses
import gzip
import hashlib
import json
import math
import random
import sqlite3
import statistics
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from airadar.curator import select as sel  # noqa: E402
from airadar.curator.weights import DEFAULT_WEIGHTS  # noqa: E402
from airadar.eval.aihot_fit.build import normalize_url  # noqa: E402

SUBMODULE = REPO / "benchmarks" / "aihot"
CAPTURES_REF = "origin/captures/daily"
# Next to this script, NOT under data/: the production deploy refuses any commit that
# tracks a data/ path (runtime-owned). Measured the hard way on 09dea35.
DEFAULT_LABELS = Path(__file__).resolve().parent / "labels" / "page-categories.jsonl"
# 达标线读数的历史序列。同样不在 data/ 下，理由同上；而它必须 git-tracked，因为「随迭代轮次
# 逐步逼近 AIHOT 的 CI」这条期望（用户 2026-09-10）只有在读数**跨 session 存活**时才验证得了。
DEFAULT_HISTORY = Path(__file__).resolve().parent / "composition-history.jsonl"

# AIHOT 的 slug -> **AIHOT 自己的桶名**。这里刻意不翻译成我方的五类名，但理由不是「两套口径不同」。
#
# **我方这一侧的口径读起来就是那条界**（AIHOT 自己的类目定义仓内没有记载，见 common.py 的长注释——
# 「两侧同界」是推断，不是引用）。`enrich/prompts_v2.py` 把 `industry` 定义成「一件可指认的商业或制度
# 事件确实发生了」，把 `tutorial` 定义成「现象观察、观点…以及不属于上面四类的其余一切」，平局规则
# 「都不成立才是 tutorial」，字段文档写着「tutorial 是兜底类不是教程」。那就是 AIHOT 的 industry/tip。
#
# **不同的是实测行为，不是定义**（n=1491 双标注，2026-09-10）：
#
#   P(我方标签 | AIHOT=tip)：industry 43.2% · 无标签 23.2% · tutorial 20.9% · product 6.3% · model 6.2%
#   我方标 industry 的 631 条里 302 条（47.9%）AIHOT 标 tip；那些条目 65.9% 是 X 形态短文。
#   抽看 20 条（「a reset a day keeps anthropic away」一类）没有一条满足「一件可指认的事件确实
#   发生了」——**这是 20 条的抽样，不是对 302 条的全称断言**（本注释第一版把它写成了全称）
#
# 所以桶名保持 `tip`：**它命名的是 AIHOT 实际填进去的东西，而我方 `tutorial` 字段实际装的是另一批**。
# 两个名字放在一起会让读者以为可以互换，而 P(我方=tutorial | AIHOT=tip) 只有 20.9%
#（**单向条件概率**，不是对称的「重叠率」；反方向没量）。这是**分类器不遵守自己已有的准入
# 线**，不是口径分歧——归因与下一步见 docs/issues/aihot-fit-eval.md。
#
# （历史：本注释第一版写的是「翻译成 tutorial 缺乏依据、它们不指同一件事」。读了 prompt 正文之后
# 那句是错的——定义指同一件事，行为不指。改名的结论不变，理由换了。）
#
# 注意这条只作用于**跨标注器**的比较：本脚本的 TV 两侧都用 AIHOT 标签，桶名是双射改名，
# 故历史 TV 读数（含 CATEGORY_MULTIPLIERS 的拟合依据）**不受影响**——实测改名前后 POOLED TV
# 都是 0.156。
#
# 生产侧 `classification.PRIMARY_CATEGORY_SLUGS` 把我方 `tutorial` 的 URL slug 也写作 `tip`，
# 那是**用户可见的 URL**，不在本脚本射程内。按上面的订正，那个 slug 其实**取对了**。
SLUG_TO_BUCKET = {
    "ai-products": "product",
    "paper": "paper",
    "ai-models": "model",
    "tip": "tip",
    "industry": "industry",
}
CATEGORIES = ("tip", "model", "product", "industry", "paper")
# 补充标注必须用上面这套 AIHOT 桶名（标注者在模仿 AIHOT 的划分，不是在用我方分类器的词表）。
LABEL_VOCABULARY = "aihot-bucket-v1"


def _git(*args: str) -> bytes:
    """失败即抛。用 check=False 时一次读不到的页会被下游当成 gzip/JSON 异常跳过，
    脚本照样打印出一个看着有效的指标——那是这类工具最坏的失败形态。"""

    done = subprocess.run(["git", "-C", str(SUBMODULE), *args], capture_output=True, check=False)
    if done.returncode != 0:
        raise SystemExit(
            f"git {' '.join(args)} 失败（rc={done.returncode}）: "
            f"{done.stderr.decode(errors='replace')[:400]}"
        )
    return done.stdout


def _first_item_list(payload: object, depth: int = 0) -> list[dict] | None:
    """AIHOT 的分页响应把条目数组埋在若干层字典里，层名不稳定。"""

    if depth > 5:
        return None
    if isinstance(payload, list) and payload and isinstance(payload[0], dict) and len(payload) > 3:
        return payload
    if isinstance(payload, dict):
        for value in payload.values():
            found = _first_item_list(value, depth + 1)
            if found:
                return found
    return None


def load_aihot() -> dict[str, dict]:
    """从 submodule 的 daily capture 分支读 AIHOT 条目。不出网：走 git object store。"""

    captures = _git("ls-tree", "-d", "--name-only", f"{CAPTURES_REF}:captures").decode().split()
    if not captures:
        raise SystemExit(
            f"没有找到 {CAPTURES_REF} 下的 capture。先在 {SUBMODULE} 里 "
            f"`git fetch origin captures/daily`（该分支只在远端）。"
        )
    items: dict[str, dict] = {}
    for capture in captures:
        listing = _git("ls-tree", "-r", "--name-only", CAPTURES_REF, "--", f"captures/{capture}/raw")
        for path in listing.decode().split():
            if not path.endswith(".json.gz"):
                continue
            try:
                page = json.loads(gzip.decompress(_git("show", f"{CAPTURES_REF}:{path}")).decode())
            except (OSError, ValueError):
                continue
            for row in _first_item_list(page) or []:
                if not row.get("id") or row.get("score") is None:
                    continue
                items[row["id"]] = {
                    "url": (row.get("links") or {}).get("original") or "",
                    "category": SLUG_TO_BUCKET.get(row.get("category")),
                    "selected": bool(row.get("selected")),
                    # 上海日，与我方 `sel._shanghai_date` 同一口径。直接截 publishedAt[:10]
                    # 是 UTC 日，两侧会在 UTC 16:00 之后错开一天。
                    "published": sel._shanghai_date(row.get("publishedAt") or ""),
                }
    return items


def load_extra_labels(path: Path) -> tuple[dict[str, str], dict]:
    """读补充标注，并连同它的内容身份一起返回。

    **不要把它叫「人评标注」。** 每条自带 `labeller`，而目前那一栏写的是一个模型名。标注者是
    人还是模型会改变读者对这批标签的信任度，所以身份逐条存、并原样打印出来——把模型标的东西
    印成「人评」，是一次关于证据来源的假陈述。

    身份不是装饰：标注文件会随时间增长，同一条命令在两个时刻会读到不同的标注集，而两次输出
    在别的字段上一模一样。sha256 + 条数是唯一能把两份读数区分开的东西。

    每行一条：{"item_id","category","labeller","labelled_at","vocabulary"}。
    `category` 与 `vocabulary` **逐条校验**：报告里那个词表名此前是无条件打印的常量，于是一份
    用错词表的文件会被原样接收、身份栏却仍显示正确词表。三类坏行分开计数（词表不符 / 桶名不在
    词表 / 解析失败），因为「标了但写错」与「没标」必须读得出区别。
    """

    empty = {"path": str(path), "present": False, "n": 0, "sha256": None}
    if str(path) in ("off", "OFF", "none", ""):
        # 显式关闭。没有这条出路时，唯一的关法是传一个不存在的路径（而 `--labels ""` 会走到
        # `Path(".")`，`exists()` 为真、`read_bytes()` 抛 IsADirectoryError 裸 traceback）。
        # 于是「只用 AIHOT 标签」这个历史口径从入口不可达，而拟合依据正是那个口径。
        return {}, {**empty, "path": "off（显式关闭，只用 AIHOT 标签）"}
    if not path.exists():
        return {}, empty
    if path.is_dir():
        raise SystemExit(f"--labels 指向的是一个目录，不是文件: {path}（要关闭请传 `off`）")
    raw = path.read_bytes()
    labels: dict[str, str] = {}
    bad = {"unparsable": 0, "wrong_vocabulary": 0, "unknown_bucket": 0}
    duplicates = conflicting = 0
    labellers: Counter = Counter()
    for line in raw.decode("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            record = json.loads(line)
            item_id, bucket = str(record["item_id"]), record["category"]
        except (ValueError, KeyError, TypeError):
            bad["unparsable"] += 1
            continue
        if record.get("vocabulary") != LABEL_VOCABULARY:
            bad["wrong_vocabulary"] += 1
            continue
        if bucket not in CATEGORIES:
            bad["unknown_bucket"] += 1
            continue
        if item_id in labels:
            # 同一条目出现两次是正常的（它可能同时出现在两个日窗的版面上），静默折叠也没错。
            # 但**取值不同**的两行是标注冲突，静默按文件顺序覆盖会把它藏起来。
            duplicates += 1
            if labels[item_id] != bucket:
                conflicting += 1
        labels[item_id] = bucket
        labellers[str(record.get("labeller") or "(未注明)")] += 1
    return labels, {
        "path": str(path),
        "present": True,
        "n": len(labels),
        "lines_accepted": sum(labellers.values()),
        "duplicates": duplicates,
        "conflicting": conflicting,
        "rejected": bad,
        "sha256": hashlib.sha256(raw).hexdigest()[:16],
        "vocabulary": LABEL_VOCABULARY,
        "labellers": dict(labellers),
    }


def total_variation(left: Counter, right: Counter) -> float | None:
    n, m = sum(left[c] for c in CATEGORIES), sum(right[c] for c in CATEGORIES)
    if n < 8 or m < 8:
        return None
    return sum(abs(left[c] / n - right[c] / m) for c in CATEGORIES) / 2


def sampling_noise(pooled: Counter, day_sizes: list[int], trials: int = 200) -> float:
    """从合并分布按各日实际条数重抽，得到这个指标自己的噪声底。"""

    total = sum(pooled[c] for c in CATEGORIES)
    weights = [pooled[c] / total for c in CATEGORIES]
    draws = []
    for size in day_sizes:
        for seed in range(trials):
            rng = random.Random((size << 16) + seed)
            sample = Counter(rng.choices(CATEGORIES, weights=weights, k=size))
            value = total_variation(sample, pooled)
            if value is not None:
                draws.append(value)
    return statistics.mean(draws) if draws else float("nan")


def pooled_null(reference: Counter, n_ours: int, trials: int = 20000) -> dict:
    """合并口径 TV 的零假设：两侧构成**完全相同**时，仅因样本量会看到多少 TV。

    这个函数迟到了。`sampling_noise` 只给逐日的噪声底，于是合并 TV——本 program 的头号指标——
    一直是个没有参照的绝对值：0.156 与 0.122 都被当成「差距」读，而没人算过同分布下它取什么值。
    2026-09-10 补算的结果是它们**落在噪声内**（对齐深度 p=0.21），也就是说此前用这个绝对值判
    「离 AIHOT 有多远」的每一次，都超出了这个指标当时的分辨力。

    两侧都从参照构成里抽，所以它同时计入了双方的抽样误差。**已知二阶缺口**：那组权重本身估自
    参照物那 122 条，故权重也带误差，本函数不计。

    配对比较（同窗口、同池子、只改系数）不受此限——它消掉的正是这里的方差；受限的是**绝对值**。
    """

    total = sum(reference[c] for c in CATEGORIES)
    if total < 8 or n_ours < 8:
        return {}
    weights = [reference[c] / total for c in CATEGORIES]
    draws = []
    for seed in range(trials):
        rng = random.Random(seed)
        a = Counter(rng.choices(CATEGORIES, weights=weights, k=n_ours))
        b = Counter(rng.choices(CATEGORIES, weights=weights, k=total))
        value = total_variation(a, b)
        if value is not None:
            draws.append(value)
    draws.sort()
    return {
        "median": statistics.median(draws),
        "p95": draws[int(0.95 * len(draws))],
        "n_ours": n_ours,
        "n_reference": total,
        "draws": draws,
    }


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """二项占比的 Wilson 95% 区间。正态近似在 k 很小时会给出负下界，这里的类别恰好就那么小。"""

    if n <= 0:
        return (0.0, 1.0)
    center = (k + z * z / 2) / (n + z * z)
    half = z * math.sqrt(k * (n - k) / n + z * z / 4) / (n + z * z)
    return (max(0.0, center - half), min(1.0, center + half))


def class_verdicts(ours: Counter, reference: Counter) -> dict:
    """达标线（用户 2026-09-10 裁定）：**逐类占比落进 AIHOT 该类的 95% CI**。

    为什么换掉整页 TV：那个绝对值在当前样本量下判不出差异（对齐深度 p≈0.22，见 `pooled_null`），
    于是「够不够接近」这个问题它永远答不了。逐类占比换来的是**现在就判得动**，且每类各自成立才算过，
    比一个聚合标量更难糊弄——一类超配、另一类欠配可以在 TV 上互相抵消，在这里不行。

    刻意只取 AIHOT 一侧的 CI（用户原话「落进 AIHOT 的 CI」），因此它**不计我方的抽样误差**：
    我方占比自己的抖动就能把我们推出区间。方向是偏严而非偏宽——缓解措施是 `verdict_null()`：
    它直接给出"我方构成正确时会落进几类"的分布，比逐类看我方 CI 更直接。我方自己的 CI 仍逐类算出
    并写进 `--record` 的行里（`ours_ci`），只是不占屏幕宽度。（本 docstring 第一版写的是"下面
    一并打印"，而新表不印它——复核轮报出这条自陈与代码相矛盾。）

    已知限制：判据的分辨力就是各类 CI 的宽度，而它由 AIHOT 一侧的 n 决定。**逐类不同，别只报最窄
    那一类**——当前（n_ref=122）实测半宽 tip ±8.22 / model ±8.11 / product ±6.29 / industry ±6.00 /
    paper ±4.96 pp，所以盲区上界是 **±8.2pp**、不是 paper 那条 ±5pp（本 docstring 第一版就是这么
    低估了 1.7 倍，第三轮 review gate 报出）。半宽随每次运行打印，不写死在这里。
    """

    n_ours = sum(ours[c] for c in CATEGORIES)
    n_ref = sum(reference[c] for c in CATEGORIES)
    # 与 `total_variation` / `pooled_null` 同一道最小样本闸。没有它时，n_ref=8 的 CI 半宽约 ±22pp，
    # 几乎必然报 5/5 并打出「已达标」——一次空洞达标。此前挡住它的只是 `pooled` 为 None 时那句
    # f-string 格式化崩溃，即一个与本判据无关的异常在充当守卫（第三轮 review gate 报出）。
    if n_ours < 8 or n_ref < 8:
        return {"n_ours": n_ours, "n_reference": n_ref, "rows": [], "inside_count": None,
                "total": len(CATEGORIES), "passed": None, "reason": "样本不足（任一侧 < 8）"}
    rows = []
    for category in CATEGORIES:
        low, high = wilson_ci(reference[category], n_ref)
        ours_share = ours[category] / n_ours if n_ours else 0.0
        rows.append(
            {
                "category": category,
                "ours_k": ours[category],
                "ours_share": ours_share,
                "ours_ci": wilson_ci(ours[category], n_ours),
                "reference_k": reference[category],
                "reference_share": reference[category] / n_ref if n_ref else 0.0,
                "reference_ci": (low, high),
                # 这条就是本判据在这一类上的分辨力，逐类不同。打印出来，免得读者拿最窄那条当全局。
                "reference_ci_halfwidth_pp": 100 * (high - low) / 2,
                "inside": low <= ours_share <= high,
                # 差多少才进得去——0 表示已在区间内。给方向，免得读者自己减。
                "gap_pp": 0.0
                if low <= ours_share <= high
                else 100 * (ours_share - high if ours_share > high else ours_share - low),
            }
        )
    inside = sum(1 for r in rows if r["inside"])
    # 顶层键刻意叫 `inside_count` 而不是 `inside`：`rows[i].inside` 是布尔，同名不同型是
    # 消费者最容易踩的一种——`if record["inside"]` 会把 1/5 读成"达标"。另给一个显式的 `passed`。
    return {"n_ours": n_ours, "n_reference": n_ref, "rows": rows,
            "inside_count": inside, "total": len(rows), "passed": inside == len(rows)}


def verdict_null(reference: Counter, n_ours: int, trials: int = 20000) -> dict:
    """达标线自己的零假设：**我方构成与 AIHOT 完全相同时，`inside` 会取什么值。**

    这个函数是第三轮 review gate 报出的 HIGH：`class_verdicts` 的头条读数 `k/5` 在它下方 20 行
    重建了 `pooled_null` 存在所要消灭的那个缺陷——一个绝对值没有零假设，于是「真有差距」与
    「样本量就这么大」在输出上同形。实测（n_ours=137 / n_ref=122）：一张**完全符合** AIHOT 构成
    的页面只有约 77% 概率拿到 5/5，而 4/5 是它第二常见的结果。也就是说单看 `4/5` 判不出
    「差一类」与「已经完美、只是抖动」。

    只抽我方一侧：判据本身就只拿我方点估计比 AIHOT 的 CI（那是用户定的形态），所以零假设要
    复现的正是"我方以正确构成抽 n_ours 条"这件事，参照侧的 CI 保持它实际的样子。
    """

    total = sum(reference[c] for c in CATEGORIES)
    if total < 8 or n_ours < 8:
        return {}
    weights = [reference[c] / total for c in CATEGORIES]
    bounds = {c: wilson_ci(reference[c], total) for c in CATEGORIES}
    counts: Counter = Counter()
    for seed in range(trials):
        rng = random.Random(1_000_000 + seed)
        draw = Counter(rng.choices(CATEGORIES, weights=weights, k=n_ours))
        inside = sum(
            1 for c in CATEGORIES if bounds[c][0] <= draw[c] / n_ours <= bounds[c][1]
        )
        counts[inside] += 1
    return {
        "p_all_inside": counts[len(CATEGORIES)] / trials,
        "expected_inside": sum(k * v for k, v in counts.items()) / trials,
        "distribution": {k: counts[k] / trials for k in range(len(CATEGORIES) + 1)},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=str(REPO / "data" / "radar.db"))
    parser.add_argument(
        "--multiplier",
        action="append",
        default=[],
        metavar="CATEGORY=FACTOR",
        help="覆盖 select.CATEGORY_MULTIPLIERS 里的一项，可重复。用 paper=1.0 取消现行系数做对照。",
    )
    parser.add_argument(
        "--gate",
        action="store_true",
        help="把系数也喂给两道绝对闸（threshold / freshness floor），复现被否决的那个实现。"
        "默认只进排序键，与生产一致。",
    )
    parser.add_argument(
        "--labels",
        default=str(DEFAULT_LABELS),
        help="补充标注文件（JSONL）。补上 AIHOT 未收录条目的类别，把读数从 AIHOT 匹配子集"
        "推向整页。冲突时 AIHOT 的标签权威，补充标注只填空缺。"
        "传 `off` 只用 AIHOT 标签——**复现 2026-09-10 之前的历史读数（含 CATEGORY_MULTIPLIERS "
        "的拟合依据）必须用它**，因为本参数默认是开的。",
    )
    parser.add_argument(
        "--depth",
        choices=("ours", "aihot"),
        default="ours",
        help="ours=按生产的 40 条上限；aihot=按 AIHOT 当日实际条数（对齐深度，见 docstring 边界 3）",
    )
    parser.add_argument(
        "--record",
        nargs="?",
        const=str(DEFAULT_HISTORY),
        default=None,
        metavar="PATH",
        help="把本次逐类判据追加进历史序列（默认 scripts/eval/composition-history.jsonl）。"
        "**只在这次运行代表一轮迭代时传它**——探索性对照（如 --multiplier paper=1.0）不该进序列，"
        "否则「随迭代逼近」这条趋势会被同一时点的不同参数搅成噪声。",
    )
    args = parser.parse_args()

    overrides = dict(sel.CATEGORY_MULTIPLIERS)
    for spec in args.multiplier:
        name, _, factor = spec.partition("=")
        overrides[name] = float(factor)

    aihot = load_aihot()
    hand, hand_identity = load_extra_labels(Path(args.labels))
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn.execute("PRAGMA busy_timeout=60000")

    index: dict[str, str] = {}
    for item_id, url in conn.execute("SELECT id, url FROM items"):
        index.setdefault(normalize_url(str(url))[0], str(item_id))
    labels: dict[str, str] = {}
    reference_by_day: dict[str, Counter] = defaultdict(Counter)
    # 条目重合这条轴此前**没有任何量具**：达标线只问「类别比例像不像」，两个能回答「装的是不是
    # 同一批东西」的指标（selected_auc / selected_p_at_k）在 A 家族里明确不设闸，而这里的逐日重放
    # 整段写在 main() 里、取不出来。加这三个数是为了把它变成可读的 gap，而不是再取一次代理指标。
    reference_ids_by_day: dict[str, set[str]] = defaultdict(set)   # AIHOT 选了、且我方库里有
    reference_total_by_day: Counter = Counter()                    # AIHOT 选了（不论我方有没有）
    for record in aihot.values():
        if not record["category"]:
            continue
        if record["selected"]:
            reference_by_day[record["published"]][record["category"]] += 1
        if record["url"]:
            our_id = index.get(normalize_url(record["url"])[0])
            if our_id:
                labels[our_id] = record["category"]
                if record["selected"]:
                    reference_ids_by_day[record["published"]].add(our_id)
        if record["selected"]:
            reference_total_by_day[record["published"]] += 1
    # 权威规则：AIHOT 自己发布的标签胜出，补充标注只填 AIHOT 没有的那些条目。参照物的划分就是
    # 定义，标注者是在模仿它——让人评覆盖它等于用摹本改原件。
    # 两者都有的条目不合并，但要数出来：那是这个标注者的校准读数。
    # **注意它不会自己长出来**：若标注只覆盖 AIHOT 缺失的条目，重叠恒为零、这条通道饿死。
    # 要有校准就得**刻意留一批与 AIHOT 重叠的条目**去标，见 docs/issues/aihot-fit-eval.md。
    hand_only = {k: v for k, v in hand.items() if k not in labels}
    overlap = [(k, hand[k], labels[k]) for k in hand if k in labels]
    hand_agree = sum(1 for _, mine, theirs in overlap if mine == theirs)
    labels.update(hand_only)

    candidates = sel.deduplicate_candidates(sel._load_candidates(conn, DEFAULT_WEIGHTS))
    all_eligible: list = []  # 见下方 gate_score 定义之后填充

    by_day: dict[str, list] = defaultdict(list)
    for candidate in candidates:
        day = sel._shanghai_date(candidate.published_at)
        if day:
            by_day[day].append(candidate)
    days = sorted(
        day
        for day in by_day
        if sum(reference_by_day[day][c] for c in CATEGORIES) >= 5 and len(by_day[day]) >= 200
    )
    if not days:
        raise SystemExit("没有可比日窗：需要 AIHOT 当日精选 >=5 条、我方当日候选 >=200 条。")

    def factor_of(candidate) -> float:
        return overrides.get(candidate.primary_category, 1.0)

    def rank(candidate) -> tuple[float, str, str]:
        return (
            -candidate.weighted_score * factor_of(candidate),
            candidate.published_at,
            candidate.item_id,
        )

    def gate_score(candidate) -> float:
        """闸读到的分。生产只在排序键上施加系数，故默认返回未调整分。"""

        return candidate.weighted_score * (factor_of(candidate) if args.gate else 1.0)

    dropped_by_gate = sum(
        1
        for c in candidates
        if c.weighted_score >= sel.DEFAULT_THRESHOLD and gate_score(c) < sel.DEFAULT_THRESHOLD
    )

    all_eligible.extend(c for c in candidates if gate_score(c) >= sel.DEFAULT_THRESHOLD)
    if hand_identity["present"]:
        rejected = hand_identity["rejected"]
        print(
            f"补充标注: {hand_identity['path']}\n"
            f"          n={hand_identity['n']} sha={hand_identity['sha256']} "
            f"词表={hand_identity['vocabulary']} "
            f"标注者={hand_identity['labellers']}\n"
            f"          丢弃: 解析失败 {rejected['unparsable']} · 词表不符 {rejected['wrong_vocabulary']} "
            f"· 桶名不在词表 {rejected['unknown_bucket']}"
            f"   重复 {hand_identity['duplicates']}（其中取值冲突 {hand_identity['conflicting']}）\n"
            f"          与 AIHOT 重叠 {len(overlap)} 条"
            + (
                f"、一致 {hand_agree} 条（{100 * hand_agree / len(overlap):.1f}%）<- 标注者校准读数"
                if overlap
                else "  <- 零重叠，本次没有标注者校准读数；下面 POOLED 里由补充标注贡献的那部分未经校准"
            )
            + f"   仅补充标注贡献 {len(hand_only)} 条"
        )
    else:
        print(
            f"补充标注: {hand_identity['path']}"
            + ("" if hand_identity["path"].startswith("off") else "  （文件不存在）")
            + "  -> 本次只用 AIHOT 标签"
        )
    print(
        f"覆盖的系数: {overrides or '（无）'}   施加面: "
        f"{'排序键+两道闸（被否决的实现）' if args.gate else '仅排序键（生产）'}   "
        f"深度: {args.depth}   日窗 {len(days)} 个"
    )
    if args.gate:
        print(f"因系数跌破 threshold 而整个掉出候选池的条目: {dropped_by_gate}")
    print(f"{'日期':12}{'候选':>7}{'覆盖(标注/版面)':>17}{'我方':>26}{'AIHOT':>26}{'TV':>8}")
    ours_pooled, reference_pooled = Counter(), Counter()
    overlap_rows: list[dict] = []
    day_sizes: list[int] = []
    day_params: list[dict] = []
    page_slots = labelled_slots = 0
    for day in days:
        pool = by_day[day]
        # 与生产同形：`fresh` 只取被重放那一天（生产取「最新 fresh 日」），而尾部槽位的
        # `filtered` 跨**全部**过阈值候选——早先版本这里只用当日候选，把尾部槽位限窄了。
        fresh = sorted((c for c in pool if gate_score(c) >= sel.DEFAULT_FRESHNESS_FLOOR), key=rank)
        eligible = sorted(all_eligible, key=rank)
        limit = (
            sum(reference_by_day[day][c] for c in CATEGORIES)
            if args.depth == "aihot"
            else sel.DEFAULT_LIMIT
        )
        # `_fill` 的 fresh 段**不受 limit 夹**（`select.py` 那里自陈「the fresh segment is not
        # clamped to limit」），而 `DEFAULT_FRESHNESS_QUOTA = 36`。生产 limit=40 > 36，所以那条
        # 在生产上无后果；但 `--depth aihot` 的 limit 常常小于 36，于是它每天照样灌到 36 条——
        # **深度根本没被对齐，而读数仍标着「对齐深度」**。实测（2026-09-10，第三轮 review gate
        # 报出）：9 个日窗 picked 合计 **169**，而各日 limit 之和是 **122**。
        #
        # 修法两步：按生产的同一比例缩放 freshness 配额（36/40 = 0.9，保持"九成来自当日"这个
        # 机制形状），再硬夹到 limit。只夹不缩放会让浅深度下 100% 来自 fresh 段，那是另一种失真。
        freshness_quota = max(1, round(limit * sel.DEFAULT_FRESHNESS_QUOTA / sel.DEFAULT_LIMIT))
        picked = sel._fill(fresh, eligible, limit, freshness_quota, sel.DEFAULT_SOURCE_QUOTA)[:limit]
        # 逐日记生效值，不是模块默认值：`--depth aihot` 下 limit 是逐日的、配额是新算的，
        # 身份块里放 `DEFAULT_LIMIT` 会是**对该次运行为假的字段**，而假字段比缺字段更坏
        # （它看起来已经答过了）。复核轮报出。
        day_params.append({"day": day, "limit": limit, "freshness_quota": freshness_quota,
                           "picked": len(picked)})
        reachable = reference_ids_by_day[day]
        hit = sum(1 for c in picked if c.item_id in reachable)
        overlap_rows.append({"day": day, "picked": len(picked), "hit": hit,
                             "reference_selected": reference_total_by_day[day],
                             "reachable": len(reachable)})
        mine = Counter(labels[c.item_id] for c in picked if c.item_id in labels)
        ours_pooled += mine
        reference_pooled += reference_by_day[day]
        day_sizes.append(sum(reference_by_day[day][c] for c in CATEGORIES))
        value = total_variation(mine, reference_by_day[day])
        share = lambda counter: " ".join(  # noqa: E731
            f"{c[:4]}={100 * counter[c] / max(sum(counter[k] for k in CATEGORIES), 1):.0f}%"
            for c in CATEGORIES
        )
        labelled = sum(1 for c in picked if c.item_id in labels)
        page_slots += len(picked)
        labelled_slots += labelled
        coverage = f"{labelled}/{len(picked)} {100 * labelled / max(len(picked), 1):.0f}%"
        print(
            f"{day:12}{len(pool):7d}{coverage:>17}  {share(mine):24}  {share(reference_by_day[day]):24}"
            f"{'  n<8' if value is None else f'{value:8.3f}'}"
        )
    # 条目重合：达标线之外的那条轴。它拆成两段，**分开看才归得了因**——
    #   收录上限 = AIHOT 选的里有多少条在我方库里（够不到的，排序怎么改都拿不到）
    #   命中率   = 我方版面里有多少条 AIHOT 也选了（够得到之中我们挑没挑中）
    # 合成一个数会把"没收到"和"没挑中"混成同一个坏消息，而两者的处置完全不同。
    tot_picked = sum(r["picked"] for r in overlap_rows)
    tot_hit = sum(r["hit"] for r in overlap_rows)
    tot_ref = sum(r["reference_selected"] for r in overlap_rows)
    tot_reach = sum(r["reachable"] for r in overlap_rows)
    print(f"\n>>> 条目重合（达标线不看这条轴，但它是最用户可见的一条）")
    print(f"    收录上限  AIHOT 精选 {tot_ref} 条，我方库里有 {tot_reach} 条 "
          f"= {100 * tot_reach / max(tot_ref, 1):.1f}%   <- 够不到的部分排序改不动")
    # 命中率单看会被读错：分母是我方 400 格、分子上界是 AIHOT 够得到的那 146 条，所以
    # **完美选择器也只有 146/400**。不把这个上限印出来，15% 读起来像灾难，其实是 41% 的召回
    # 配上 2.5 倍的深度差。这条是「判官读数的刻度」那类纪律在本指标上的实例。
    ceiling = 100 * tot_reach / max(tot_picked, 1)
    print(f"    命中率    我方版面 {tot_picked} 格，其中 AIHOT 也选了 {tot_hit} 条 "
          f"= {100 * tot_hit / max(tot_picked, 1):.1f}%   <- 上限 {ceiling:.1f}%"
          f"（深度差造成，非选择问题）")
    print(f"    召回      够得到的 {tot_reach} 条里挑中 {tot_hit} 条 "
          f"= {100 * tot_hit / max(tot_reach, 1):.1f}%   <- 这一格才是排序/选择的 gap")
    pooled = total_variation(ours_pooled, reference_pooled)
    print(
        f"\n{'POOLED':12}{'':7}{sum(ours_pooled.values()):12d}  "
        f"{' '.join(f'{c[:4]}={100 * ours_pooled[c] / max(sum(ours_pooled.values()), 1):.1f}%' for c in CATEGORIES)}"
    )
    print(
        f"{'AIHOT':12}{'':7}{sum(reference_pooled.values()):12d}  "
        f"{' '.join(f'{c[:4]}={100 * reference_pooled[c] / max(sum(reference_pooled.values()), 1):.1f}%' for c in CATEGORIES)}"
    )
    coverage_pct = 100 * labelled_slots / max(page_slots, 1)
    print(
        f"\n>>> 标注覆盖率 = {labelled_slots}/{page_slots} = {coverage_pct:.1f}% 的版面格位"
        + (
            "   <- 100% 的格位都有标签"
            + (
                "，但其中有补充标注贡献的部分，故 POOLED 是**跨标注器**构成"
                if hand_only
                else "，且全部来自 AIHOT，故 POOLED 就是整页构成"
            )
            if labelled_slots == page_slots
            else "   <- 未达 100%，下面的 POOLED 是**已标注子集**的构成，不是整页构成"
        )
    )
    print(f">>> POOLED TV = {pooled:.3f}   <- 唯一该读的那个数")
    # 零假设必须与它同时打印。少了它，这个数在「真有差距」与「样本量就这么大」两种情况下同形——
    # 而 2026-09-10 补算发现对齐深度下正是后者（p=0.21）。
    null = pooled_null(reference_pooled, sum(ours_pooled[c] for c in CATEGORIES))
    above = None
    if null and pooled is not None:
        above = sum(1 for x in null["draws"] if x >= pooled) / len(null["draws"])
        print(
            f"    零假设（两侧同构成，n={null['n_ours']} vs {null['n_reference']}）:"
            f" 中位 {null['median']:.3f}  95% 分位 {null['p95']:.3f}"
        )
        verdict = (
            "  **落在噪声内：这个绝对值判不出差异**" if above >= 0.05
            else "  显著高于噪声" if above < 0.01 else "  勉强出噪声"
        )
        print(f"    ⇒ 同构成下出现 >= {pooled:.3f} 的概率 = {above:.3f}{verdict}")
        print("    配对比较（同窗口同池子、只改系数）不受此限；受限的是绝对值。")
    print(f"    逐日 TV 的噪声底（同分布重抽）= {sampling_noise(reference_pooled, day_sizes):.3f}")
    print("    逐日 TV 低于噪声底即无信息量；只用它查异常，不用它判改进。")

    verdicts = class_verdicts(ours_pooled, reference_pooled)
    print("\n>>> 达标线：逐类占比落进 AIHOT 该类的 95% CI（用户 2026-09-10 裁定的判据）")
    if verdicts["inside_count"] is None:
        print(f"    判不了：{verdicts['reason']}（我方 n={verdicts['n_ours']}，AIHOT n={verdicts['n_reference']}）")
        vnull = {}
    else:
        print(f"{'类别':8}{'我方占比':>12}{'AIHOT 占比':>13}{'AIHOT 95% CI':>20}{'半宽':>8}{'判定':>6}{'离区间':>10}")
        for row in verdicts["rows"]:
            low, high = row["reference_ci"]
            # 两位小数：一位会把边界情形印成自相矛盾的行（`OUT` 配 `+0.0pp`、我方占比与区间端点
            # 印成同一个数）。第三轮 review gate 在参数网格上命中 700 个这样的组合，而真实运行
            # 已经出过 `industry OUT +0.4pp` 这种同量级读数。
            gap = "" if row["inside"] else f"{row['gap_pp']:+.2f}pp"
            print(
                f"{row['category']:8}{100 * row['ours_share']:11.2f}%{100 * row['reference_share']:12.2f}%"
                f"   [{100 * low:5.2f},{100 * high:5.2f}]{row['reference_ci_halfwidth_pp']:7.2f}"
                f"{'IN' if row['inside'] else 'OUT':>6}{gap:>10}"
            )
        print(
            f"    ⇒ {verdicts['inside_count']}/{verdicts['total']} 类落进区间"
            f"（我方 n={verdicts['n_ours']}，AIHOT n={verdicts['n_reference']}）"
            + ("   **本判据下已达标**" if verdicts["passed"] else "")
        )
        # 这个头条读数自己的零假设。少了它，`4/5` 在「差一类」与「已经完美、只是抖动」两种情况下
        # 同形——而后者的概率并不小。这是 `pooled_null` 那条教训在同一文件里的第二次兑现。
        vnull = verdict_null(reference_pooled, verdicts["n_ours"])
        if vnull:
            print(
                f"    零假设（我方构成 = AIHOT **本次观测到的**构成）: P(5/5) = {vnull['p_all_inside']:.3f}"
                f"   E[落进数] = {vnull['expected_inside']:.2f}/5"
            )
            if verdicts["inside_count"] is not None and not verdicts["passed"]:
                p_at_or_below = sum(
                    v for k, v in vnull["distribution"].items() if k <= verdicts["inside_count"]
                )
                print(
                    f"    ⇒ 完美页面拿到 <= {verdicts['inside_count']}/5 的概率 = {p_at_or_below:.3f}"
                    + ("  **这个读数判不出「差」与「抖」**" if p_at_or_below >= 0.05 else "")
                )
        print("    判据只取 AIHOT 一侧的 CI，故我方 n 小的时候自身抖动就能判 OUT（我方 CI 见记录行）。")
        print("    半宽随 AIHOT 侧 n 增长而收窄 ⇒ **判据会变严**，同一系统可能从 IN 翻成 OUT。"
              "读到翻转先看 n_reference 有没有变大。")

    if args.record:
        # 身份块。**两行只有在这些输入都相同时才可比**，而它们没有一个在代码里——
        # 一次 re-enrich 改判类别、或 capture 分支多了一天，读数就变，而"我们改好了"与
        # "输入换了"在缺身份的行里完全同形。同一文件的 `load_extra_labels` 早就把这条标准写死了
        # （「sha256 + 条数是唯一能把两份读数区分开的东西」），本记录行第一版却只对 labels 施加它——
        # 三个输入里最小的那个。第三轮 review gate 报出。
        try:
            capture_sha = _git("rev-parse", CAPTURES_REF).decode().strip()[:12]
        except SystemExit:
            capture_sha = None
        items_n = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        max_eval = conn.execute("SELECT MAX(id) FROM item_evaluations").fetchone()[0]
        # **代码身份**。这一轮就是它的反证：夹住深度那处改动不动任何被记录的常量，却让同一
        # `--depth aihot` 从 169 个槽位变成 122——两行身份块会逐字段相同而数字不同。首轮那条
        # 记录只能靠**删除**来避免污染趋势，因为行内没有任何字段能把它标成"前夹子版本"。
        head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
                              capture_output=True, check=False)
        # 排除**记录文件自己**：上一轮 append 之后它就与 HEAD 不同，于是 `code_dirty` 从第二轮起
        # 恒为 true，这个字段也就再区分不出"工作树跑的"与"干净树跑的"——一个自指的恒真字段。
        # `-- . ':(exclude)<path>'` 用 git 自己的 pathspec 排除，不做字符串过滤。
        rel_history = Path(args.record).resolve().relative_to(REPO).as_posix()
        dirty = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain",
                                "--untracked-files=no", "--", ".", f":(exclude){rel_history}"],
                               capture_output=True, check=False)
        row = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "identity": {
                "db": args.db,
                "db_items": items_n,
                "db_max_eval_id": max_eval,
                "captures_ref": CAPTURES_REF,
                "captures_sha": capture_sha,
                "code_head": head.stdout.decode().strip() or None,
                "code_dirty": bool(dirty.stdout.strip()),
                "days": days,
                "weights": DEFAULT_WEIGHTS.as_record(),
                "threshold": sel.DEFAULT_THRESHOLD,
                # 生效值逐日记；`limit_rule` 说明它是怎么来的。不记 `DEFAULT_LIMIT`——见上。
                "limit_rule": "DEFAULT_LIMIT" if args.depth == "ours" else "AIHOT 当日精选条数",
                "freshness_quota_rule": "round(limit * DEFAULT_FRESHNESS_QUOTA / DEFAULT_LIMIT)",
                "day_params": day_params,
                "freshness_floor": sel.DEFAULT_FRESHNESS_FLOOR,
                # `DEFAULT_SOURCE_QUOTA` 是个对象、不可 JSON 序列化；`dataclasses.asdict` 对它
                # 成立就用结构化形态，否则退到 repr。第一版直接塞对象，在**全部计算跑完之后**
                # 才抛 TypeError——读数白跑一次，正是 review gate 指出的那类写盘时机问题。
                "source_quota": (
                    dataclasses.asdict(sel.DEFAULT_SOURCE_QUOTA)
                    if dataclasses.is_dataclass(sel.DEFAULT_SOURCE_QUOTA)
                    else repr(sel.DEFAULT_SOURCE_QUOTA)
                ),
                "labels": hand_identity["path"],
                "labels_sha256": hand_identity.get("sha256"),
                "labels_n": hand_identity.get("n"),
                # **显式关闭**与**文件不存在**在上面三个字段上完全同形（`sha256=null, n=0`，
                # path 只差一段中文文案），而"标注文件悄悄没了"会改覆盖率与 n_ours，是必须分开的
                # 一种。我原以为 sha256+n 能兜住，复核轮实测它们在权威口径（`--labels off`）上
                # 一个都用不上。
                "labels_mode": (
                    "off" if hand_identity["path"].startswith("off")
                    else "file" if hand_identity.get("present") else "missing"
                ),
            },
            "depth": args.depth,
            "multipliers": overrides,
            "gate": args.gate,
            "page_slots": page_slots,
            "labelled_slots": labelled_slots,
            # 新增于 2026-09-11。既有键一个没动，所以旧行仍然可比；旧行缺这几个键即"当时没量"。
            "overlap": {
                "picked": tot_picked, "hit": tot_hit,
                "reference_selected": tot_ref, "reachable": tot_reach,
                "by_day": overlap_rows,
            },
            "pooled_tv": pooled,
            "pooled_null_p": above,
            # 顶层用 `inside_count` + `passed`，不用 `inside`：见 class_verdicts 的返回注释。
            "inside_count": verdicts["inside_count"],
            "total": verdicts["total"],
            "passed": verdicts["passed"],
            "verdict_null_p_all_inside": vnull.get("p_all_inside"),
            "verdict_null_expected_inside": vnull.get("expected_inside"),
            "n_ours": verdicts["n_ours"],
            "n_reference": verdicts["n_reference"],
            # 逐类整行照记，**含 reference_share / 两侧计数**：只留区间的话，"我们在向 AIHOT 的实际
            # 占比靠近还是远离"这个最自然的下一问就答不了（`gap_pp` 只说离区间多远——model 那条
            # 记 −4.6pp，而离 AIHOT 实际占比是 −12.1pp，2.6 倍之差）。
            "rows": verdicts["rows"],
        }
        target = Path(args.record)
        target.parent.mkdir(parents=True, exist_ok=True)
        # 已有文件末尾没有换行时，append 会把新行拼到上一行尾，**两行一起变成不可解析**，
        # 而脚本照样打印"已追加"。所以先补一个换行。
        if target.exists() and target.stat().st_size:
            with open(target, "rb") as probe:
                probe.seek(-1, 2)
                needs_newline = probe.read(1) != b"\n"
            if needs_newline:
                with open(target, "a", encoding="utf-8") as handle:
                    handle.write("\n")
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        with open(target, encoding="utf-8") as handle:
            lines = sum(1 for _ in handle)
        print(f"\n>>> 已追加一行到 {target}（现共 {lines} 行）")


if __name__ == "__main__":
    main()
