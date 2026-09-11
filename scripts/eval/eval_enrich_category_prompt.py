#!/usr/bin/env python3
"""在**留出样本**上比较 enrich 分类 prompt 的改动，ground truth = AIHOT 自己的标签。

为什么是这个形态（本仓已在这条轴上失败两次，两次的形态都记在台账）：

- 一次是「从**分歧条目**刻画出宽规则、直接写进 prompt」⇒ 盲测 28/40，**低于**生产 prompt 的 33/40。
  ⇒ 所以本脚本强制 `--exclude-ids`：刻画时读过的条目一律进 train，不得进留出。
- 一次是窄规则上线后 `category_agreement` 只 +2.6pp（CI 重叠）而 `tag_jaccard` −7.7pp。
  ⇒ 所以主读数是 **per-input 一致率** + **逐格混淆**，而且**一致率是硬否决项**：
  目标那一格降了但一致率掉了，判不成立。

**基线不花钱**：库里既有的 enrich 行就是基线，前提是它们确实由**当前这份 prompt** 产出——
所以脚本启动时核一次 `ruleset.current_version_v2()` 与 `--baseline-stamp` 是否相等，不等就拒跑。
（`2026-09-08%` 这个前缀底下实测有三份不同的 prompt，按前缀取基线会把身份搞错。）

用法：
    uv run python scripts/eval/eval_enrich_category_prompt.py --limit 20   # 先验机制
    uv run python scripts/eval/eval_enrich_category_prompt.py --limit 300  # 正式
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import random
import sqlite3
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
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
OURS_TO_BUCKET = {"tutorial": "tip"}  # 词表差一个词，见 measure_category_boundary.py

# 本次要测的改动：把「在说 vs 在做」从 tutorial 描述的**末尾**提到五类清单**之前**，
# 变成先过的一道闸。不删任何既有文字、不改类名、不动 PRIMARY_CATEGORY_SLUGS。
GATE_SENTENCE = (
    "先判一件事，再看五类：**这条内容是在「宣布 / 发生了一件事」，还是在「报道、评测、评论、分析、"
    "表态」那件事？** 后者一律归 tutorial——不论它讲的是模型、产品、公司还是钱，"
    "也不论说话的是当事方还是第三方。"
)
# 「评测」是规范自己的词，不是我新造的——它在 tutorial 的描述里已经写着
# 「第三方对某个模型、产品或论文做的评测与解读归这一类」。补进闸是因为实测它是
# AIHOT-tip→我方-model 那一格（27 条）的**主导形态**：清一色是 ARC-AGI-3 / Epoch /
# Terminal Bench 之类第三方跑出来的基准成绩，而它们被判成了"模型发布"。
ANCHOR = "每条内容必须选择且只能选择一个主类。"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(REPO / "data" / "radar.db"))
    ap.add_argument("--baseline-stamp", default="2026-09-08.r2.31b2065e")
    ap.add_argument("--limit", type=int, default=20, help="留出样本条数（先小后大）")
    ap.add_argument("--seed", type=int, default=20260911)
    ap.add_argument("--workers", type=int, default=8,
                    help="并发上限。API 非独占，按它的容量定；8 是本仓默认起点，不是天花板。")
    ap.add_argument("--exclude-ids", default=None,
                    help="每行一个 item_id：刻画干预时读过的条目，一律排除出留出样本。")
    ap.add_argument("--out", default=None, help="把逐条结果写成 jsonl，便于事后复核")
    args = ap.parse_args()

    from airadar.enrich import prompts_v2, runner_v2
    from airadar.ruleset import current_version_v2

    live = current_version_v2()
    if live != args.baseline_stamp:
        raise SystemExit(
            f"基线身份不符：库里要比的是 {args.baseline_stamp}，而当前 prompt 算出的戳是 {live}。"
            "配对比较的基线那一半必须由当前 prompt 产出，否则量到的差里混着别的 prompt。"
        )
    print(f"基线身份已核：{live}")

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    ah = {
        _comp.normalize_url(r["url"])[0]: r["category"]
        for r in _comp.load_aihot().values()
        if r.get("category") and r.get("url")
    }
    base: dict[str, str] = {}
    for iid, out in conn.execute(
        "SELECT item_id, output_json FROM item_evaluations "
        "WHERE stage='enrich' AND error IS NULL AND ruleset_version=? ORDER BY id",
        (args.baseline_stamp,),
    ):
        try:
            cat = (json.loads(out) or {}).get("primary_category")
        except Exception:
            continue
        if isinstance(cat, str) and cat:
            base[str(iid)] = OURS_TO_BUCKET.get(cat, cat)

    excluded = set()
    if args.exclude_ids:
        excluded = {ln.strip() for ln in Path(args.exclude_ids).read_text().split() if ln.strip()}

    rows = conn.execute(
        "SELECT id, title, url, source_id, '' , author, published_at, content_text "
        "FROM items WHERE url IS NOT NULL"
    ).fetchall()
    pool = []
    for row in rows:
        iid = str(row[0])
        truth = ah.get(_comp.normalize_url(str(row[2]))[0])
        if truth and iid in base and iid not in excluded:
            pool.append((row, truth))
    print(f"双标注且在基线戳内、且不在 train 的条目：{len(pool)} 条"
          f"（排除了 {len(excluded)} 条用于刻画的）")

    rng = random.Random(args.seed)
    rng.shuffle(pool)
    holdout = pool[: args.limit]

    # tier 在本次评测里取不到（items 表不带它），统一给空串：它只进 user 模板的一个字段，
    # **两个 arm 同样缺**，所以配对比较不受影响；但绝对值不得与生产读数混用。
    patched = prompts_v2.SYSTEM_PROMPT.replace(ANCHOR, GATE_SENTENCE + ANCHOR, 1)
    if patched == prompts_v2.SYSTEM_PROMPT:
        raise SystemExit(f"锚点没命中，改动没生效：{ANCHOR!r}")
    prompts_v2.SYSTEM_PROMPT = patched
    provider = runner_v2._provider_from_env()
    print(f"provider={type(provider).__name__}  留出 n={len(holdout)}  workers={args.workers}")

    def one(pair):
        row, truth = pair
        item = runner_v2._to_provider_item(row)
        enriched, _out, err, _ms = runner_v2._evaluate_item(provider, item)
        got = getattr(enriched, "primary_category", None) if enriched else None
        return str(row[0]), truth, (OURS_TO_BUCKET.get(got, got) if got else None), err

    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool_ex:
        futures = [pool_ex.submit(one, p) for p in holdout]
        for i, fut in enumerate(as_completed(futures), 1):
            results.append(fut.result())
            if i % 25 == 0:
                print(f"  ...{i}/{len(holdout)}")

    ok = [r for r in results if r[2]]
    errs = len(results) - len(ok)
    if not ok:
        # 全军覆没时把错误原文打出来再退出。先前这里直接除零，于是**真正的失败原因
        # 被一个 ZeroDivisionError 盖住**——那是这类脚本最坏的收尾。
        for _iid, _t, _n, err in results[:3]:
            print(f"  调用失败样例: {err}")
        raise SystemExit(f"{len(results)} 条全部失败，没有可判读数")
    b_hit = sum(1 for iid, truth, _new, _e in ok if base[iid] == truth)
    n_hit = sum(1 for _iid, truth, new, _e in ok if new == truth)
    print(f"\n留出 n={len(ok)}（{errs} 条调用失败，未计入）")
    print(f"  基线 per-input 一致率 {100 * b_hit / len(ok):.1f}%  ({b_hit}/{len(ok)})")
    print(f"  本改动 per-input 一致率 {100 * n_hit / len(ok):.1f}%  ({n_hit}/{len(ok)})")
    # 配对：只有改变了判定的那些条目携带信息，符号检验就看这两个数。
    b2n = sum(1 for iid, t, new, _e in ok if base[iid] == t and new != t)
    n2b = sum(1 for iid, t, new, _e in ok if base[iid] != t and new == t)
    print(f"  配对：改对 {n2b} 条 / 改错 {b2n} 条（其余不变）")

    print(f"\n{'类别':10}{'基线净偏':>10}{'本改动净偏':>12}   （我方占比 − AIHOT 占比，同一批条目）")
    tb = Counter(truth for _i, truth, _n, _e in ok)
    ob = Counter(base[iid] for iid, _t, _n, _e in ok)
    on = Counter(new for _i, _t, new, _e in ok)
    n = len(ok)
    for c in CATS:
        print(f"{c:10}{100 * (ob[c] - tb[c]) / n:>9.2f}pp{100 * (on[c] - tb[c]) / n:>11.2f}pp")
    cell_b = sum(1 for iid, t, _n, _e in ok if t == "tip" and base[iid] == "industry")
    cell_n = sum(1 for _i, t, new, _e in ok if t == "tip" and new == "industry")
    print(f"\n目标格 AIHOT-tip → 我方 industry：基线 {cell_b} 条 = {100 * cell_b / n:.2f}pp"
          f"   本改动 {cell_n} 条 = {100 * cell_n / n:.2f}pp")
    print("**一致率是硬否决项**：目标格降了而一致率掉了即判不成立——"
          "那与只读赢的那一面是同一个错。")

    if args.out:
        with Path(args.out).open("w", encoding="utf-8") as fh:
            for iid, truth, new, err in results:
                fh.write(json.dumps(
                    {"item_id": iid, "aihot": truth, "baseline": base.get(iid),
                     "revised": new, "error": err}, ensure_ascii=False) + "\n")
        print(f"逐条结果写入 {args.out}")


if __name__ == "__main__":
    main()
