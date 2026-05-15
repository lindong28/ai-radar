from __future__ import annotations

import os

from ..enrich.prompts import render_enrich_prompt
from ..scorer.prompts import render_scoring_prompt
from .base import EnrichResult, ProviderItem, ScoringResult
from .deepseek_chat import chat_json
from .heuristics import heuristic_score


class DeepSeekV4ProScorer:
    model_id = "deepseek-v4-pro"

    def smoke_test(self) -> str:
        return "ok" if os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("ARK_API_KEY") else "ok (offline fallback)"

    def score_5d(self, item: ProviderItem) -> ScoringResult:
        if not (os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("ARK_API_KEY")) or os.environ.get(
            "AI_RADAR_FORCE_HEURISTIC"
        ):
            return heuristic_score(item)
        prompt = render_scoring_prompt(item)
        result = chat_json(
            system=prompt["system"],
            user=prompt["user"],
            default_model=self.model_id,
            model_env="AI_RADAR_DEEPSEEK_SCORER_MODEL",
            ark_model_env="AI_RADAR_ARK_SCORER_MODEL",
            temperature=0.0,
            max_tokens=600,
        )
        payload = result.json
        return ScoringResult(
            relevance=float(payload.get("relevance", 0.0)),
            density=float(payload.get("density", 0.0)),
            recency=float(payload.get("recency", 0.0)),
            authority=float(payload.get("authority", 0.0)),
            engineering=float(payload.get("engineering", 0.0)),
            reasoning=str(payload.get("reasoning", "")),
            topics=tuple(str(tag) for tag in payload.get("topics", [])),
            raw={"provider": result.provider, "model": result.model, "json": payload},
        )


class DeepSeekV4ProEnricher:
    model_id = "deepseek-v4-pro"

    def smoke_test(self) -> str:
        return "ok" if os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("ARK_API_KEY") else "skipped (missing key)"

    def enrich(self, item: ProviderItem) -> EnrichResult:
        prompt = render_enrich_prompt(item)
        result = chat_json(
            system=prompt["system"],
            user=prompt["user"],
            default_model=self.model_id,
            model_env="AI_RADAR_DEEPSEEK_ENRICH_MODEL",
            ark_model_env="AI_RADAR_ARK_ENRICH_MODEL",
            temperature=float(os.environ.get("AI_RADAR_ENRICH_TEMPERATURE", "0.2")),
        )
        payload = result.json
        return EnrichResult(
            title_zh=str(payload.get("title_zh", "")),
            summary_zh=str(payload.get("summary_zh", "")),
            why_recommend=str(payload.get("why_recommend", "")),
            tags=tuple(str(tag) for tag in payload.get("tags", [])),
            raw={"provider": result.provider, "model": result.model, "json": payload},
        )
