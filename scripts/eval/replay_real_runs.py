"""在**真实 `curation_runs` 历史**上做同期对照——不重建归档面，也不合成节奏。

## 为什么要它

本仓记了一个仪器缺口（`docs/references/aihot-approximation-metrics.md` 末节）：
**没有任何仪器能在权威面上评估一个逐类干预**。
`measure_archive_composition.py` 读已发生的历史、做不了反事实；
`simulate_multirun_archive.py` 能 A/B，但它对权威面的**逐类误差 5.92pp 大于要追的位移**。

那 5.92pp 此前被归给「历史上多个代码版本」，没有分解过。本脚本的出发点是：
**它大部分是模拟器自己造成的，而 `curation_runs` 里存着足够把它消掉的东西。**

| 模拟器的失真源 | 真实 run 里存着什么 |
|---|---|
| 合成节奏（`--per-day 48` 固定） | `created_at` ——实测真实是每天 **11–60** 轮，剧烈变动 |
| `_load_candidates` 读**今天**最新的 scoring 行 | `input_eval_ids` ——**那一轮实际用的那批 `item_evaluations.id`** |
| `datetime.now(UTC)` 定新鲜窗 | `created_at` 即当时的「now」 |
| 权重/阈值按今天的常量 | `weights_json` · `threshold` 逐轮记着 |

## 它凭什么可信：有一个**确定答案**的自校准

每条 run 还记着 `output_curated_ids`。**用记录下来的输入集 + 记录下来的配置重放，
必须逐条复现那 40 个 id**。复现率就是这把尺子的**误差棒**，而且是逐类测得出来的——
不像 5.92pp 那样只能事后从两个不同口径的读数相减。
**自校准不过关就不要读 arm B**：那时它量的是实现差异，不是干预效应。

## 关键实现选择：不重抄选择逻辑

`curate()` 的写库在函数最尾、选择结果在它之前 ⇒ 把**候选加载**与**时钟**换掉、
把 `conn` 换成一个只有表结构的空临时库，就能让**生产的 `curate()` 原样跑完**，
结果从临时库的 `curated_items` 读回。
本仓已经因为「量具自己抄了一份选择逻辑」吃过亏（模拟器的尾段阈值闸与生产不一致，
`docs/issues/aihot-fit-eval.md` 有记录），所以这里一行选择逻辑都不抄。

只读真实库；一切写入落在临时库。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from airadar.curator import select as sel  # noqa: E402
from airadar.curator.score import weighted_score  # noqa: E402
from airadar.curator.weights import Weights  # noqa: E402

# `_load_candidates` 的投影，逐列对齐——差一列就会在别处静默变成另一个候选
CANDIDATE_SQL = """
SELECT
  e.id, i.id, i.content_hash, i.url, i.published_at, s.tier,
  s.id, COALESCE(s.kind, 'feed'),
  e.numeric_json,
  (SELECT en.output_json FROM item_evaluations en
    WHERE en.item_id = e.item_id AND en.stage = 'enrich' AND en.error IS NULL
      AND (? IS NULL OR en.id <= ?)
    ORDER BY en.id DESC LIMIT 1),
  (SELECT en.id FROM item_evaluations en
    WHERE en.item_id = e.item_id AND en.stage = 'enrich' AND en.error IS NULL
      AND (? IS NULL OR en.id <= ?)
    ORDER BY en.id DESC LIMIT 1)
