"""为「正文回抓」这条假设建一个**覆盖层**：抓回真实正文，重跑三个阶段，结果写侧文件。

**本文件不写生产**：`radar.db` 全程 `mode=ro`，且它**不调任何 LLM provider**，
产出只落 `--out` 那个 SQLite，回滚 = 删该文件。
⚠️ **但下一段会写生产，别把这句话读到整条流程上**：`rejudge_body_overlay.py` 调 provider，
而 provider 每次 completion 都写 `data/llm_usage.db`、失败时写 `data/ark-breaker.json`。
**成本台账那一条按用户 2026-09-11 的裁定不改道**（评测调用计入项目总用量）；
**熔断器那一条改道**（它是生产每次调用都读的状态，不是账）。判据见它的 docstring。
`scripts/eval/simulate_multirun_archive.py --score-overlay <path>` 最后消费覆盖层（只读）。

为什么不复制生产库：它 5.3 GB 而本机只剩 9 GB；且一旦 `AI_RADAR_DB` 写错就会改到生产。

**选源判据不写死源名**：只收历史 prefilter AI 率 ≥ `--min-ai-rate` 的源。它排掉的是
「聚合整个 web、多数条目本就该拒」的灌量源——实测 `buzzing_hn` 12.2% / `ithome` 20.6%，
而为它们多抓 5061 次只多救 9 条 AIHOT 精选（562 次/条）。新源接进来自动分档，不必维护名单。

**SSRF 防护是这里的第一等公民，不是加固**（2026-09-11 决策评审报出）：`entry.link` 是
feed 喂进来的、几乎不受限，而 `airadar.egress` 对字面 loopback 主机**特意走 direct**
⇒ 不设防时一个被入侵的 feed 能让本机去 GET 自己的内部服务。故：只收 http(s)；
**逐跳校验**（不交给 httpx 自动跟转，每一跳都重新判主机）；拒绝内网名与非公网的字面地址。
判据按**主机形态**而不是 DNS 解析——理由与那次实测误拦见 `assert_public_http` 的 docstring。

**接受条件不是「抽到了非空正文」**：`clean_content` 会接受任何非空 trafilatura 结果，
所以「抓到登录页 / 页脚 / 整页列表」不会走失败分支。这里要求新正文比原正文长
`--min-gain` 倍才替换，并记录 `fetched_len` 供事后判「抽错」。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "src"))

from airadar.enrich.article_context import (  # noqa: E402,F401
    MAX_BYTES,
    MAX_REDIRECTS,
    UnsafeTarget,
    _as_ip,
    assert_public_http,
    fetch_article,
)


def eligible_sources(conn: sqlite3.Connection, min_rate: float, lookback_days: int) -> set[str]:
    keep: set[str] = set()
    for row in conn.execute(
        # **分母要按条目去重**（复核轮的 L17）：`COUNT(*)` 数的是 prefilter 行，
        # 被重跑过的条目会重复计入 ⇒ 这个「AI 率」不是条目级的率。取每条目**最新**那一行。
        "SELECT source_id s, SUM(is_ai)*1.0/COUNT(*) r, COUNT(*) n FROM ("
        "  SELECT i.source_id AS source_id, i.id AS iid,"
        "         (e.output_json LIKE '%\"is_ai_related\":true%') AS is_ai,"
        "         ROW_NUMBER() OVER (PARTITION BY i.id ORDER BY e.id DESC) AS rn "
        "  FROM items i JOIN item_evaluations e ON e.item_id=i.id AND e.stage='prefilter' "
        f"  WHERE e.error IS NULL AND i.published_at >= datetime('now','-{lookback_days} days')"
        ") WHERE rn=1 GROUP BY source_id"
    ):
        # 样本太少判不出率，放行——它们量也小，不构成灌量风险。
        if row["n"] < 10 or row["r"] >= min_rate:
            keep.add(row["s"])
    # **x 类整体排除**（2026-09-11 review-gate 的 H3）：对一条推文，`entry.link` 指向推文本身，
    # **不存在可抓的文章正文**。实测第一版 971 条产物里 757 条（78%）是 x 类，抓回来的是
    # 句柄 + 日期 + 推文 + **粘在末尾的互动计数**（`13521891`）；14 组 40 条共用同一段正文、
    # 最大一组 ×6。信息增量为零、噪声为正，而 `--min-gain` 挡不住（23 字拿到 46 字就过闸）。
    dropped = {s for s in keep if s.startswith("x_")}
    return keep - dropped


RETRYABLE_HTTP = {"408", "429"}


def _miss_is_permanent(note: str) -> bool:
    """这条 miss 再跑一次还会一样吗。判不准就当可重试——重跑一次的代价远小于粘住一条真样本。"""
    if note.startswith("增益不足"):
        return True
    if note.startswith("HTTP "):
        code = note.split()[1] if len(note.split()) > 1 else ""
        return code.startswith("4") and code not in RETRYABLE_HTTP
    if note.startswith(("PDF", "content-type", "过大", "抽取为空")):
        return True
    # UNSAFE / 超时 / 连接错误 / 跟转过多 / 5xx：判据或环境一变结果就变。
    return False


def _cap_per_source(rows: list, share: float) -> list:
    """把单源占比压到 `share` 以下，按 `published_at` 降序保留每源最新的那些。

    **这是类别均衡探针用的上限，不是生产机制**，而且它让样本**不再代表生产会做什么**
    ——短正文人群天然以 `paper` 为主（该类 86.0% 的条目正文 <300 字），那是真实结构、不是偏差。
    所以默认关闭；只在刻意要一个类别均衡的探针时才开。
    做法是解一次自洽的上限：砍掉超配源之后总数变小，上限也跟着变小，故迭代到稳定。
    """
    from collections import Counter

    kept = list(rows)
    for _ in range(20):
        counts = Counter(r["source_id"] for r in kept)
        cap = max(1, int(len(kept) * share))
        over = {s for s, n in counts.items() if n > cap}
        if not over:
            break
        out, taken = [], Counter()
        for r in sorted(kept, key=lambda r: r["published_at"], reverse=True):
            sid = r["source_id"]
            if sid in over and taken[sid] >= cap:
                continue
            taken[sid] += 1
            out.append(r)
        kept = out
    counts = Counter(r["source_id"] for r in kept)
    top = counts.most_common(1)[0] if counts else ("-", 0)
    print(f"单源上限 {share:.0%}：样本 {len(rows)} → {len(kept)} 条，"
          f"最大源现占 {top[1] / max(len(kept), 1) * 100:.1f}% ({top[0]})")
    return kept


def open_overlay(path: Path) -> sqlite3.Connection:
    out = sqlite3.connect(path)
    out.execute(
        "CREATE TABLE IF NOT EXISTS body_overlay ("
        " item_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, url TEXT NOT NULL,"
        " old_len INTEGER NOT NULL, new_len INTEGER NOT NULL,"
        " content_text TEXT NOT NULL, note TEXT NOT NULL, fetched_at TEXT NOT NULL)"
    )
    out.execute(
        "CREATE TABLE IF NOT EXISTS fetch_miss ("
        " item_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, url TEXT NOT NULL, note TEXT NOT NULL)"
    )
    out.commit()
    return out



def _print_db_identity(path: str) -> None:
    """打印所读库的身份，让「三段读了三个不同时刻」看得见（复核轮的 H10）。

    生产库此刻仍在写，而 build → rejudge → simulate 是三次独立进程、三个快照
    ⇒ 测出来的 Δ 里混着「机制生效」与「池子长大了」。本机 scratchpad 里有 `radar-frozen.db`，
    三段都指它即可消除；但**默认值仍是生产库**，所以这里至少把身份摆出来——
    比较两份输出时，快照不一致一眼看得出。
    """
    import os
    from datetime import UTC
    from datetime import datetime as _dt
    try:
        st = os.stat(path)
    except OSError as exc:
        print(f"库身份：{path}（stat 失败 {type(exc).__name__}）")
        return
    mtime = _dt.fromtimestamp(st.st_mtime, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"库身份：{path}  mtime={mtime}  {st.st_size / 2**30:.2f} GiB")

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(REPO / "data" / "radar.db"),
                    help="**三段式量具应当共用一个冻结快照**（2026-09-11 review-gate 的 H10）："
                         "生产库此刻仍在写，而 build / rejudge / simulate 读的是三个不同时刻 ⇒ "
                         "测出来的 Δ 里混着「正文回抓」与「池子长大了」。"
                         "本机 scratchpad 里已有 `radar-frozen.db`，显式传它。")
    ap.add_argument("--out", required=True, help="覆盖层 SQLite 落点（新文件；已存在则续跑，跳过已抓过的）")
    ap.add_argument("--days", type=int, default=3, help="按 published_at 取最近几天（默认 3）")
    ap.add_argument("--max-body", type=int, default=300, help="正文短于多少字才回抓（默认 300）")
    ap.add_argument("--min-gain", type=float, default=2.0, help="新正文至少是原来的几倍才替换（默认 2）")
    ap.add_argument("--min-ai-rate", type=float, default=0.30, help="信源历史 prefilter AI 率下限（默认 0.30）")
    ap.add_argument("--max-per-source", type=float, default=0.0,
                    help="单个信源在本批样本里的占比上限。**默认 0（关闭），这是有意的**："
                         "短正文人群天然以 `paper` 为主——实测该类 86.0%% 的条目正文不足 300 字"
                         "（次位 model 45.4%%、product 只有 29.3%%），因为 `hf_daily_papers` 一类"
                         "每日论文源给的就是摘要。**那不是取样偏差，那就是生产回抓会作用的真实人群**，"
                         "所以估生产效应必须用它。设 >0 只适合做**类别均衡的探针**"
                         "（本脚本一度把 0.25 当默认，那是错的：它让样本不再代表生产会做什么）。"
                         "顺带一条不需要跑就成立的推论：该人群 37.6%% 是 paper，而权威归档面上 "
                         "`paper` 已出界 +4.38pp ⇒ **回抓作为生产机制自带把构成推更差的偏置**。")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--limit", type=int, help="只跑前 N 条（冒烟用）")
    args = ap.parse_args()

    _print_db_identity(args.db)
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=120000")

    keep = eligible_sources(conn, args.min_ai_rate, lookback_days=14)
    # **窗口边界要与库里的格式同形**（复核轮的 M16）：`published_at` 存的是
    # `'2026-09-09T01:06:56Z'`，而 `datetime('now','-3 days')` 给的是 `'2026-09-09 01:06:56'`
    # ——字符串比较下 `'T'(0x54) > ' '(0x20)` ⇒ 边界日被整天纳入，窗口不是精确 N 天。
    # 顺带挡住未来日期（库里实测有 `2026-09-14T00:00:00Z`），它会被无条件收进任何窗口。
    cutoff = (datetime.now(UTC) - timedelta(days=args.days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    upper = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = [
        r for r in conn.execute(
            "SELECT id, source_id, url, content_text, published_at FROM items "
            "WHERE published_at >= ? AND published_at <= ? "
            f"  AND length(trim(content_text)) < {args.max_body}",
            (cutoff, upper),
        )
        if r["source_id"] in keep
    ]
    print(f"窗口 [{cutoff}, {upper}]（按 published_at，已挡未来日期）")
    if args.max_per_source > 0 and rows:
        rows = _cap_per_source(rows, args.max_per_source)
    out = open_overlay(Path(args.out))
    # **只有"永久"的 miss 才算已完成**（2026-09-11 review-gate 的 H6）。第一版把整张
    # `fetch_miss` 并入 `done`，于是瞬时错误、以及**被闸误拦**的条目被永久粘住、再跑也不重试。
    # 已经咬过一次：第一版 SSRF 闸误拦的那条 youtube 被粘死在覆盖层里，而现闸放过它。
    # 判据：`HTTP 4xx`（除 408/429）与「增益不足」是关于目标本身的、永久；
    # 超时 / 连接错误 / UNSAFE / `HTTP 5xx` 都可能随环境或判据改变，**可重试**。
    done = {r[0] for r in out.execute("SELECT item_id FROM body_overlay")}
    for item_id, note in out.execute("SELECT item_id, note FROM fetch_miss"):
        if _miss_is_permanent(str(note)):
            done.add(item_id)
    todo = [r for r in rows if r["id"] not in done]
    if args.limit:
        todo = todo[: args.limit]
    print(f"合格信源 {len(keep)} 个（AI 率 ≥ {args.min_ai_rate:.0%}）")
    print(f"窗口内短正文条目 {len(rows)}，已处理 {len(rows) - len([r for r in rows if r['id'] not in done])}，本次跑 {len(todo)}")

    lock = threading.Lock()
    stats = {"kept": 0, "miss": 0, "thin": 0, "unsafe": 0}

    def work(row: sqlite3.Row) -> tuple[sqlite3.Row, str, str]:
        body, note = fetch_article(row["url"], args.timeout)
        return row, body, note

    with ThreadPoolExecutor(max_workers=args.workers) as pool, out:
        futures = [pool.submit(work, r) for r in todo]
        for i, fut in enumerate(as_completed(futures), 1):
            row, body, note = fut.result()
            old = len((row["content_text"] or "").strip())
            with lock:
                if body and len(body) >= max(old, 1) * args.min_gain:
                    out.execute(
                        "INSERT OR REPLACE INTO body_overlay VALUES (?,?,?,?,?,?,?,datetime('now'))",
                        (row["id"], row["source_id"], row["url"], old, len(body), body, note),
                    )
                    stats["kept"] += 1
                else:
                    reason = note if not body else f"增益不足 {old}→{len(body)}"
                    out.execute(
                        "INSERT OR REPLACE INTO fetch_miss VALUES (?,?,?,?)",
                        (row["id"], row["source_id"], row["url"], reason),
                    )
                    stats["unsafe" if note.startswith("UNSAFE") else ("thin" if body else "miss")] += 1
                if i % 100 == 0:
                    out.commit()
                    print(f"  {i}/{len(todo)}  替换 {stats['kept']} · 抓不到 {stats['miss']}"
                          f" · 增益不足 {stats['thin']} · 拦下不安全 {stats['unsafe']}", flush=True)
        out.commit()

    print(f"\n完成：替换 {stats['kept']} · 抓不到 {stats['miss']} · 增益不足 {stats['thin']}"
          f" · 拦下不安全 {stats['unsafe']}")
    print(f"覆盖层：{args.out}")


if __name__ == "__main__":
    main()
