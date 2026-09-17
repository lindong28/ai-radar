"""Isolated current-source replay; never open the production DB or provider ledger.

The caller injects ``chat(stage=..., prompt=..., request=...)`` and owns durable
attempt accounting at the actual transport boundary. This module performs one
call per stage, with no retry, heuristic fallback, endpoint fallback or key reads.
``project_pool`` consumes a complete fixed pool, not reference-selected cases.
Its score/text surface is /timeline; featured membership is the archive union
after one fixed-time curation, with an explicitly supplied initial member set.
"""

from __future__ import annotations

import hashlib
import math
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from airadar.curator.dedup import deduplicate_candidates
from airadar.curator.score import ScoredCandidate, weighted_score
from airadar.curator.select import (
    DEFAULT_FRESHNESS_FLOOR,
    DEFAULT_FRESHNESS_QUOTA,
    DEFAULT_FRESHNESS_WINDOW_HOURS,
    DEFAULT_LIMIT,
    DEFAULT_SOURCE_QUOTA,
    DEFAULT_THRESHOLD,
    _calibrate_selected_scores,
    _fill,
    _parse_utc,
    _shanghai_date,
    parse_source_quota,
    ranking_key,
)
from airadar.curator.weights import DEFAULT_WEIGHTS, weights_from_mapping
from airadar.enrich.normalizers.production_enrich_provider_output_v2 import normalize
from airadar.enrich.prompts_v2 import render_enrich_prompt
from airadar.enrich.schema_v2 import EnrichOutputV2
from airadar.prefilter.prompts import render_prefilter_prompt
from airadar.prefilter.runner import PrefilterNumeric
from airadar.presentation.summary import _visible_reason_from_payload, item_summary
from airadar.provider.base import ProviderItem
from airadar.scorer.prompts import render_scoring_prompt
from airadar.scorer.schema import ScoringNumeric

TARGETS = ("news-admission", "visible-score", "content-enrichment", "featured-members")
_STAGES = {
    "prefilter": (render_prefilter_prompt, "deepseek-v4-flash", "AI_RADAR_DEEPSEEK_PREFILTER_MODEL", 0.0, 200),
    "score": (render_scoring_prompt, "deepseek-v4-flash", "AI_RADAR_DEEPSEEK_SCORER_MODEL", 0.0, 600),
    "enrich": (render_enrich_prompt, "deepseek-v4-pro", "AI_RADAR_DEEPSEEK_ENRICH_MODEL", 0.2, None),
}


def _item(raw: dict[str, Any]) -> ProviderItem:
    """Allowlist model input; case IDs, labels and reference fields never enter prompts."""
    return ProviderItem(
        id=str(raw.get("item_id") or raw.get("id") or raw["case_id"]),
        title=raw["title"], url=raw["url"], source_id=raw["source_id"],
        tier=raw["tier"], author=raw.get("author"),
        published_at=raw["published_at"], content_text=raw["content_text"],
    )


def _request(stage: str, config: dict[str, Any]) -> dict[str, Any]:
    _, default_model, model_env, temperature, max_tokens = _STAGES[stage]
    selector_env, selector_default = {
        "prefilter": ("AI_RADAR_PREFILTER", "deepseek_v32"),
        "score": ("AI_RADAR_SCORER", "deepseek_v4_flash"),
        "enrich": ("AI_RADAR_ENRICHER", "deepseek_v4_pro"),
    }[stage]
    selector = os.environ.get(selector_env, selector_default)
    supported = {"prefilter": {"deepseek_v32"},
                 "score": {"deepseek_v4_flash", "deepseek_v4_pro"},
                 "enrich": {"deepseek_v4_flash", "deepseek_v4_pro"}}[stage]
    if selector not in supported:
        raise ValueError(f"unsupported production provider selector: {selector_env}")
    if stage != "prefilter":
        default_model = selector.replace("_", "-")
    model = config.get("models", {}).get(stage) or os.environ.get(model_env, default_model)
    if stage == "enrich":
        temperature = float(os.environ.get("AI_RADAR_ENRICH_TEMPERATURE", temperature))
    request = {"model": model, "temperature": temperature}
    if max_tokens is not None:
        request["max_tokens"] = max_tokens
    return request


def _run_stage(stage: str, item: ProviderItem, config: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"status": "error", "output": None, "raw": None, "usage": None, "model": None}
    try:
        prompt = _STAGES[stage][0](item)
        response = config["chat"](stage=stage, prompt=prompt, request=_request(stage, config))
        result.update({key: response.get(key) for key in ("raw", "usage", "model", "provider", "requested_model", "attempt_id")})
        payload = response["json"]
        if result["raw"] is None:
            result["raw"] = payload
        if stage == "prefilter":
            # Same conversion as DeepSeekV32Prefilter, then production runner validation.
            output = PrefilterNumeric.model_validate({
                "is_ai_related": bool(payload.get("is_ai_related")),
                "confidence": max(0.0, min(1.0, float(payload.get("confidence", 0.0)))),
            }).model_dump()
        elif stage == "score":
            output = ScoringNumeric.model_validate({
                **{key: float(payload.get(key, 0.0)) for key in
                   ("relevance", "density", "recency", "authority", "engineering")},
                "significance": None if payload.get("significance") is None else float(payload["significance"]),
                "reasoning": str(payload.get("reasoning", ""))[:200],
                "topics": [str(tag) for tag in payload.get("topics", [])][:4],
            }).model_dump()
        else:
            output = EnrichOutputV2.model_validate(normalize(payload, item=item)).model_dump()
        result.update(status="ok", output=output)
    except Exception as exc:
        # Transport logging owns the full exception, which may contain credentials.
        result["error"] = type(exc).__name__
        if isinstance(getattr(exc, "attempt_id", None), str):
            result["attempt_id"] = exc.attempt_id
    return result


