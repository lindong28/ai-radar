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
from math import comb
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


def run_config(run: sqlite3.Row) -> dict:
    """从这一条 run 自己的记录里读出它当时生效的机制配置。

    **这是本脚本最容易错、也最贵的一步**：拿今天的常量去重放一条老 run，会得到一个
    「看起来像重放、其实在量实现差异」的读数，而它与「历史不可复现」逐字同形。
    三处机制各有一个记录里读得到的标记：

    | 机制 | 上线 | 标记 | 缺失时的正解 |
    |---|---|---|---|
    | 源配额 ADR-bc36 | 2026-09-03 | `shadow_json` 非空 | `source_quota=None`（当时没有配额） |
    | 类别乘数 ADR-3f8b | 2026-09-10 | `weights_json.category_multipliers` | `{}`（当时没有乘数） |
    | enrich 快照 ADR-9e21 | 较晚 | `weights_json.enrich_watermark` | **不可还原** |

    第三行没有正解：老 run 没记 watermark，当时的类别快照取不回来，重放只能读今天的 enrich。
    **这是本尺子对老窗的硬上限**，不是可以调好的参数——读老窗结果时必须一并读它。
    """
    w = json.loads(run["weights_json"])
    return {
        # `shadow_json` 是配额机制自己写的，没有它就是那一轮没跑配额——比按日期猜可靠
        "source_quota": sel.DEFAULT_SOURCE_QUOTA if run["shadow_json"] else None,
        "mults": w.get("category_multipliers", {}),
        "enrich_max": w.get("enrich_watermark"),
        "enrich_recoverable": "enrich_watermark" in w,
    }


