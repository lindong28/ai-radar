from __future__ import annotations

import os

from ..enrich.prompts_v2 import render_enrich_prompt
from ..scorer.prompts import render_scoring_prompt
from .base import ProviderItem, ScoringResult
from .base_v2 import EnrichResultV2
from .deepseek_chat import chat_json
from .heuristics import heuristic_score


class DeepSeekV4FlashScorer:
    model_id = "deepseek-v4-flash"

    def smoke_test(self) -> str:
        return "ok" if os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("ARK_API_KEY") else "ok (offline fallback)"

    def score_5d(self, item: ProviderItem) -> ScoringResult:
        if not (os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("ARK_API_KEY")) or os.environ.get(
            "AI_RADAR_FORCE_HEURISTIC"
        ):
            return heuristic_score(item)
        prompt = render_scoring_prompt(item)
        input_char_count = len(prompt["system"]) + len(prompt["user"])
        result = chat_json(
            system=prompt["system"],
            user=prompt["user"],
            default_model=self.model_id,
            model_env="AI_RADAR_DEEPSEEK_SCORER_MODEL",
            ark_model_env="AI_RADAR_ARK_SCORER_MODEL",
            temperature=0.0,
            max_tokens=600,
            stage="score",
            item_id=item.id,
            input_item_count=1,
            input_char_count=input_char_count,
            attribution={"source_id": item.source_id, "url": item.url, "title": item.title},
        )
        payload = result.json
        return ScoringResult(
            relevance=float(payload.get("relevance", 0.0)),
            density=float(payload.get("density", 0.0)),
            recency=float(payload.get("recency", 0.0)),
            authority=float(payload.get("authority", 0.0)),
            engineering=float(payload.get("engineering", 0.0)),
            significance=(None if payload.get("significance") is None else float(payload["significance"])),
            reasoning=str(payload.get("reasoning", "")),
            topics=tuple(str(tag) for tag in payload.get("topics", [])),
            raw={"provider": result.provider, "model": result.model, "json": payload},
        )


class DeepSeekV4FlashEnricherV2:
    model_id = "deepseek-v4-flash"

    def smoke_test(self) -> str:
        return "ok" if os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("ARK_API_KEY") else "skipped (missing key)"

    def enrich(self, item: ProviderItem) -> EnrichResultV2:
        prompt = render_enrich_prompt(item)
        input_char_count = len(prompt["system"]) + len(prompt["user"])
        result = chat_json(
            system=prompt["system"],
            user=prompt["user"],
            default_model=self.model_id,
            model_env="AI_RADAR_DEEPSEEK_ENRICH_MODEL",
            ark_model_env="AI_RADAR_ARK_ENRICH_MODEL",
            temperature=float(os.environ.get("AI_RADAR_ENRICH_TEMPERATURE", "0.2")),
            stage="enrich",
            item_id=item.id,
            input_item_count=1,
            input_char_count=input_char_count,
            attribution={"source_id": item.source_id, "url": item.url, "title": item.title},
        )
        from ..enrich.normalizers.production_enrich_provider_output_v2 import adapt_raw

        parsed = adapt_raw(result.json)
        return EnrichResultV2(
            title_zh=str(parsed.get("title_zh", "")),
            summary_zh=str(parsed.get("summary_zh", "")),
            why_recommend=str(parsed.get("why_recommend", "")),
            tags=tuple(str(tag) for tag in parsed.get("tags", [])),
            primary_category=str(parsed["primary_category"]),
            is_opinion=bool(parsed["is_opinion"]),
            raw={"provider": result.provider, "model": result.model, "json": result.json},
        )
