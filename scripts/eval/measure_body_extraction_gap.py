"""量「我方存的正文长度」让我方在打分与准入上损失多少。只读，零 LLM 调用，不出网。

两把尺子，后者是前者的受控版本：

**A. 分档对照**（AIHOT 精选 ∩ 我方已打分）——拿 AIHOT 自己的分数当"真实质量"的控制变量：
它选中了这些条目，且它读的是**真文章**，所以它的分数跨档应当持平；我方分数若随
**我方存的**正文长度滑动，那个落差就是测量假象，不是质量差异。

**B. 同篇配对**（同一个归一化 URL 被两个源各收一次）——聚合源给列表 stub、原站给全文。
同标题、同发布时间，它比 A 更接近因果，但**它不是干净的因果配对**，见下面限制 2。

四条读它的限制，**第 2 条最要紧、且是本脚本第一版写错过的地方**：

1. **两把尺子都只测假阴性方向**（我方把好条目打低了）。放开回抓会放进多少本该被拒的条目，
   这里测不出——那要在随机样本上回抓重判，见 `docs/issues/aihot-fit-eval.md` 同名节。
2. **B 没有消掉信源混淆。** 本脚本初版的 docstring 写着「唯一差别是我方有没有存正文」，
   **那句是错的**（2026-09-11 由外部决策评审报出）：同一 URL 的两行按
   `dedup.py` 的 `UNIQUE (source_id, content_hash)` **必然来自不同 `source_id`**，
   而打分 prompt 明确读 `source tier` 与 `source id`（`scorer/prompts.py`）。
   ⇒ B 的分差里掺着信源效应，它是**强关联证据，不是因果配对**。
   要真正消掉它，得让**同一个 source_id** 的同一条目在两种正文下各打一次分。
3. **B 的 stub 侧集中在 `hn_ai` / `buzzing_hn`**；对 X 类条目 B 没有样本。
4. 加权分按 `curator.weights.DEFAULT_WEIGHTS` 现算，**不是**库里存的 `weighted_score`
   ——后者掺着历次权重世代。权重一改，本脚本的读数随之变，这是有意的。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "src"))

import measure_curated_composition as _comp  # noqa: E402

from airadar.curator.weights import DEFAULT_WEIGHTS  # noqa: E402
from airadar.eval.aihot_fit.build import normalize_url  # noqa: E402

# 生产准入闸。`admin/thresholds.py` 可改它，这里只作报告用的参照线。
GATE = 6.5
BUCKETS = ((0, 100), (100, 300), (300, 1000), (1000, 10**9))


def weighted(numeric: dict) -> float:
    w = DEFAULT_WEIGHTS
    return (
        w.relevance * numeric.get("relevance", 0.0)
        + w.density * numeric.get("density", 0.0)
        + w.recency * numeric.get("recency", 0.0)
        + w.authority * numeric.get("authority", 0.0)
        + w.engineering * numeric.get("engineering", 0.0)
        + w.significance * numeric.get("significance", 0.0)
    )


def _load(conn: sqlite3.Connection) -> tuple[dict[str, list[sqlite3.Row]], dict[str, dict]]:
    by_url: dict[str, list[sqlite3.Row]] = {}
    for row in conn.execute("SELECT id, source_id, url, title, content_text FROM items"):
        by_url.setdefault(normalize_url(row["url"])[0], []).append(row)
    # 同一 item 多行打分时取最新（`ORDER BY id` 让后写的覆盖先写的）
    scores: dict[str, dict] = {}
    for row in conn.execute(
        "SELECT item_id, numeric_json FROM item_evaluations "
        "WHERE stage='scoring' AND error IS NULL ORDER BY id"
    ):
        try:
            scores[row["item_id"]] = json.loads(row["numeric_json"] or "{}")
        except ValueError:
            continue
    return by_url, scores


def bucketed(by_url, scores) -> None:
    dims = ("relevance", "density", "recency", "authority", "engineering", "significance")
    groups: dict[tuple[int, int], list[tuple[dict, float | None]]] = {b: [] for b in BUCKETS}
    for ref in _comp.load_aihot().values():
        if not ref["selected"] or not ref.get("url"):
            continue
        cands = by_url.get(normalize_url(ref["url"])[0])
        if not cands:
            continue
        numeric = scores.get(cands[0]["id"])
        if not numeric:
            continue
        length = len((cands[0]["content_text"] or "").strip())
        for lo, hi in BUCKETS:
            if lo <= length < hi:
                groups[(lo, hi)].append((numeric, ref.get("score")))
                break

    total = sum(len(v) for v in groups.values())
    print(f"\n=== A. 分档对照（AIHOT 精选 ∩ 我方已打分，n={total}）===")
    print("AIHOT 的分数是控制变量：它跨档持平而我方滑动 ⇒ 落差是测量假象，不是质量差异。\n")
    head = f"{'我方正文':<11}{'n':>4}  " + "".join(f"{d[:7]:>9}" for d in dims)
    print(head + f"{'加权分':>9}{'过闸%':>8}{'AIHOT分':>9}")
    for (lo, hi), rows in groups.items():
        if not rows:
            continue
        label = f"<{hi}" if lo == 0 else (f">={lo}" if hi > 10**8 else f"{lo}-{hi}")
        line = f"{label:<11}{len(rows):>4}  "
        for d in dims:
            vals = [float(n[d]) for n, _ in rows if isinstance(n.get(d), (int, float))]
            line += f"{statistics.mean(vals):>9.2f}" if vals else f"{'n/a':>9}"
        ws = [weighted(n) for n, _ in rows]
        aihot = [float(a) for _, a in rows if isinstance(a, (int, float))]
        line += f"{statistics.mean(ws):>9.2f}"
        line += f"{sum(1 for x in ws if x >= GATE) / len(ws) * 100:>7.1f}%"
        line += f"{statistics.mean(aihot):>9.1f}" if aihot else f"{'n/a':>9}"
        print(line)


def paired(by_url, scores, *, min_ratio: float, min_body: int) -> None:
    pairs = []
    for cands in by_url.values():
        scored = [c for c in cands if c["id"] in scores]
        if len(scored) < 2:
            continue
        scored.sort(key=lambda r: len((r["content_text"] or "").strip()))
        lo, hi = scored[0], scored[-1]
        len_lo = len((lo["content_text"] or "").strip())
        len_hi = len((hi["content_text"] or "").strip())
        if len_hi < min_body or len_hi < min_ratio * max(len_lo, 1):
            continue
        pairs.append((len_lo, len_hi, weighted(scores[lo["id"]]), weighted(scores[hi["id"]]),
                      lo["source_id"], hi["source_id"], hi["title"]))

    print(f"\n=== B. 同篇配对（同一 URL 两个源，长度差 ≥{min_ratio:g}x 且全文档 ≥{min_body} 字）===")
    if not pairs:
        print("没有配对样本。")
        return
    deltas = [hi - lo for _, _, lo, hi, *_ in pairs]
    up = sum(1 for d in deltas if d > 0)
    down = sum(1 for d in deltas if d < 0)
    print(f"n={len(pairs)} 对。同标题同发布时间，但**两行必然来自不同 source_id**，")
    print("而打分 prompt 读 source tier / source id ⇒ 分差里掺着信源效应，这不是因果配对。\n")
    print(f"  加权分差（全文 − stub）：均值 {statistics.mean(deltas):+.2f}  中位 {statistics.median(deltas):+.2f}")
    print(f"  方向：全文更高 {up}/{len(pairs)}  ·  stub 更高 {down}  ·  持平 {len(pairs) - up - down}")
    g_lo = sum(1 for _, _, lo, _, *_ in pairs if lo >= GATE)
    g_hi = sum(1 for _, _, _, hi, *_ in pairs if hi >= GATE)
    print(f"  过 {GATE} 闸：stub 档 {g_lo}/{len(pairs)} = {g_lo / len(pairs) * 100:.1f}%"
          f"  →  全文档 {g_hi}/{len(pairs)} = {g_hi / len(pairs) * 100:.1f}%")
    print("\n  增益最大的 10 对：")
    for len_lo, len_hi, s_lo, s_hi, src_lo, src_hi, title in sorted(
        pairs, key=lambda p: p[3] - p[2], reverse=True
    )[:10]:
        print(f"    {s_lo:5.2f}({len_lo:>5}字 {src_lo[:16]:<16}) → {s_hi:5.2f}"
              f"({len_hi:>6}字 {src_hi[:16]:<16}) {title[:44]}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(REPO / "data" / "radar.db"))
    ap.add_argument("--min-ratio", type=float, default=3.0,
                    help="B 尺：全文档比 stub 档长多少倍才算一对（默认 3）")
    ap.add_argument("--min-body", type=int, default=300,
                    help="B 尺：全文档至少多少字（默认 300）")
    args = ap.parse_args()

    # `mode=ro` 不带 `immutable=1`：有 pipeline 在写时后者会抛 database is malformed。
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=120000")
    by_url, scores = _load(conn)
    print(f"权重：{DEFAULT_WEIGHTS}")
    print(f"items 归一化 URL {len(by_url)} 个，已打分条目 {len(scores)} 个")
    bucketed(by_url, scores)
    paired(by_url, scores, min_ratio=args.min_ratio, min_body=args.min_body)


if __name__ == "__main__":
    main()
