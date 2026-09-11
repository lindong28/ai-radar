#!/usr/bin/env python3
"""量 AIHOT 的类别与我方 enrich 类别之间的**边界错位**，按 enrich 戳分开算。

为什么需要它：2026-09-11 的决定性归因（台账同日那节）把瓶颈从控制器挪到了标注器——
同一个再平衡机制喂 AIHOT 自己的标签达到 5/5、喂我方新戳 enrich 只到 3/5。
但「差额归标注器」只说了**有多大**，没说**在哪一格**。动 enrich prompt 要花 LLM 调用，
所以先零成本地把混淆矩阵摆出来，再决定值不值得动。

它不产出达标线读数，也不该进趋势序列——那是 measure_curated_composition.py 的事。

用法：
    uv run python scripts/eval/measure_category_boundary.py
    uv run python scripts/eval/measure_category_boundary.py --db <frozen.db>
"""

from __future__ import annotations

import argparse
import collections
import importlib.util
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "_composition", HERE / "measure_curated_composition.py"
)
assert _spec and _spec.loader
_comp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_comp)

CATS = list(_comp.CATEGORIES)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(_comp.REPO / "data" / "radar.db"))
    args = ap.parse_args()

    # AIHOT 一侧只取**它自己发布的标签**，不掺补充标注：参照物的划分就是定义，
    # 拿摹本去改原件会让这张矩阵量的变成"标注者像不像 AIHOT"。
    aihot = {}
    for record in _comp.load_aihot().values():
        if record.get("category") and record.get("url"):
            aihot[_comp.normalize_url(record["url"])[0]] = record["category"]

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    url_by_item = {}
    for item_id, url in conn.execute("SELECT id, url FROM items"):
        if url:
            url_by_item[str(item_id)] = _comp.normalize_url(str(url))[0]

    # 逐戳分开：ADR-9e21 要求任何类别级干预按戳分层，而这张矩阵正是那类干预的输入。
    # 同一条目可能被多次 enrich，取**该戳下最新的一行**。
    by_stamp: dict[str, dict[str, str]] = collections.defaultdict(dict)
    rows = conn.execute(
        "SELECT item_id, ruleset_version, output_json FROM item_evaluations "
        "WHERE stage='enrich' AND error IS NULL ORDER BY id"
    )
    for item_id, ruleset, output in rows:
        cat = _comp._primary_category(output) if hasattr(_comp, "_primary_category") else None
        if cat is None:
            from airadar.curator import select as _sel

            cat = _sel._primary_category(output)
        if cat:
            by_stamp[str(ruleset).split(".")[0]][str(item_id)] = cat

    print(f"AIHOT 有类别的条目 {len(aihot)}；enrich 戳 {sorted(by_stamp)}")

    for stamp in sorted(by_stamp):
        ours = by_stamp[stamp]
        pairs = [
            (aihot[url_by_item[i]], c)
            for i, c in ours.items()
            if i in url_by_item and url_by_item[i] in aihot
        ]
        if len(pairs) < 30:
            print(f"\n>>> 戳 {stamp}：双标注条目只有 {len(pairs)} 条，跳过")
            continue
        m: collections.Counter = collections.Counter(pairs)
        n = len(pairs)
        agree = sum(v for (a, o), v in m.items() if a == o)
        print(f"\n>>> 戳 {stamp}  双标注 n={n}  逐条一致率 {100 * agree / n:.1f}%")
        print("    行 = AIHOT 的类别，列 = 我方 enrich 的类别；对角线是一致的那些")
        head = "".join(f"{c[:7]:>9}" for c in CATS)
        print(f"{'AIHOT\\ours':>12}{head}{'合计':>7}")
        for a in CATS:
            row = [m[(a, o)] for o in CATS]
            tot = sum(row)
            cells = "".join(f"{v:>9}" for v in row)
            print(f"{a:>12}{cells}{tot:>7}")
        col = {o: sum(m[(a, o)] for a in CATS) for o in CATS}
        print(f"{'我方合计':>12}" + "".join(f"{col[o]:>9}" for o in CATS))

        # **按「这一格值多少」排序，不按格子大小**：要修的是让达标线动起来的那几格。
        # 一格 (a, o) 的代价 = 它把 o 的占比推高 |cell|/n、把 a 推低同样多。
        print("\n    最贵的错位格（占全部双标注条目的百分点，双向各算一次）：")
        worst = sorted(
            ((v, a, o) for (a, o), v in m.items() if a != o), key=lambda t: -t[0]
        )[:6]
        for v, a, o in worst:
            print(
                f"      AIHOT 叫 {a:9} 而我方叫 {o:9}  {v:>4} 条 = {100 * v / n:5.2f}pp"
                f"   （我方 {o} 虚高、{a} 虚低）"
            )
        # 逐类净偏：正 = 我方这一类比 AIHOT 多标了多少
        print("\n    逐类净偏（我方占比 − AIHOT 占比，同一批条目，所以只反映边界不反映选择）：")
        for c in CATS:
            a_share = sum(m[(c, o)] for o in CATS) / n
            o_share = col[c] / n
            print(f"      {c:10} 我方 {100 * o_share:5.1f}%  AIHOT {100 * a_share:5.1f}%  "
                  f"净 {100 * (o_share - a_share):+5.2f}pp")


if __name__ == "__main__":
    main()
