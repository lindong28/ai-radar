from __future__ import annotations

import os

from ..prefilter.prompts import render_prefilter_prompt
from .base import PrefilterResult, ProviderItem
from .deepseek_chat import chat_json
from .heuristics import heuristic_prefilter


class DeepSeekV32Prefilter:
    model_id = "deepseek-v4-flash"

    def smoke_test(self) -> str:
        return "ok" if os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("ARK_API_KEY") else "ok (offline fallback)"

    def is_ai_related(self, item: ProviderItem) -> PrefilterResult:
        if not (os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("ARK_API_KEY")) or os.environ.get(
            "AI_RADAR_FORCE_HEURISTIC"
        ):
            return heuristic_prefilter(item)
        prompt = render_prefilter_prompt(item)
        result = chat_json(
            system=prompt["system"],
            user=prompt["user"],
            default_model=self.model_id,
            model_env="AI_RADAR_DEEPSEEK_PREFILTER_MODEL",
            ark_model_env="AI_RADAR_ARK_PREFILTER_MODEL",
            temperature=0.0,
            max_tokens=200,
        )
        payload = result.json
        return PrefilterResult(
            is_ai_related=bool(payload.get("is_ai_related")),
            confidence=max(0.0, min(1.0, float(payload.get("confidence", 0.0)))),
            raw={"provider": result.provider, "model": result.model, "json": payload},
        )
