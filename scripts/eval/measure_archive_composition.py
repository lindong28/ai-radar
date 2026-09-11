#!/usr/bin/env python3
"""在**用户真正看到的那一面**上量达标线：跨-run 累积归档的第 1 页。

为什么另起一个量具（用户 2026-09-11 裁定「立刻把达标线改挂到归档面」）：
`measure_curated_composition.py` 量的是**单轮重放的 40 条**，而项目 `CLAUDE.md` 里写着
「用户看到的就是那 40 条」——**那句话不成立**。首页 `web/static/app.js:1810` 调
`/api/v1/curated?limit=40&page=N`，**不带 `run_id` / `date`** ⇒ 按 [ADR-006] 进入归档模式：
跨 run 去重累积，`ORDER BY i.published_at DESC, i.fetched_at DESC, i.id DESC`。
实测单日约 **48 个 run**（约 1920 条精选），首页 40 条是从这个并集里按发布时间取前 40。

两者的差不是措辞：**本轮 curate 丢掉一条，不撤销它在更早 run 里的归档成员资格**，
所以任何"后置删减"型机制在重放上量到的效应都**系统性高于**用户实际会看到的。
这一条是外部决策评审（Codex，2026-09-11，session 01a08f1b）报出来的，判据 5「与既有决策交界」。

**本量具不重放、不模拟**：它读**生产实际写下的** `curated_items` 历史，
所以它答的是「用户实际看到过什么」，而不是「某个机制会产出什么」。
代价也在这里——**它不能用来 A/B 备选机制**，那仍归重放量具。

用法：
    uv run python scripts/eval/measure_archive_composition.py
    uv run python scripts/eval/measure_archive_composition.py --record
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import subprocess
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "_composition", HERE / "measure_curated_composition.py"
)
assert _spec and _spec.loader
_comp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_comp)

CATS = list(_comp.CATEGORIES)
PAGE = 40  # 首页 `limit=40`，见 web/static/app.js 的 curatedApiPath


def archive_page(conn: sqlite3.Connection, day: str, limit: int) -> list[tuple[str, str]]:
    """复现归档第 1 页在**上海日 `day` 结束那一刻**的样子，返回 (url, published_at)。

    **切点必须换算时区。** `run_id` 是 UTC（`select._run_id` 用 `datetime.now(UTC)`），
    而逐日归属一律用上海日（量具纪律第 1 条）。上海日 `day` 的结束 = UTC `day 16:00:00Z`。
    直接拿 `day` 当 UTC 前缀比会把当天 16:00Z 之后的 run 算进前一天，正是那条纪律要防的错开一天。

    去重与排序照抄 `web/routes/curated_archive.py`：按 item 去重（那里用
    `c.run_id = (SELECT MAX(run_id) ...)`，这里等价地 `GROUP BY i.id`，因为本量具只取 url
    与发布时间、不取随 run 变化的元数据），`ORDER BY published_at DESC, fetched_at DESC, id DESC`。
    """

    cutoff = f"{day.replace('-', '')}T160000Z"
    rows = conn.execute(
        """
        SELECT i.url, i.published_at
        FROM items i
        JOIN curated_items c ON c.item_id = i.id
        WHERE c.run_id < ?
        GROUP BY i.id
        ORDER BY i.published_at DESC, i.fetched_at DESC, i.id DESC
        LIMIT ?
        """,
        (cutoff, limit),
    ).fetchall()
    return [(str(u or ""), str(p or "")) for u, p in rows]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(_comp.REPO / "data" / "radar.db"))
    ap.add_argument("--limit", type=int, default=PAGE, help="首页每页条数（生产 40）")
    ap.add_argument(
        "--record",
        nargs="?",
        const=str(_comp.DEFAULT_HISTORY),
        default=None,
        help="把本次判定追加进历史序列。**行里带 `surface: \"archive\"`**——既有行没有这个键，"
        "即「当时量的是单轮重放面」，两代不可直接比，但各自内部可比。",
    )
    args = ap.parse_args()

    aihot = _comp.load_aihot()
    reference_by_day: dict[str, Counter] = {}
    label_by_url: dict[str, str] = {}
    for record in aihot.values():
        if record.get("category") and record.get("url"):
            label_by_url[_comp.normalize_url(record["url"])[0]] = record["category"]
        if record["selected"] and record["published"] and record.get("category"):
            reference_by_day.setdefault(record["published"], Counter())[record["category"]] += 1

    # 与重放量具同一条门槛：AIHOT 当日精选 >= 5。**它那边还要求我方当日候选 >= 200**，
    # 那条是重放的前提（没有候选池就重放不了），本量具读的是已经写下的历史，不适用。
    # 两边窗口集因此可能不同——所以下面把实际用到的日子逐个打印，别默认它们一致。
    days = sorted(d for d, c in reference_by_day.items() if sum(c.values()) >= 5)
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)

    print(f"归档面（跨-run 累积、按发布时间倒序、每页 {args.limit} 条）——用户首页实际看到的那一面")
    print(f"{'上海日':12}{'页内条数':>8}{'有 AIHOT 标签':>14}{'当日发布占比':>14}  逐类（AIHOT 标签）")
    ours_pooled: Counter = Counter()
    reference_pooled: Counter = Counter()
    used: list[str] = []
    for day in days:
        page = archive_page(conn, day, args.limit)
        if not page:
            continue
        labelled = Counter()
        for url, _pub in page:
            cat = label_by_url.get(_comp.normalize_url(url)[0])
            if cat:
                labelled[cat] += 1
        # 归档第 1 页按发布时间取，**不保证都是当日条目**——它可能跨到前一天。
        # 这个比例决定"拿它与 AIHOT 当日构成比"是不是同一件事，所以每行都打印。
        same_day = sum(1 for _u, p in page if _comp.sel._shanghai_date(p) == day)
        detail = " ".join(f"{c[:3]}:{labelled[c]}" for c in CATS if labelled[c])
        print(
            f"{day:12}{len(page):>8}{sum(labelled.values()):>14}"
            f"{100 * same_day / len(page):>13.1f}%  {detail}"
        )
        ours_pooled += labelled
        reference_pooled += reference_by_day[day]
        used.append(day)

    if not used:
        raise SystemExit("没有可比日窗")

    print(f"\n窗口 {len(used)} 个：{used[0]}..{used[-1]}")
    tv = _comp.total_variation(ours_pooled, reference_pooled)
    verdicts = _comp.class_verdicts(ours_pooled, reference_pooled)
    print(f"\n>>> 达标线：逐类占比落进 AIHOT 该类的 95% CI（合并口径，TV {tv:.3f}）")
    if verdicts["inside_count"] is None:
        print(f"    判不了：{verdicts['reason']}")
        vnull = {}
    else:
        print(f"{'类别':8}{'我方占比':>12}{'AIHOT 占比':>13}{'AIHOT 95% CI':>20}{'判定':>6}{'离区间':>10}")
        for row in verdicts["rows"]:
            low, high = row["reference_ci"]
            gap = "" if row["inside"] else f"{row['gap_pp']:+.2f}pp"
            print(
                f"{row['category']:8}{100 * row['ours_share']:11.2f}%{100 * row['reference_share']:12.2f}%"
                f"   [{100 * low:5.2f},{100 * high:5.2f}]"
                f"{'IN' if row['inside'] else 'OUT':>6}{gap:>10}"
            )
        print(
            f"    ⇒ {verdicts['inside_count']}/{verdicts['total']} 类落进区间"
            f"（我方 n={verdicts['n_ours']}，AIHOT n={verdicts['n_reference']}）"
            + ("   **本判据下已达标**" if verdicts["passed"] else "")
        )
        # 与重放量具同一条纪律：绝对值没有零假设就读不动。
        vnull = _comp.verdict_null(reference_pooled, verdicts["n_ours"])
        if vnull:
            print(
                f"    零假设（我方构成 = AIHOT 本次观测到的构成）: P(5/5) = {vnull['p_all_inside']:.3f}"
                f"   E[落进数] = {vnull['expected_inside']:.2f}/5"
            )

    if args.record:
        head = subprocess.run(
            ["git", "-C", str(_comp.SUBMODULE), "rev-parse", "--short", _comp.CAPTURES_REF],
            capture_output=True, check=False,
        )
        row = {
            "recorded_at": _comp.sel._utc_now(),
            # **新键，既有行没有**。缺它即「当时量的是单轮重放面」，两代不可直接比。
            "surface": "archive",
            "page_limit": args.limit,
            "days": used,
            "captures_sha": head.stdout.decode().strip() or None,
            "db_items": conn.execute("SELECT COUNT(*) FROM items").fetchone()[0],
            "total_variation": tv,
            "inside_count": verdicts["inside_count"],
            "passed": verdicts["passed"],
            "n_ours": verdicts["n_ours"],
            "n_reference": verdicts["n_reference"],
            "rows": verdicts["rows"],
            "verdict_null_p_all_inside": vnull.get("p_all_inside"),
            "verdict_null_expected_inside": vnull.get("expected_inside"),
        }
        target = Path(args.record)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"\n已追加一行到 {target}（surface=archive）")


if __name__ == "__main__":
    main()
