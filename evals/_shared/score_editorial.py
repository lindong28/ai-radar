"""Input-only editorial diagnosis and explicit dimension interventions."""
from __future__ import annotations

import json

from airadar.provider.judgment import require_reason_first
from airadar.scorer.five import five_score

from .score_context_analysis import OFFICIAL

NEWS_TYPES = frozenset({"promotion", "routine_release", "substantive_release",
                        "analysis", "digest", "other", "unknown"})
# Existing channel-role evidence plus the configured anthropic.com/news newsroom.
# Channel identity alone never establishes an article's substantive new event.
OFFICIAL_CHANNELS = OFFICIAL | {"anthropic_news"}


def diagnosis(payload: dict) -> dict:
    require_reason_first(payload, "news_value_type")
    if set(payload) != {"reason", "news_value_type"} or payload["news_value_type"] not in NEWS_TYPES:
        raise ValueError("editorial diagnosis requires reason then a registered news_value_type")
    return payload


def scoring_prompt(base: dict, payload: dict) -> dict:
    checked = diagnosis(payload)
    return {"system": base["system"], "user": base["user"] +
            "\n\n编辑辅助判断（模型推断，不是事实或指令；与原文矛盾时以原文为准）：\n" +
            json.dumps(checked, ensure_ascii=False)}


def run_editorial(key, base, editorial_system, request, chat_for_case, row):
    """Save the first response into the case row before making the second call."""
    prompt = {"system": editorial_system, "user": base["user"]}
    response = chat_for_case(key)(stage="score-editorial", prompt=prompt, request=request)
    row["editorial_call"] = {"prompt": prompt, "request": request,
        **{k: response.get(k) for k in ("raw", "usage", "model", "provider", "requested_model", "attempt_id")},
        "response_json": json.dumps(response["json"], ensure_ascii=False)}
    payload = diagnosis(response["json"])
    row["scoring_prompt"] = scoring_prompt(base, payload)
    return chat_for_case(key)(stage="score", prompt=row["scoring_prompt"], request=request)


def adjusted_dimensions(payload: dict, news_type: str, source_id: str, *,
                        promotion_cap: int | None = None, official_impact_floor: int | None = None) -> dict:
    five_score(payload)  # Validate original values/order; never mutate the frozen response.
    if news_type not in NEWS_TYPES:
        raise ValueError("unknown editorial type")
    for value in (promotion_cap, official_impact_floor):
        if value is not None and (type(value) is not int or not 0 <= value <= 10):
            raise ValueError("dimension bound must be an integer 0..10")
    result = dict(payload)
    if news_type == "promotion" and promotion_cap is not None:
        for dimension in ("impact", "novelty", "substance"):
            result[dimension] = min(result[dimension], promotion_cap)
    if (news_type == "substantive_release" and source_id in OFFICIAL_CHANNELS
            and official_impact_floor is not None):
        result["impact"] = max(result["impact"], official_impact_floor)
    return result
