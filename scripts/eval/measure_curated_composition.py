#!/usr/bin/env python3
"""精选构成 vs AIHOT 自己的精选构成，**两边都用 AIHOT 的类别标签**。

为什么要有这个脚本：此前所有构成读数都是拿**我方**分类器的标签比**AIHOT**的标签，而两个标注器
在同一批内容上的逐条一致率只有 68.5%，光是这份分歧就值 0.129 的总变差——比要测的差距还大。
本脚本只用 AIHOT 自己发布的类别，因此没有标注器混淆。代价见下面「边界」。

它同时是 `select.CATEGORY_MULTIPLIERS` 的**发现通道**：改了系数、或 enrich prompt 改版之后，
跑它一次就能看出页面构成往哪边走。

用法（只读，不写库，不出网）：

    uv run python scripts/eval/measure_curated_composition.py
    uv run python scripts/eval/measure_curated_composition.py --multiplier paper=1.0   # 对照

**边界，读数之前必须知道三条**：

1. 只覆盖我方精选中**被 AIHOT 也收录过**的那部分（约 30%）。这是一个有偏子集——AIHOT 收录什么
   本身是它的选择。**整页口径本脚本不覆盖、也没有别的可复现入口**：唯一的整页读数来自人工标注
   外推（标注者盲测一致率 81.7%，但 tutorial 召回仅 50%、误判去向 paper/industry，故整页 paper
   估计偏高）。这一条是已知未闭合项，不要把本脚本的 POOLED 读成整页结论。
2. **逐日构成 TV 饱和于抽样噪声**：AIHOT 自己每天与它自己的合并均值就差 ~0.27，而按当日条数
   （5–35）从合并分布重抽的纯噪声已有 ~0.25。所以只读 `POOLED` 那一行，逐日行仅供查异常。
3. 两边的**截断深度不同**（我方 40 条/天、AIHOT 十几条），而类别在排序上分布不均，故深度本身就
   造成构成差。`--depth aihot` 用 AIHOT 当日条数重取一遍，供对齐深度比较。
"""

from __future__ import annotations

import argparse
import gzip
import json
import random
import sqlite3
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from airadar.curator import select as sel  # noqa: E402
from airadar.curator.weights import DEFAULT_WEIGHTS  # noqa: E402
from airadar.eval.aihot_fit.build import normalize_url  # noqa: E402

SUBMODULE = REPO / "benchmarks" / "aihot"
CAPTURES_REF = "origin/captures/daily"
# AIHOT 的 slug -> 我们这套五类名。映射不是猜的：在 aihot-fit-v1 题集上，五个 slug 对
# reference.primary_category 各 100% 一致（n=641/304/305/979/512）。
SLUG_TO_CATEGORY = {
    "ai-products": "product",
    "paper": "paper",
    "ai-models": "model",
    "tip": "tutorial",
    "industry": "industry",
}
CATEGORIES = ("tutorial", "model", "product", "industry", "paper")


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
                    "category": SLUG_TO_CATEGORY.get(row.get("category")),
                    "selected": bool(row.get("selected")),
                    # 上海日，与我方 `sel._shanghai_date` 同一口径。直接截 publishedAt[:10]
                    # 是 UTC 日，两侧会在 UTC 16:00 之后错开一天。
                    "published": sel._shanghai_date(row.get("publishedAt") or ""),
                }
    return items


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
        "--depth",
        choices=("ours", "aihot"),
        default="ours",
        help="ours=按生产的 40 条上限；aihot=按 AIHOT 当日实际条数（对齐深度，见 docstring 边界 3）",
    )
    args = parser.parse_args()

    overrides = dict(sel.CATEGORY_MULTIPLIERS)
    for spec in args.multiplier:
        name, _, factor = spec.partition("=")
        overrides[name] = float(factor)

    aihot = load_aihot()
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn.execute("PRAGMA busy_timeout=60000")

    index: dict[str, str] = {}
    for item_id, url in conn.execute("SELECT id, url FROM items"):
        index.setdefault(normalize_url(str(url))[0], str(item_id))
    labels: dict[str, str] = {}
    reference_by_day: dict[str, Counter] = defaultdict(Counter)
    for record in aihot.values():
        if not record["category"]:
            continue
        if record["selected"]:
            reference_by_day[record["published"]][record["category"]] += 1
        if record["url"]:
            our_id = index.get(normalize_url(record["url"])[0])
            if our_id:
                labels[our_id] = record["category"]

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
    print(
        f"覆盖的系数: {overrides or '（无）'}   施加面: "
        f"{'排序键+两道闸（被否决的实现）' if args.gate else '仅排序键（生产）'}   "
        f"深度: {args.depth}   日窗 {len(days)} 个"
    )
    if args.gate:
        print(f"因系数跌破 threshold 而整个掉出候选池的条目: {dropped_by_gate}")
    print(f"{'日期':12}{'候选':>7}{'带AIHOT标签':>12}{'我方':>26}{'AIHOT':>26}{'TV':>8}")
    ours_pooled, reference_pooled = Counter(), Counter()
    day_sizes = []
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
        picked = sel._fill(fresh, eligible, limit, sel.DEFAULT_FRESHNESS_QUOTA, sel.DEFAULT_SOURCE_QUOTA)
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
        print(
            f"{day:12}{len(pool):7d}{labelled:12d}  {share(mine):24}  {share(reference_by_day[day]):24}"
            f"{'  n<8' if value is None else f'{value:8.3f}'}"
        )
    pooled = total_variation(ours_pooled, reference_pooled)
    print(
        f"\n{'POOLED':12}{'':7}{sum(ours_pooled.values()):12d}  "
        f"{' '.join(f'{c[:4]}={100 * ours_pooled[c] / max(sum(ours_pooled.values()), 1):.1f}%' for c in CATEGORIES)}"
    )
    print(
        f"{'AIHOT':12}{'':7}{sum(reference_pooled.values()):12d}  "
        f"{' '.join(f'{c[:4]}={100 * reference_pooled[c] / max(sum(reference_pooled.values()), 1):.1f}%' for c in CATEGORIES)}"
    )
    print(f"\n>>> POOLED TV = {pooled:.3f}   <- 唯一该读的那个数")
    print(f"    逐日 TV 的噪声底（同分布重抽）= {sampling_noise(reference_pooled, day_sizes):.3f}")
    print("    逐日 TV 低于噪声底即无信息量；只用它查异常，不用它判改进。")


if __name__ == "__main__":
    main()