def _presentation(raw: dict[str, Any], enriched: dict[str, Any]) -> dict[str, Any]:
    item = _item(raw)
    row = {
        **vars(item), "source_name": raw.get("source_name", item.source_id),
        "source_kind": raw.get("source_kind", "feed"),
        "fetched_at": raw.get("fetched_at", item.published_at),
    }
    visible = item_summary(row, include_related=False, enrichment=EnrichOutputV2.model_validate(enriched))
    return {
        "category": visible["primary_category"], "tags": visible["topic_tags"],
        "title": visible["title_zh"], "summary": visible["summary_zh"],
        "reason": visible["why_recommend"],
    }


def predict_one(target: str, input: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Predict one stage/raw item. Pool-dependent targets remain pending_pool.

    Stage aliases allow callers to cache one prefilter/score/enrich result and
    share it across all four objects. A returned error is never an empty success.
    """
    if target == "featured-members":
        raise ValueError("featured-members requires project_pool with fixed predictions")
    aliases = {"news-admission": "prefilter", "visible-score": "score", "content-enrichment": "enrich"}
    stage = aliases.get(target, target)
    if stage not in _STAGES:
        raise ValueError(f"unknown target: {target}")
    item = _item(input)
    stages = {stage: _run_stage(stage, item, config)}
    if target == "news-admission" and stages[stage]["status"] == "ok" and stages[stage]["output"]["is_ai_related"]:
        stages["score"] = _run_stage("score", item, config)
    success = all(value["status"] == "ok" for value in stages.values())
    output = stages[stage]["output"] if success else None
    if success and stage == "enrich":
        output = {**_presentation(input, output), "reason_surface": "enrich-before-curation"}
    return {
        "case_id": input.get("case_id", item.id),
        "status": ("pending_pool" if target in TARGETS else "ok") if success else "error",
        "output": output, "stage_results": stages,
    }


def _stage_output(stages: dict[str, Any], stage: str) -> dict[str, Any] | None:
    result = stages.get(stage)
    return result.get("output") if result and result.get("status") == "ok" else None


def _select(candidates: list[ScoredCandidate], config: dict[str, Any]) -> list[ScoredCandidate]:
    """Pure transcription of curate's pool setup; ranking/fill/calibration are production functions."""
    now = datetime.fromisoformat(config["now"].replace("Z", "+00:00"))
    if now.tzinfo is None:
        raise ValueError("now must include a timezone")
    selection = config.get("selection", {})
    candidates = deduplicate_candidates(candidates)
    filtered = sorted(
        (candidate for candidate in candidates if candidate.weighted_score >= selection.get("threshold", DEFAULT_THRESHOLD)),
        key=ranking_key,
    )
    cutoff = now.astimezone(UTC) - timedelta(hours=selection.get("freshness_window_hours", DEFAULT_FRESHNESS_WINDOW_HOURS))
    fresh_pool = [candidate for candidate in candidates
                  if candidate.weighted_score >= selection.get("freshness_floor", DEFAULT_FRESHNESS_FLOOR)
                  and (published := _parse_utc(candidate.published_at)) and published >= cutoff
                  and _shanghai_date(candidate.published_at)]
    latest_date = max((_shanghai_date(candidate.published_at) for candidate in fresh_pool), default=None)
    fresh = sorted((candidate for candidate in fresh_pool if _shanghai_date(candidate.published_at) == latest_date), key=ranking_key)
    quota = parse_source_quota(selection["source_quota"]) if "source_quota" in selection else DEFAULT_SOURCE_QUOTA
    return _calibrate_selected_scores(_fill(
        fresh, filtered, selection.get("limit", DEFAULT_LIMIT),
        selection.get("freshness_quota", DEFAULT_FRESHNESS_QUOTA), quota,
    ))


def project_pool(raw_predictions: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    """Project frozen {input, stage_results} rows to /timeline and archive membership.

    Required config: now, pool_complete=True, archive_initial (list of item IDs,
    explicitly [] for empty-state replay). References are not accepted here.
    Missing/failed pool scores or categories invalidate pool-dependent results.
    """
    if config.get("pool_complete") is not True:
        raise ValueError("complete prediction pool is required")
    initial = config["archive_initial"]
    if not isinstance(initial, list) or not all(isinstance(value, str) for value in initial):
        raise ValueError("archive_initial must be explicit item ID list")
    weights = weights_from_mapping(config["weights"]) if "weights" in config else DEFAULT_WEIGHTS
    candidates = []
    representatives: dict[tuple[str, str], tuple[str, str, str]] = {}
    prepared = []
    pool_errors = []
    seen_ids = set()
    for row in raw_predictions:
        raw, stages = row["input"], row["stage_results"]
        item = _item(raw)
        if item.id in seen_ids:
            raise ValueError("duplicate item ID in prediction pool")
        seen_ids.add(item.id)
        # Missing metadata cannot silently impersonate a visible production source.
        enabled, kind = raw["source_enabled"], raw["source_kind"] or "feed"
        visible_source = bool(enabled) and kind != "wechat"
        key = (item.source_id, item.url.lower().rstrip("/"))
        order = (item.published_at, raw["fetched_at"], item.id)
        representatives[key] = max(representatives.get(key, order), order)
        pre, score, enrich = (_stage_output(stages, stage) for stage in ("prefilter", "score", "enrich"))
        pre_ok = pre is not None
        needs_score = pre_ok and pre["is_ai_related"]
        if visible_source and (not pre_ok or (needs_score and (score is None or enrich is None))):
            pool_errors.append(item.id)
        raw_weighted = weighted_score(score, weights, item.tier) if score else None
        if visible_source and needs_score and score is not None:
            candidates.append(ScoredCandidate(
                eval_id=len(candidates), item_id=item.id, content_hash=raw["content_hash"],
                url=item.url, published_at=item.published_at, weighted_score=raw_weighted,
                reason={"scores": score}, source_id=item.source_id, kind=kind,
                primary_category=enrich["primary_category"] if enrich else "",
            ))
        prepared.append((raw, stages, item, key, order, visible_source, pre, score, enrich, raw_weighted))
    selected = {candidate.item_id: candidate for candidate in _select(candidates, config)}
    archive = set(initial) | set(selected)
    output = []
    for raw, stages, item, key, order, visible_source, pre, score, enrich, raw_weighted in prepared:
        representative = representatives[key] == order
        admission_known = not visible_source or not representative or (pre is not None and (not pre["is_ai_related"] or score is not None))
        member = bool(visible_source and representative and pre and pre["is_ai_related"] and score and score["relevance"] >= 6.5)
        chosen = selected.get(item.id)
        values: dict[str, Any] = {"member": member if admission_known else None}
        values["featured"] = bool(visible_source and representative and item.id in archive) if not pool_errors else None
        display_weighted = chosen.weighted_score if chosen else raw_weighted
        values["score"] = math.floor(display_weighted * 10 + 0.5) if display_weighted is not None and not pool_errors else None
        if enrich:
            values.update(_presentation(raw, enrich))
            if chosen:
                values["reason"] = _visible_reason_from_payload(chosen.reason, {"source_name": raw.get("source_name", item.source_id)})
        target_status = {
            "news-admission": "ok" if admission_known else "error",
            "visible-score": "ok" if values["score"] is not None else "error",
            "content-enrichment": "ok" if enrich is not None and not pool_errors else "error",
            "featured-members": "ok" if not pool_errors else "error",
        }
        output.append({
            "case_id": raw.get("case_id", item.id), "item_id": item.id,
            "status": "ok" if all(value == "ok" for value in target_status.values()) else "error",
            "target_status": target_status, "output": values, "stage_results": stages,
            "pool_error_item_ids": list(pool_errors),
        })
    return output


def identity(config: dict[str, Any]) -> dict[str, Any]:
    """Source/model provenance only; never serialize credentials or the chat callable."""
    root = Path(__file__).resolve().parents[2]
    paths = [
        "evals/_shared/inference.py", "src/airadar/prefilter/prompts.py",
        "src/airadar/prefilter/runner.py", "src/airadar/provider/base.py",
        "src/airadar/scorer/prompts.py", "src/airadar/scorer/schema.py",
        "src/airadar/enrich/prompts_v2.py", "src/airadar/enrich/schema_v2.py",
        "src/airadar/enrich/normalizers/production_enrich_provider_output_v2.py",
        "src/airadar/enrich/normalizers/production_enrich_provider_output_v1.py",
        "src/airadar/presentation/summary.py", "src/airadar/enrich/classification.py",
        "src/airadar/curator/select.py", "src/airadar/curator/score.py",
        "src/airadar/curator/weights.py", "src/airadar/curator/dedup.py",
        "src/airadar/web/routes/timeline.py", "src/airadar/web/routes/categories.py",
        "web/static/app.js",
    ]
    return {
        "baseline": "isolated-current-source-replay", "surface": "timeline-and-archive-membership",
        "source_sha256": {path: hashlib.sha256((root / path).read_bytes()).hexdigest() for path in paths},
        "requests": {stage: _request(stage, config) for stage in _STAGES},
        "selection": config.get("selection", {}), "weights": config.get("weights", DEFAULT_WEIGHTS.as_record()),
        "now": config.get("now"), "archive_initial": config.get("archive_initial"),
        "retry_policy": "caller-owned; one invocation per stage",
    }
