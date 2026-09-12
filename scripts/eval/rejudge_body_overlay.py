"""覆盖层第二阶段：用回抓到的正文重跑 prefilter / scoring / enrich，结果写回覆盖层。

⚠️ **本文件第一版声明「零生产写入」，那句话是假的**（2026-09-11 由 review-gate 报出并实测确认）。
`provider/deepseek_chat.py` 在**每一次** completion 后调 `record_llm_usage_best_effort()`，
落点是 `data/llm_usage.db`——三个 stage 的 provider 全部经由它。实测一次运行往生产成本台账
写进约 3199 行、3.68M token，**带真实 `stage`/`model`/`item_id`，与生产调用结构上不可区分**，
而 `admin/cost_report.py` / `cost_audit.py` / `alerts.py` 都消费那张表。
第二条路是 `provider/ark_breaker.py`：一次 429 就 `trip()` 并写 `data/ark-breaker.json`，
**冷却 7200 秒、文件级跨进程**，生产路径会读到它 ⇒ 评测能让生产静默停 ARK 两小时。

⇒ **用户 2026-09-11 裁定：成本台账那一条不隔离**（「不用区分评测和生产，我只关心来自
这个项目的总体 LLM usage」），故评测调用照常计入 `data/llm_usage.db`。
**熔断器那一条仍然隔离**——它不是账、是会让生产停摆的状态。见 `_isolate_side_effects`。
**可迁移的一条仍然成立：「只读 items」不等于「零写入」——凡调 provider 就会写。**

只读**生产 `radar.db`**（`mode=ro`）；重判产出落 `--overlay` 那个侧文件。

三个阶段都跑，因为两个权威指标各要一半：
- `prefilter` 决定**准入**——`②` 格那 14 条死在这里；
- `scoring` 决定**过闸与名次**——`③` 格 42 条里 35 条正文短；
- `enrich` 决定**类别**——版面构成指标按类别算，正文变了类别可能跟着变。

调用的是三个 stage 各自 `_provider_from_env()` 拿到的同一个 provider，prompt 一字未改
——本实验要测的是**正文**这个自变量，不是 prompt。

**并发默认 4，不是 8**（2026-09-11 由 review-gate 改正）。第一版取 8、理由写的是
「本仓别处用过同一量级」——`CLAUDE.md` 的 coding-guidelines 明文说这种理由**不作数**。
（顺带订正我自己引错的证据：生产 scoring CLI 的 `--workers` 默认是 **1**，那个 8 来自
`scripts/backfill_rescore.sh` 的 `BACKFILL_WORKERS:-8`，是**回填**作业；
而那个脚本的文件头自己就记着八 worker 造成过 `PIPELINE STARVATION`——比我原来引的证据更硬。）
该端点**非独占**：回填在跑时叠起来打向 ARK 是 12+。
真正限住它的约束是：一次 429 会让 `ark_breaker` trip 并冷却 **7200 秒**，
而那个代价**不落在评测上、落在生产上**——所以这里的上限由"别把生产打停"定，不由吞吐定。
微信正文抓取（`fetcher/runner.py` 的 `WECHAT_BODY_FETCH_WORKERS`）实测取的也是 4。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))


def _isolate_side_effects(overlay_path: str) -> dict[str, str]:
    """把 provider 的两条**生产状态写入**改道到侧文件旁边。

    **时序契约**：`ark_breaker._state_path()` 在**每次调用时**读 env，不在 import 时读，
    所以本函数不必早于 import——只需早于**第一次 provider 调用**。故放在 argparse 之后立刻调。

    **只改道熔断器，不改道成本台账**（用户 2026-09-11 裁定：「不用区分评测和生产，
    我只关心来自这个项目的总体 LLM usage」）。两者性质不同：

    - `data/llm_usage.db` 是**记账**。按用户口径评测调用**本来就该计入**项目总用量，
      把它移走反而让总账少记 —— 所以**不隔离**。（本文件第一版隔离过它，方向是反的。）
    - `data/ark-breaker.json` 是**状态**，不是账：一次 429 就 `trip()`、冷却 7200 秒，
      而**生产每次调用都读它** ⇒ 一次评测能让生产静默停 ARK 两小时。**必须隔离。**

    返回实际生效的路径，供调用方打印——**不打印就没人知道隔离到底生效没有**，
    而失败形态（env 名写错）与"隔离生效了"在所有可观察面上同形。
    """
    import os

    side = Path(overlay_path).resolve()
    paths = {
        "AI_RADAR_ARK_BREAKER_STATE": str(side.with_name(side.stem + "-ark-breaker.json")),
    }
    for key, value in paths.items():
        os.environ[key] = value
    return paths


# **必须是 v2，不是 v1**（2026-09-11 review-gate 的 C2）：v1 的 `EnrichResult` 只有
# `title_zh/summary_zh/why_recommend/tags/raw`，**不产 `primary_category`**，而版面构成指标按类别算。
# 第一版导入 v1 ⇒ 781 条输出含类别的 0 条、类别轴整条 no-op、新增候选 `category=''`
# 因而豁免了生产的 `paper=0.95` 降权并绕过配额封顶，而它**不报错**。
from airadar.enrich.runner_v2 import _output_from_result  # noqa: E402
from airadar.enrich.runner_v2 import _provider_from_env as enrich_provider  # noqa: E402
from airadar.prefilter.runner import _provider_from_env as prefilter_provider  # noqa: E402
from airadar.provider.base import ProviderItem  # noqa: E402
from airadar.scorer.runner import _provider_from_env as scorer_provider  # noqa: E402

DIMS = ("relevance", "density", "recency", "authority", "engineering", "significance")


def ensure_schema(out: sqlite3.Connection) -> None:
    out.execute(
        "CREATE TABLE IF NOT EXISTS rejudged ("
        " item_id TEXT PRIMARY KEY,"
        " is_ai_related INTEGER, confidence REAL,"
        " numeric_json TEXT, enrich_output_json TEXT,"
        " error TEXT)"
    )
    out.commit()



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
    ap.add_argument("--db", default=str(REPO / "data" / "radar.db"))
    ap.add_argument("--overlay", required=True)
    ap.add_argument("--workers", type=int, default=4,
                    help="并发上限。默认 4，理由见模块 docstring——它由「别把生产的 ARK 熔断器打开」定。")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    for key, value in _isolate_side_effects(args.overlay).items():
        print(f"隔离：{key} = {value}")

    _print_db_identity(args.db)
    src = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    src.execute("PRAGMA busy_timeout=120000")
    out = sqlite3.connect(args.overlay, check_same_thread=False)
    out.row_factory = sqlite3.Row
    ensure_schema(out)

    done = {r[0] for r in out.execute("SELECT item_id FROM rejudged WHERE error IS NULL")}
    overlay_rows = out.execute(
        "SELECT item_id, source_id, content_text, old_len, new_len FROM body_overlay"
    ).fetchall()
    todo = [r for r in overlay_rows if r["item_id"] not in done]
    if args.limit:
        todo = todo[: args.limit]
    print(f"覆盖层里有新正文的条目 {len(overlay_rows)}，已重判 {len(done)}，本次跑 {len(todo)}")
    if not todo:
        return

    meta = {}
    for row in src.execute(
        "SELECT i.id, i.title, i.url, i.source_id, i.author, i.published_at, s.tier "
        "FROM items i JOIN sources s ON s.id = i.source_id"
    ):
        meta[row["id"]] = row

    pf, sc, en = prefilter_provider(), scorer_provider(), enrich_provider()
    print(f"provider: prefilter={pf.model_id} scoring={sc.model_id} enrich={en.model_id}")

    lock = threading.Lock()
    stats = {"ok": 0, "admitted": 0, "rejected": 0, "err": 0}

    def work(row: sqlite3.Row) -> tuple[str, dict]:
        m = meta.get(row["item_id"])
        if m is None:
            return row["item_id"], {"error": "item 不在生产库里"}
        item = ProviderItem(
            id=m["id"], title=m["title"], url=m["url"], source_id=m["source_id"],
            # ⚠️ **这里原本硬写 tier="a"，理由是"只动正文一个自变量"。那个理由被证伪了**
            # （2026-09-11 review-gate）：tier **进 prefilter 与 scorer 的 prompt**
            # （两者都含 `Source tier: {{ item.tier }}`；`enrich/prompts_v2.py` 不含它——
            # 复核订正了我原先写的"三个"），而生产只有 `T1/T1.5/T2` ⇒ `"a"` 是 OOD token；
            # 更要紧的是模拟器把重判分与**生产分**放进同一个排序键，所以恒定偏移**不抵消**、
            # 只是整体平移覆盖层条目相对于其余条目的位次。改用该条目真实的 tier。
            tier=m["tier"] or "T2", author=m["author"], published_at=m["published_at"],
            content_text=row["content_text"],
        )
        rec: dict = {}
        try:
            verdict = pf.is_ai_related(item)
            rec["is_ai_related"] = int(verdict.is_ai_related)
            rec["confidence"] = verdict.confidence
            if verdict.is_ai_related:
                s = sc.score_5d(item)
                rec["numeric_json"] = json.dumps(
                    {d: getattr(s, d, None) for d in DIMS}, ensure_ascii=False
                )
                e = en.enrich(item)
                rec["enrich_output_json"] = json.dumps(
                    _output_from_result(e, item), ensure_ascii=False
                )
        except Exception as exc:  # noqa: BLE001
            rec["error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
        return row["item_id"], rec

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(work, r) for r in todo]
        for i, fut in enumerate(as_completed(futures), 1):
            item_id, rec = fut.result()
            with lock:
                out.execute(
                    "INSERT OR REPLACE INTO rejudged VALUES (?,?,?,?,?,?)",
                    (item_id, rec.get("is_ai_related"), rec.get("confidence"),
                     rec.get("numeric_json"), rec.get("enrich_output_json"), rec.get("error")),
                )
                if rec.get("error"):
                    stats["err"] += 1
                else:
                    stats["ok"] += 1
                    stats["admitted" if rec.get("is_ai_related") else "rejected"] += 1
                if i % 50 == 0:
                    out.commit()
                    print(f"  {i}/{len(todo)}  判 AI {stats['admitted']} · 判非 AI "
                          f"{stats['rejected']} · 出错 {stats['err']}", flush=True)
    out.commit()
    print(f"\n完成：判 AI {stats['admitted']} · 判非 AI {stats['rejected']} · 出错 {stats['err']}")


if __name__ == "__main__":
    main()
