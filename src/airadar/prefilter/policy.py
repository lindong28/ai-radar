"""C11 admission policy shared by the database runner and offline evaluation."""
from __future__ import annotations

import re

POLICY = "hn100-standalone-body-v1"


def context_flags(raw: dict) -> dict[str, bool]:
    extra = raw.get("extra") or {}
    refs = extra.get("referenced_tweets") or []
    # Raw archives compute the predicate directly; DB ingestion freezes it
    # because publication/fetch times in a deduplicated row can be different batches.
    placeholder = extra.get("title_only_fetch_time_placeholder")
    if not isinstance(placeholder, bool):
        placeholder = (raw["content_text"] == raw["title"] and bool(raw.get("published_at"))
                       and raw["published_at"] == raw.get("fetched_at"))
    return {
        "is_reply": raw.get("source_kind") == "x" and any(
            isinstance(ref, dict) and ref.get("type") == "replied_to" for ref in refs),
        "is_title_only_web": raw.get("source_kind") == "web"
            and raw["source_id"] != "hf_daily_papers"
            and placeholder,
    }


def rejection_reasons(raw: dict) -> list[str]:
    reasons = []
    if raw["source_id"] == "buzzing_hn":
        points = re.search(r"(\d+) HN Points", raw["content_text"][:4000])
        if points is None or int(points[1]) < 100:
            reasons.append("hn_points_missing_or_below_100")
    context = context_flags(raw)
    if context["is_reply"]:
        reasons.append("conversation_reply")
    if context["is_title_only_web"]:
        reasons.append("title_only_web_listing")
    return reasons


def apply_to_output(raw: dict, output: dict) -> dict:
    """Keep the model explanation and decision separate from the final decision."""
    reasons = rejection_reasons(raw)
    model_output = {key: output[key] for key in ("reason", "is_ai_related", "confidence")}
    return {
        **output,
        "is_ai_related": output["is_ai_related"] and not reasons,
        "model_output": model_output,
        "admission_policy": {"name": POLICY, "rejection_reasons": reasons},
    }