FROM item_evaluations e
JOIN items i ON i.id = e.item_id
JOIN sources s ON s.id = i.source_id
WHERE e.id IN (%s)
"""


class FrozenClock:
    """只替换 `curate()` 读的那一个墙钟调用，其余 datetime 行为原样透传。

    `curate()` 用 `datetime.now(UTC) - timedelta(hours=...)` 定新鲜窗。重放时它必须是
    **那一轮的** now，否则 10 天前的 run 会拿今天的窗口去筛，`fresh` 段整段错位——
    而那一段占 40 格里的 36 格。
    """

    def __init__(self, moment: datetime) -> None:
        self._moment = moment

    def now(self, tz=None):  # noqa: ANN001, ANN201 - 签名跟随 datetime.now
        return self._moment if tz is None else self._moment.astimezone(tz)

    def __getattr__(self, name):  # noqa: ANN001, ANN204
        return getattr(datetime, name)


def temp_sink(real: sqlite3.Connection) -> sqlite3.Connection:
    """只有 `curation_runs` / `curated_items` 两张空表的内存库，接住 `curate()` 的写入。"""
    sink = sqlite3.connect(":memory:")
    for table in ("curation_runs", "curated_items"):
        ddl = real.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not ddl or not ddl[0]:
            raise SystemExit(f"真实库里找不到 {table} 的 DDL，无法建临时槽")
        sink.execute(ddl[0])
    return sink


def load_recorded_candidates(
    conn: sqlite3.Connection, eval_ids: list[int], weights: Weights, enrich_max: int | None
) -> list[sel.ScoredCandidate]:
    """按 run 记录的 `input_eval_ids` 取候选——**不走 `_load_candidates` 的「最新一行」**。"""
    out: list[sel.ScoredCandidate] = []
    chunk = 900  # SQLite 变量上限
    for start in range(0, len(eval_ids), chunk):
        batch = eval_ids[start : start + chunk]
        sql = CANDIDATE_SQL % ",".join("?" * len(batch))
        params = [enrich_max, enrich_max, enrich_max, enrich_max, *batch]
        for row in conn.execute(sql, params):
            numeric = json.loads(row[8])
            category = sel._primary_category(row[9])
            try:
                score = weighted_score(numeric, weights, row[5])
            except (KeyError, ValueError):
                continue
            out.append(
                sel.ScoredCandidate(
                    eval_id=row[0],
                    item_id=row[1],
                    content_hash=row[2],
                    url=row[3],
                    published_at=row[4],
                    weighted_score=score,
                    reason={
                        "scores": numeric,
                        "tier": row[5],
                        "tier_multiplier": sel.tier_multiplier(row[5]) if weights.uses_tier_multiplier else 1.0,
                        "category": category,
                        "category_multiplier": sel.category_multiplier(category),
                        "weighted_score": score,
                    },
                    source_id=row[6],
                    kind=row[7],
                    primary_category=category,
                    enrich_eval_id=row[10],
                )
            )
    return out


def replay(conn: sqlite3.Connection, run: sqlite3.Row, weights: Weights, *, limit: int,
           source_quota, enrich_max: int | None) -> list[str]:
    """用生产的 `curate()` 重放一条 run，返回它选出的 item_id（按名次）。"""
    eval_ids = json.loads(run["input_eval_ids"])
    cands = load_recorded_candidates(conn, eval_ids, weights, enrich_max)
    moment = datetime.strptime(run["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)

    sink = temp_sink(conn)
    real_loader, real_datetime = sel._load_candidates, sel.datetime
    try:
        sel._load_candidates = lambda _conn, _weights: list(cands)
        sel.datetime = FrozenClock(moment)
        sel.curate(
            sink,
            weights=weights,
            threshold=float(run["threshold"]),
            limit=limit,
            source_quota=source_quota,
        )
    finally:
        sel._load_candidates, sel.datetime = real_loader, real_datetime
    rows = sink.execute("SELECT item_id FROM curated_items ORDER BY rank").fetchall()
    sink.close()
    return [r[0] for r in rows]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(REPO / "data" / "radar.db"))
    ap.add_argument("--runs", type=int, default=20, help="抽多少条真实 run 做自校准")
    ap.add_argument("--since", default=None, help="只取 created_at >= 该 UTC 前缀的 run")
    ap.add_argument("--limit", type=int, default=sel.DEFAULT_LIMIT)
    args = ap.parse_args()

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn.execute("PRAGMA busy_timeout=120000")
    conn.row_factory = sqlite3.Row

    where = "WHERE created_at >= ?" if args.since else ""
    params = [args.since] if args.since else []
    runs = conn.execute(
        f"SELECT * FROM curation_runs {where} ORDER BY created_at DESC LIMIT ?",
        [*params, args.runs],
    ).fetchall()
    if not runs:
        raise SystemExit("没取到 run")

    print(f"自校准：{len(runs)} 条真实 run（{runs[-1]['created_at']} .. {runs[0]['created_at']}）")
    print(f"{'run':26}{'输入':>8}{'记录选出':>9}{'重放选出':>9}{'逐条相同':>9}{'交集':>7}{'Jaccard':>9}")
    exact = 0
    jac_sum = 0.0
    for run in runs:
        recorded = json.loads(run["output_curated_ids"])
        w = json.loads(run["weights_json"])
        # `uses_tier_multiplier` 也逐轮记着，要一起还原——它决定 tier 乘数进不进分数，
        # 漏掉它会让重放用今天的默认值去算一个当时不是这么算的分。
        dims = {d: float(w.get(d, 0.0)) for d in
                ("relevance", "density", "recency", "authority", "engineering", "significance")}
        weights = Weights(**dims, uses_tier_multiplier=bool(w.get("uses_tier_multiplier", False)))
        got = replay(conn, run, weights, limit=args.limit,
                     source_quota=sel.DEFAULT_SOURCE_QUOTA, enrich_max=w.get("enrich_watermark"))
        same = got == recorded
        inter = len(set(got) & set(recorded))
        union = len(set(got) | set(recorded)) or 1
        exact += same
        jac_sum += inter / union
        print(f"{run['id']:26}{len(json.loads(run['input_eval_ids'])):>8}{len(recorded):>9}"
              f"{len(got):>9}{'✅' if same else '❌':>8}{inter:>7}{inter / union:>9.3f}")
    print(f"\n⇒ **逐条完全相同 {exact}/{len(runs)}**，平均 Jaccard {jac_sum / len(runs):.3f}")
    print("   判读：逐条相同率就是这把尺子的误差棒。**不接近 1 就别读 arm B**"
          "——那时它量的是实现差异，不是干预效应。")


if __name__ == "__main__":
    main()
