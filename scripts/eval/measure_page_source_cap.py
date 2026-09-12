#!/usr/bin/env python3
"""读取侧单源限流的**精确反事实**：复刻生产归档第 1 页的 where，再算加了限流会怎样。

为什么这条杠杆能离线精确算，而供给侧那些不能
（指标档「量具纪律」与 `measure_archive_composition.py` 的 docstring 都写着「本量具不重放、不模拟」）：
`_compute_archive_page` → `_archive_items` 是 `curated_items` 上的
「按生产 where 过滤 → 按 `published_at` 倒序 → `LIMIT/OFFSET`」，**是既有历史的纯函数**。
加一条单源上限后产出的仍是这段历史的确定性重排——不必重放 curate、不必猜哪些条目本会被选中。
供给侧杠杆（停源、改系数、抬 floor）改变的是「哪些条目进 `curated_items`」，那份反事实历史不存在。

**本量具与 `measure_archive_composition.archive_page()` 的差别，是它存在的第一个理由**：
那一个只写 `GROUP BY i.id`，**不带**生产的三条 where——
`s.enabled=1`、`COALESCE(s.kind,'feed') != 'wechat'`、`deduped_item_clause('i')`
（见 `web/routes/curated_archive.py:97`）。于是它量的页面与用户真正看到的那一页**不是同一批条目**。
本量具直接 import 生产的 `_archive_where` / `deduped_item_clause`，**不重抄一份**——
重抄就是第二真相，两份会各自漂移（本仓已为此撤回过读数）。
下面的 `--compare-loose` 会把两个口径的差直接打出来，用来量那个近似有多大。

**限流按 (source_id, 上海日) 计数，不是「每页 N 格」**：`_archive_items` 用 SQL `LIMIT ? OFFSET ?`，
而「每页 N 格」是**有状态**的——第 2 页得知道第 1 页消耗了什么，总数也会与实际页数不符。

⚠️ **本量具算的是「第 1 页长什么样」，落地形态另有一层，别把两者混了**（2026-09-12 决策评审报出）：
把它实现成 `WHERE rn <= cap` 的**全局过滤**会让每源每日第 cap+1 条以后**在所有分页中不可达**，
直接违反 ADR-006「归档须包含所有曾被选入精选的条目」与 ux-contract:277。
可实现的是**改排序不过滤**：`ORDER BY (rn - 1) / cap, published_at DESC, fetched_at DESC, id DESC`
——桶 0 是每源每日前 cap 条、桶 1 是下一批，**一条不丢、总数不变**，
而第 1 页与本量具的输出**逐条相同**（实测 6/6 天）。

⚠️ **页内单源上限不是 2×cap**：页面可跨**任意多个**上海日 ⇒ 理论上限是 `cap × 页面跨越的日数`。
`4–5` 只是本窗口第 1 页的观测值，不是算法保证。

只读（`mode=ro`，**不带 `immutable=1`**——有写者时它抛 `database is malformed`）、零 LLM 调用、
标签一律用 AIHOT 自己的（量具纪律 ⑤：机制读的标签与计分器读的必须分开；本机制**根本不读标签**）。

用法：
    uv run python scripts/eval/measure_page_source_cap.py
    uv run python scripts/eval/measure_page_source_cap.py --until 2026-09-06 --compare-loose
"""

from __future__ import annotations

import argparse
import importlib.util
import sqlite3
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
HERE = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location(
    "_composition", HERE / "measure_curated_composition.py"
)
assert _spec and _spec.loader
_comp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_comp)

# 生产的 where 直接 import，不重抄。
from airadar.web.routes.categories import deduped_item_clause as _dedup  # noqa: E402
from airadar.web.routes.curated_archive import _archive_where  # noqa: E402

CATS = list(_comp.CATEGORIES)
PAGE = 40
# 补位深度：实测凑满 40 格需要扫到第 43–56 条（cap=4），取 400 留足余量。
# 不够时页面会**不足 40 格**，故下面逐日断言页满，不靠这个常量保证。
DEPTH = 400


