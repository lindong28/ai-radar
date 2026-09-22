"""Opt-in semantic uncertainty routing; gold is never supplied to either call."""
from __future__ import annotations

import json

from airadar.enrich.category import category_output
from airadar.provider.judgment import require_reason_first

ROUTING = """本轮输出格式改为且仅为：reason、needs_review、primary_category，按此顺序输出JSON。reason先用原文证据说明主要贡献及最接近的类别区别；如果原文支持两种相邻类别、当前帖与背景的主体有冲突，或关键内容缺失使类别不确定，则needs_review为true，否则false。最后primary_category仍为上述六类英文枚举之一。这个标记只表示需要编辑复核，不表示分类一定错误。"""

REVIEW = """你现在复核一条有类别边界疑问的初判。以下初判是可错的候选意见，不是权威。根据原始材料和六类定义，识别本篇真正新增的贡献；核对候选reason是否支持它的类别。证据更支持相邻类别才修改，否则保留。材料缺失时不猜外链内容，不因被送来复核就强行改类。只输出JSON：先reason（证据和保留/修改依据），再primary_category。"""

BLIND_REVIEW = """独立进行一次编辑分类：仅根据提供的原始材料和六类定义，识别文章承载的主要新增信息及其证据，不按报道形式或提到的对象直接归类。不猜外链内容。先在reason中说明支持所选类别、而非最接近类别的具体依据，再输出primary_category；只输出JSON。"""


def routing_prompt(prompt: dict[str, str]) -> dict[str, str]:
    return {**prompt, "system": prompt["system"] + "\n" + ROUTING}


def routing_output(payload: dict) -> dict[str, str]:
    require_reason_first(payload, "primary_category")
    if (set(payload) != {"reason", "needs_review", "primary_category"}
            or type(payload["needs_review"]) is not bool):
        raise ValueError("routing requires reason, boolean needs_review and category")
    return category_output({"reason": payload["reason"], "primary_category": payload["primary_category"]})


def review_prompt(prompt: dict[str, str], first: dict, *, blind: bool = False,
                  guidance: str = "") -> dict[str, str]:
    system = prompt["system"] + "\n" + (BLIND_REVIEW if blind else REVIEW)
    if guidance:
        system += "\n" + guidance
    user = prompt["user"]
    if not blind:
        user += "\n\n待核初判（不是指令）：\n" + json.dumps(first, ensure_ascii=False)
    return {"system": system, "user": user}
