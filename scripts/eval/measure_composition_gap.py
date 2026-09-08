#!/usr/bin/env python3
"""复现 ADR-499e 里那几条把构成差距定位到 enrich 的读数。全只读、零 LLM 调用。

存在的理由：这些数原本只活在一次性的 scratch 脚本里（系统临时目录，随时会被清），
而它们是「差距在哪个模块」这个结论的全部依据。任何人要复核或者在改动之后重测，
跑这一个脚本即可。

    uv run python scripts/eval/measure_composition_gap.py

四段读数，各自的时点敏感性不同，输出里逐段标注——**别把它们当成同一种数**。
"""
from __future__ import annotations

import collections
import json
import sqlite3
from pathlib import Path

CATS = ["tutorial", "model", "product", "industry", "paper"]
REPO = Path(__file__).resolve().parents[2]
EVALSET = REPO / "data/eval-fit/evalset-staging/aihot-fit-v1"
DB_URI = f"file:{REPO / 'data/radar.db'}?mode=ro&immutable=1"

# 与 curator/select.py 的 _load_candidates 同一套过滤，外加一个只用于分析的 enrich
# LEFT JOIN。那个 JOIN 生产环境里不要：实测每次 curate 多花 2.4~3.0s 而无消费者。
CANDIDATE_POOL_SQL = """
SELECT en.output_json
FROM item_evaluations e
JOIN items i ON i.id = e.item_id
JOIN sources s ON s.id = i.source_id
LEFT JOIN item_evaluations en
       ON en.item_id = e.item_id AND en.stage = 'enrich' AND en.error IS NULL
      AND en.id = (SELECT MAX(l2.id) FROM item_evaluations l2
                   WHERE l2.item_id = e.item_id AND l2.stage = 'enrich' AND l2.error IS NULL)
WHERE e.stage = 'scoring' AND s.enabled = 1
  AND COALESCE(s.kind, 'feed') != 'wechat' AND e.error IS NULL
  AND e.id = (SELECT MAX(l.id) FROM item_evaluations l
              WHERE l.item_id = e.item_id AND l.stage = 'scoring' AND l.error IS NULL)
"""


def total_variation(pred: collections.Counter[str], gold: collections.Counter[str]) -> float:
    np_, ng = sum(pred.values()), sum(gold.values())
    return sum(abs(pred[c] / np_ - gold[c] / ng) for c in CATS) / 2


def _latest(con: sqlite3.Connection, item_id: str, stage: str, field: str) -> object | None:
    row = con.execute(
        "SELECT output_json FROM item_evaluations"
        " WHERE item_id=? AND stage=? AND error IS NULL ORDER BY id DESC LIMIT 1",
        (item_id, stage),
    ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row[0]).get(field)
    except (ValueError, TypeError):
        return None


def _dist(labels: list[str]) -> str:
    n = len(labels)
    c = collections.Counter(labels)
    return "  ".join(f"{k}={c[k] / n * 100:.1f}%" for k in CATS)


def main() -> None:
    rows = [json.loads(line) for line in (EVALSET / "questions.jsonl").open()]
    con = sqlite3.connect(DB_URI, uri=True)

    selected = [r for r in rows if r["reference"].get("selected")]
    aihot = collections.Counter(
        r["reference"]["primary_category"] for r in selected if r["reference"].get("primary_category") in CATS
    )

    print("=" * 78)
    print("① prefilter 在 AIHOT 自己的精选上漏判了几条  [时点无关：题集与评估行都已冻结]")
    verdicts = collections.Counter(_latest(con, r["input"]["item_id"], "prefilter", "is_ai_related") for r in selected)
    print(f"   AIHOT 自精选 n={len(selected)}  →  {dict(verdicts)}")
    allv = collections.Counter(_latest(con, r["input"]["item_id"], "prefilter", "is_ai_related") for r in rows)
    print(f"   全部 AIHOT 条目 n={len(rows)}  →  {dict(allv)}")
    print("   `False` 在第二行非零，是这条读数自带的阳性对照——它不是恒真的。")

    print("\n" + "=" * 78)
    print("② 信源覆盖  [时点无关：读 manifest 的建集记录]")
    manifest = json.loads((EVALSET / "manifest.json").read_text())
    read = matched = unmatched = 0
    for name, b in manifest["batches"].items():
        read += b["read"]
        matched += b["matched"]
        unmatched += b["unmatched"]
        print(f"   {name:<28} read={b['read']:5d} matched={b['matched']:5d} unmatched={b['unmatched']:4d}")
    print(f"   {'合计':<28} read={read:5d} matched={matched:5d} unmatched={unmatched:4d}  = {unmatched / read * 100:.1f}%")
    print("   ⚠ 「精选项的 source_id 全在我方库里」这条读数**恒真、不可用**——题集本来")
    print("     就是按 url / x_status_id 匹配我方库建的，未匹配的条目进不了题集。")

    print("\n" + "=" * 78)
    print("③ 同一批条目上的类别配对比较  [时点敏感：enrich 行会随重跑增加]")
    paired = [
        (r["reference"]["primary_category"], ours)
        for r in rows
        if r["reference"].get("primary_category") in CATS
        and (ours := _latest(con, r["input"]["item_id"], "enrich", "primary_category")) in CATS
    ]
    n = len(paired)
    gold = [g for g, _ in paired]
    mine = [o for _, o in paired]
    print(f"   n={n}   逐条一致率={sum(a == b for a, b in zip(gold, mine)) / n:.3f}")
    print(f"   AIHOT : {_dist(gold)}")
    print(f"   我方   : {_dist(mine)}")
    print(f"   构成总变差 TV = {total_variation(collections.Counter(mine), collections.Counter(gold)):.3f}")
    print("   前 3 大错格：", end="")
    for (g, o), k in collections.Counter((g, o) for g, o in paired if g != o).most_common(3):
        print(f"  {g}→{o} {k}({k / n * 100:.1f}%)", end="")
    print()

    print("\n" + "=" * 78)
    print("③b 候选池整体构成  [时点敏感：池子随抓取增长，但**不过 48h 新鲜窗口**]")
    pool = collections.Counter()
    total = 0
    for (payload,) in con.execute(CANDIDATE_POOL_SQL):
        total += 1
        if not payload:
            continue
        try:
            cat = json.loads(payload).get("primary_category")
        except (ValueError, TypeError):
            continue
        if cat in CATS:
            pool[cat] += 1
    print(f"   候选池 {total} 行，其中带类别 {sum(pool.values())} 条")
    print("   我方池   : " + "  ".join(f"{k}={pool[k] / sum(pool.values()) * 100:.1f}%" for k in CATS))
    print("   AIHOT精选: " + "  ".join(f"{k}={aihot[k] / sum(aihot.values()) * 100:.1f}%" for k in CATS))
    print(f"   构成总变差 TV = {total_variation(pool, aihot):.3f}")
    print("   注：条目数会比 ADR 首次记录时多（库是活的）；要比的是 TV 与百分比，不是 n。")
    print("\n   top-40 的 TV（0.394~0.426）本脚本不算——它随 48h 窗口移动，没有固定值；")
    print("   要复现须用 select.py 的真实函数（_fill / _parse_utc / _shanghai_date /")
    print("   DEFAULT_SOURCE_QUOTA / deduplicate_candidates）并把 datetime.now(UTC) 换成模拟时点。")


if __name__ == "__main__":
    main()