def ranked(conn: sqlite3.Connection, day: str, *, loose: bool) -> list[tuple[str, str, str]]:
    """上海日 `day` 结束那一刻的归档排序流，返回 (url, source_id, published_at)。

    `loose=True` 复现 `measure_archive_composition.archive_page()` 的口径（不带生产 where），
    只用来量那个近似有多大。切点换算与排序两边一致：上海日结束 = UTC `day 16:00:00Z`。
    """
    cutoff = f"{day.replace('-', '')}T160000Z"
    if loose:
        where, params = "", []
    else:
        where, params, _sub = _archive_where(None, None)
    return [
        (str(u or ""), str(s or ""), str(p or ""))
        for u, s, p in conn.execute(
            f"""
            SELECT i.url, i.source_id, i.published_at
            FROM items i
            JOIN sources s ON s.id = i.source_id
            JOIN curated_items c ON c.item_id = i.id
            {('WHERE ' + where[len('WHERE '):] + ' AND ') if where else 'WHERE '}c.run_id < ?
            GROUP BY i.id
            ORDER BY i.published_at DESC, i.fetched_at DESC, i.id DESC
            LIMIT ?
            """,
            (*params, cutoff, DEPTH),
        )
    ]


def capped(rows: list[tuple[str, str, str]], cap: int | None) -> list[str]:
    """按 (source_id, 上海日) 限流后取前 40。cap=None 即现状。"""
    out: list[str] = []
    per: Counter = Counter()
    for url, src, pub in rows:
        if len(out) >= PAGE:
            break
        if cap is not None:
            key = (src, _comp.sel._shanghai_date(pub))
            if per[key] >= cap:
                continue
            per[key] += 1
        out.append(url)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(REPO / "data" / "radar.db"))
    ap.add_argument("--until", default=None, help="只读到这一上海日为止（归档是累积的，只能这么切）")
    ap.add_argument("--caps", default="6,5,4,3,2", help="要扫的上限值，逗号分隔")
    ap.add_argument("--compare-loose", action="store_true",
                    help="同时按 `archive_page()` 的宽口径（不带生产 where）算一遍，量那个近似有多大")
    args = ap.parse_args()

    aihot = _comp.load_aihot()
    label: dict[str, str] = {}
    selected: set[str] = set()
    ref_by_day: dict[str, Counter] = {}
    for r in aihot.values():
        if r.get("url"):
            n = _comp.normalize_url(r["url"])[0]
            if r.get("category"):
                label[n] = r["category"]
            if r["selected"]:
                selected.add(n)
        if r["selected"] and r["published"] and r.get("category"):
            ref_by_day.setdefault(r["published"], Counter())[r["category"]] += 1
    days = sorted(d for d, c in ref_by_day.items() if sum(c.values()) >= 5)
    if args.until:
        days = [d for d in days if d <= args.until]
    if not days:
        raise SystemExit("--until 过滤后没有窗口了")

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn.execute("PRAGMA busy_timeout=120000")

    ref: Counter = Counter()
    for d in days:
        ref += ref_by_day[d]

    if args.compare_loose:
        # **把差直接算出来打印，不让读者自己比两张表**（2026-09-12 复核报出：
        # 原来只并排输出 strict/loose 两张汇总表，而「每次都把这个差打出来」是更强的主张）。
        from airadar.web.routes.curated_archive import _archive_where as _w
        where, wparams, _ = _w(None, None)
        cut = f"{days[-1].replace('-', '')}T160000Z"
        base = ("SELECT COUNT(*) FROM (SELECT i.id FROM items i JOIN sources s ON s.id=i.source_id "
                "JOIN curated_items c ON c.item_id=i.id ")
        n_loose = conn.execute(base + "WHERE c.run_id < ? GROUP BY i.id)", (cut,)).fetchone()[0]
        n_strict = conn.execute(base + f"{where} AND c.run_id < ? GROUP BY i.id)",
                                (*wparams, cut)).fetchone()[0]
        rank = conn.execute(
            base + "WHERE c.run_id < ? GROUP BY i.id HAVING MAX(i.published_at) > "
                   "(SELECT MAX(i2.published_at) FROM items i2 "
                   " JOIN sources s2 ON s2.id=i2.source_id JOIN curated_items c2 ON c2.item_id=i2.id "
                   f" WHERE c2.run_id < ? AND NOT (s2.enabled=1 AND "
                   f"       COALESCE(s2.kind,'feed')!='wechat' AND {_dedup('i2')})))",
            (cut, cut)).fetchone()[0] + 1
        print(f"\n>>> 生产 where 与 `archive_page()` 宽口径的差（截至 {days[-1]}）")
        print(f"    归档全集 {n_loose} → 生产口径 {n_strict}，**排除 {n_loose - n_strict} 条**"
              f"（{(n_loose - n_strict) / max(n_loose, 1) * 100:.1f}%）")
        print(f"    **被排除者里最新那条的全局名次：第 {rank} 位**"
              f" ⇒ 页面只取前 {PAGE} 条，{'两口径在第 1 页上等价' if rank > PAGE else '**两口径在第 1 页上已分开**'}")
        print("    ⚠️ 这是**本窗口的事实、不是恒等式**：某个高产源被停用的当天，"
              "被排除者就是新条目，名次会落进前 40。")
        first = []
        for d in days:
            lo = [u for u, _s, _p in ranked(conn, d, loose=True)]
            st = [u for u, _s, _p in ranked(conn, d, loose=False)]
            first.append((d, next((i + 1 for i, (a, b) in enumerate(zip(lo, st)) if a != b), None)))
        bad = [d for d, i in first if i is not None and i <= PAGE]
        print(f"    逐日前 {DEPTH} 条的第一处分歧位次：{[(d, i) for d, i in first]}"
              f"{'  ⚠️ 有天数落进前 40：' + str(bad) if bad else ''}")

    for loose in ([False, True] if args.compare_loose else [False]):
        tag = "宽口径（= archive_page，不带生产 where）" if loose else "生产口径（enabled + 非微信 + 去重）"
        rows_by_day = {d: ranked(conn, d, loose=loose) for d in days}
        short = [(d, len(capped(rows_by_day[d], 4))) for d in days
                 if len(capped(rows_by_day[d], 4)) < PAGE]
        print(f"\n=== {tag} · 窗口 {days[0]}..{days[-1]}（{len(days)} 天）===")
        if short:
            print(f"    ⚠️ 有 {len(short)} 天在 cap=4 下**填不满 40 格**（补位深度 {DEPTH} 不够）：{short}")
        else:
            print(f"    补位检查：cap=4 下 {len(days)}/{len(days)} 天仍满 {PAGE} 格")
        print(f"{'单源上限':16}{'页内有标签':>10}{'页面重合':>9}{'TV':>8}{'k/5':>6}"
              f"{'当日发布%':>10}  逐类（tip/model/product/industry/paper）")
        caps: list[int | None] = [None] + [int(x) for x in args.caps.split(",") if x.strip()]
        for cap in caps:
            ours: Counter = Counter()
            overlap = 0
            fresh = 0
            slots = 0
            for d in days:
                page = capped(rows_by_day[d], cap)
                pub_by_url = {u: p for u, _s, p in rows_by_day[d]}
                slots += len(page)
                for url in page:
                    n = _comp.normalize_url(url)[0]
                    if (c := label.get(n)) is not None:
                        ours[c] += 1
                    if n in selected:
                        overlap += 1
                    if _comp.sel._shanghai_date(pub_by_url.get(url, "")) == d:
                        fresh += 1
            v = _comp.class_verdicts(ours, ref)
            n_ours = sum(ours.values()) or 1
            name = "无（现状）" if cap is None else f"{cap}/源/日"
            print(f"{name:16}{n_ours:>10}{overlap:>9}{_comp.total_variation(ours, ref):>8.3f}"
                  f"{v['inside_count']:>5}/5{100 * fresh / max(slots, 1):>9.1f}%  "
                  + " ".join(f"{100 * ours[c] / n_ours:5.1f}" for c in CATS))
        tn = sum(ref.values())
        print(f"{'AIHOT 精选':16}{tn:>10}{'—':>9}{'—':>8}{'—':>6}{'—':>10}  "
              + " ".join(f"{100 * ref[c] / tn:5.1f}" for c in CATS))


if __name__ == "__main__":
    main()
