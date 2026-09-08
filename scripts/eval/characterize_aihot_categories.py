#!/usr/bin/env python3
"""刻画 AIHOT 的类别决策函数，并给下一版 enrich prompt 定对照线。

零 LLM 调用、全只读；语料在 gitignored 的 data/eval-fit/ 下，脚本本身入库。产出三个数（同一批配对条目上）：多数类下界、我方 LLM 现状、
只读标题的确定性规则。规则不是要上线的东西——它是把「AIHOT 凭什么这么分」写成
一个以输入为自变量的规则，并给出一条 prompt 改动必须超过的线。

    uv run python scripts/eval/characterize_aihot_categories.py
"""
from __future__ import annotations

import collections
import json
import math
import re
import sqlite3
from pathlib import Path

CATS = ["tutorial", "model", "product", "industry", "paper"]
REPO = Path(__file__).resolve().parents[2]
QUESTIONS = REPO / "data/eval-fit/evalset-staging/aihot-fit-v1/questions.jsonl"
DB_URI = f"file:{REPO / 'data/radar.db'}?mode=ro&immutable=1"

# 只看标题。加正文没有让这条规则更准，而生产分类器读了正文仍更差——见 ADR-499e。
PAPER = re.compile(r"论文|研究报告|arxiv|预印本|基准测试|连接组|提出了一种")
MONEY = re.compile(
    r"亿美元|亿元|万美元|万元|ipo|上市|收购|并购|融资|估值|财报|营收|募资|发行价"
    r"|裁员|诉讼|起诉|反垄断|监管|合规|禁令|出售|入股|投资|市值|股价|中签",
    re.I,
)
MODEL = re.compile(
    r"发布.{0,12}模型|开源.{0,8}模型|模型.{0,6}(发布|开源|上线|登顶|开放)"
    r"|登顶|刷新记录|刷新纪录|得分|智能指数|arena|sota",
    re.I,
)
PRODUCT = re.compile(r"推出|上线|新增|新功能|功能|支持|开放|可用|正式发布|预览|beta", re.I)


def rule(title: str) -> str:
    """AIHOT 的判据按证据强度排序，不是按漏斗。tutorial 是 residual —— 它在 AIHOT
    那边也是最大的一类（35.7%），大的兜底类本身没错，错的是上游漏斗的宽度。"""
    if PAPER.search(title):
        return "paper"
    if MONEY.search(title):
        return "industry"
    if MODEL.search(title):
        return "model"
    if PRODUCT.search(title):
        return "product"
    return "tutorial"


def _our_category(con: sqlite3.Connection, item_id: str) -> str | None:
    row = con.execute(
        "SELECT output_json FROM item_evaluations"
        " WHERE item_id=? AND stage='enrich' AND error IS NULL ORDER BY id DESC LIMIT 1",
        (item_id,),
    ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row[0]).get("primary_category")
    except (ValueError, TypeError):
        return None


def _tv(pred: list[str], gold: list[str]) -> float:
    n = len(gold)
    p, g = collections.Counter(pred), collections.Counter(gold)
    return sum(abs(p[c] / n - g[c] / n) for c in CATS) / 2


def main() -> None:
    rows = [json.loads(line) for line in QUESTIONS.open()]
    labelled = [r for r in rows if r["reference"].get("primary_category") in CATS]

    # --- 各类最具区分度的标题词（对数几率）---
    def tokens(s: str) -> list[str]:
        s = s.lower()
        return re.findall(r"[a-z][a-z0-9\-.]{2,}", s) + re.findall(r"[一-鿿]{2,4}", s)

    per_cat: dict[str, collections.Counter[str]] = {c: collections.Counter() for c in CATS}
    for r in labelled:
        title = r["reference"].get("title") or r["input"].get("title") or ""
        per_cat[r["reference"]["primary_category"]].update(set(tokens(title)))
    overall: collections.Counter[str] = collections.Counter()
    for c in CATS:
        overall.update(per_cat[c])
    sizes = {c: sum(1 for r in labelled if r["reference"]["primary_category"] == c) for c in CATS}
    total = sum(sizes.values())
    print(f"AIHOT 标注语料 n={total}  " + " ".join(f"{c}={sizes[c]}" for c in CATS))
    for c in CATS:
        scored = [
            (
                math.log(((k + 0.5) / (sizes[c] + 1)) / ((overall[w] - k + 0.5) / (total - sizes[c] + 1))),
                w,
            )
            for w, k in per_cat[c].items()
            if overall[w] >= 12
        ]
        scored.sort(reverse=True)
        print(f"  {c:<9} " + " · ".join(w for _, w in scored[:14]))

    # --- 三条线，必须在同一批条目上比 ---
    con = sqlite3.connect(DB_URI, uri=True)
    paired = []
    for r in labelled:
        ours = _our_category(con, r["input"]["item_id"])
        if ours in CATS:
            paired.append(
                (r["reference"].get("title") or r["input"].get("title") or "", r["reference"]["primary_category"], ours)
            )
    n = len(paired)
    gold = [g for _, g, _ in paired]
    majority = collections.Counter(gold).most_common(1)[0][0]
    lines = {
        f"多数类下界（全判 {majority}）": [majority] * n,
        "我方 LLM enrich（现状）": [o for _, _, o in paired],
        "标题关键词确定性规则": [rule(t) for t, _, _ in paired],
    }
    print(f"\n同一批配对样本 n={n}\n{'口径':<30}{'一致率':>9}{'TV':>9}")
    for name, pred in lines.items():
        print(f"{name:<30}{sum(a == b for a, b in zip(pred, gold)) / n:>9.3f}{_tv(pred, gold):>9.3f}")


if __name__ == "__main__":
    main()