def replay(conn: sqlite3.Connection, run: sqlite3.Row, weights: Weights, *, limit: int,
           source_quota, enrich_max: int | None, mults: dict | None = None) -> list[str]:
    """用生产的 `curate()` 重放一条 run，返回它选出的 item_id（按名次）。"""
    eval_ids = json.loads(run["input_eval_ids"])
    cands = load_recorded_candidates(conn, eval_ids, weights, enrich_max)
    moment = datetime.strptime(run["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)

    sink = temp_sink(conn)
    real_loader, real_datetime = sel._load_candidates, sel.datetime
    real_mults = sel.CATEGORY_MULTIPLIERS
    try:
        sel._load_candidates = lambda _conn, _weights: list(cands)
        sel.datetime = FrozenClock(moment)
        # `ranking_key` 在调用时才读这个模块全局 ⇒ 在这里换掉就还原了那一轮的类别乘数。
        # 老 run 的 `weights_json` 里没有这个键 = 当时**没有**类别乘数（ADR-3f8b 是 2026-09-10
        # 才上的）；套今天的 `{"paper": 0.95}` 会把一整类的名次系统性挪位。
        sel.CATEGORY_MULTIPLIERS = mults if mults is not None else real_mults
        sel.curate(
            sink,
            weights=weights,
            threshold=float(run["threshold"]),
            limit=limit,
            source_quota=source_quota,
        )
    finally:
        sel._load_candidates, sel.datetime = real_loader, real_datetime
        sel.CATEGORY_MULTIPLIERS = real_mults
    rows = sink.execute("SELECT item_id FROM curated_items ORDER BY rank").fetchall()
    sink.close()
    return [r[0] for r in rows]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(REPO / "data" / "radar.db"))
    ap.add_argument("--runs", type=int, default=20, help="抽多少条真实 run 做自校准")
    ap.add_argument("--since", default=None, help="只取 created_at >= 该 UTC 前缀的 run")
    ap.add_argument("--limit", type=int, default=sel.DEFAULT_LIMIT)
    ap.add_argument(
        "--arm-b",
        default=None,
        metavar="rel,den,rec,auth,eng,sig",
        help="再跑一遍这个权重向量，与生产权重**配对**比 AIHOT 精选的召回。"
             "只计入 arm A 逐条相同的 run——不同的那些量的是实现漂移，不是干预效应。",
    )
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
    unrecoverable = 0
    for run in runs:
        recorded = json.loads(run["output_curated_ids"])
        w = json.loads(run["weights_json"])
        # `uses_tier_multiplier` 也逐轮记着，要一起还原——它决定 tier 乘数进不进分数，
        # 漏掉它会让重放用今天的默认值去算一个当时不是这么算的分。
        dims = {d: float(w.get(d, 0.0)) for d in
                ("relevance", "density", "recency", "authority", "engineering", "significance")}
        weights = Weights(**dims, uses_tier_multiplier=bool(w.get("uses_tier_multiplier", False)))
        cfg = run_config(run)
        got = replay(conn, run, weights, limit=args.limit, source_quota=cfg["source_quota"],
                     enrich_max=cfg["enrich_max"], mults=cfg["mults"])
        same = got == recorded
        inter = len(set(got) & set(recorded))
        union = len(set(got) | set(recorded)) or 1
        exact += same
        jac_sum += inter / union
        print(f"{run['id']:26}{len(json.loads(run['input_eval_ids'])):>8}{len(recorded):>9}"
              f"{len(got):>9}{'✅' if same else '❌':>8}{inter:>7}{inter / union:>9.3f}"
              f"   配额{'有' if cfg['source_quota'] else '无'}"
              f" 乘数{'有' if cfg['mults'] else '无'}"
              f" enrich{'可还原' if cfg['enrich_recoverable'] else '**不可还原**'}")
        unrecoverable += not cfg["enrich_recoverable"]
    print(f"\n⇒ **逐条完全相同 {exact}/{len(runs)}**，平均 Jaccard {jac_sum / len(runs):.3f}")
    if unrecoverable:
        print(f"   ⚠️ 其中 **{unrecoverable}/{len(runs)}** 条没记 `enrich_watermark` ⇒ "
              "重放只能读**今天**的类别，当时的快照取不回来。**这是对老窗的硬上限**，"
              "剩余不一致里有多少归它，本尺子分不出来。")
    print("   判读：逐条相同率就是这把尺子的误差棒。**不接近 1 就别读 arm B**"
          "——那时它量的是实现差异，不是干预效应。")

    if args.arm_b:
        arm_b(conn, runs, args)


def arm_b(conn: sqlite3.Connection, runs: list, args) -> None:
    """配对比较：换一个权重向量，AIHOT 的精选召回是升是降。

    **分母是「作为候选进过至少一轮」，不是「归档面上够得着」。** 后者把信源可达性混进来，
    而权重动不了可达性；前者把它整个拿掉，剩下的差异只可能来自排序与闸。

    代价要一起读：这个分母也混进了几天前的精选——它们早被更早的 run 选过，本窗口不会再选，
    所以**绝对召回远低于归档面那个数，两者不可比**。可比的是两臂之差，它是配对的。
    故这里报 McNemar 而不是两个比例：n 小的时候，「差了几个百分点」与随机噪声长得一样。
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("_mcc", Path(__file__).with_name("measure_curated_composition.py"))
    assert spec and spec.loader
    mcc = importlib.util.module_from_spec(spec)
    sys.modules["_mcc"] = mcc
    spec.loader.exec_module(mcc)

    dims = [float(x) for x in args.arm_b.split(",")]
    if len(dims) != 6:
        raise SystemExit("--arm-b 要六个数：rel,den,rec,auth,eng,sig")
    cand_dims = dict(zip(
        ("relevance", "density", "recency", "authority", "engineering", "significance"), dims, strict=True))

    refs = {mcc.normalize_url(r["url"])[0]: r
            for r in mcc.load_aihot().values() if r["selected"] and r.get("url")}
    # 一条参照 URL 在我方可能对应多行 items（跨源重复）。取第一行即可：下面两边都按
    # **URL 集合**算命中，同一 URL 命中几次不改变结果，但让两臂的分母保持同一个口径。
    url_to_item: dict[str, str] = {}
    for iid, u in conn.execute("SELECT id, url FROM items WHERE url IS NOT NULL"):
        n = mcc.normalize_url(str(u))[0]
        if n in refs:
            url_to_item.setdefault(n, iid)
    item_to_url = {v: k for k, v in url_to_item.items()}

    pool_refs: set[str] = set()
    hit: dict[str, set[str]] = {"A": set(), "B": set()}
    used = 0
    for run in runs:
        recorded = json.loads(run["output_curated_ids"])
        w = json.loads(run["weights_json"])
        tier = bool(w.get("uses_tier_multiplier", False))
        base = Weights(**{d: float(w.get(d, 0.0)) for d in cand_dims}, uses_tier_multiplier=tier)
        cfg = run_config(run)
        kw = dict(limit=args.limit, source_quota=cfg["source_quota"],
                  enrich_max=cfg["enrich_max"], mults=cfg["mults"])
        if replay(conn, run, base, **kw) != recorded:
            continue  # arm A 不精确的 run 不计入；上面的自校准表已逐条报过它
        used += 1
        got_b = replay(conn, run, Weights(**cand_dims, uses_tier_multiplier=tier), **kw)
        eval_ids = json.loads(run["input_eval_ids"])
        pool = {r[0] for r in conn.execute(
            f"SELECT item_id FROM item_evaluations WHERE id IN ({','.join('?' * len(eval_ids))})", eval_ids)}
        pool_refs |= {item_to_url[i] for i in pool & item_to_url.keys()}
        hit["A"] |= {item_to_url[i] for i in set(recorded) & item_to_url.keys()}
        hit["B"] |= {item_to_url[i] for i in set(got_b) & item_to_url.keys()}

    n = len(pool_refs) or 1
    print(f"\n>>> arm B {args.arm_b}：{used}/{len(runs)} 条 run 计入（arm A 逐条相同的那些）")
    print(f"分母＝作为候选进过至少一轮的 AIHOT 精选 = **{len(pool_refs)}**")
    for arm in ("A", "B"):
        print(f"   arm {arm} 命中 {len(hit[arm]):>3}  = {100 * len(hit[arm]) / n:5.1f}%")
    x, y = len(hit["B"] - hit["A"]), len(hit["A"] - hit["B"])
    print(f"   Δ = {100 * (len(hit['B']) - len(hit['A'])) / n:+.1f}pp   不一致对 B独有={x} A独有={y}")
    if x + y:
        p = 2 * sum(comb(x + y, i) for i in range(min(x, y) + 1)) / 2 ** (x + y)
        print(f"   McNemar 精确双侧 **p = {min(p, 1.0):.4f}**"
              + ("  ⇒ 与随机不可分，不足以断言哪一臂更好" if p > 0.05 else ""))
    else:
        print("   零不一致对 ⇒ 这把尺子在本窗口对该干预**没有分辨力**，不是「两臂一样好」")


if __name__ == "__main__":
    main()
