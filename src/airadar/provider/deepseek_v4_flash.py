from __future__ import annotations

import os

from ..enrich.prompts import render_enrich_prompt
from .base import EnrichResult, ProviderItem
from .deepseek_chat import chat_json


class DeepSeekV4FlashEnricher:
    model_id = "deepseek-v4-flash"

    def smoke_test(self) -> str:
        return "ok" if os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("ARK_API_KEY") else "skipped (missing key)"

    def enrich(self, item: ProviderItem) -> EnrichResult:
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
        parsed = result.json
        return EnrichResult(
            title_zh=str(parsed.get("title_zh", "")),
            summary_zh=str(parsed.get("summary_zh", "")),
            why_recommend=str(parsed.get("why_recommend", "")),
            tags=tuple(str(tag) for tag in parsed.get("tags", [])),
            raw={"provider": result.provider, "model": result.model, "json": parsed},
        )
