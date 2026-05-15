from __future__ import annotations

import json
import os
from typing import Any

from openai import OpenAI

from ..enrich.prompts import render_enrich_prompt
from .base import EnrichResult, ProviderItem


def _deepseek_base_url() -> str:
    configured = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
    if configured.endswith("/chat/completions"):
        configured = configured[: -len("/chat/completions")]
    if configured == "https://api.deepseek.com":
        configured = f"{configured}/v1"
    return configured


class DeepSeekV4FlashEnricher:
    model_id = "deepseek-v4-flash"

    def smoke_test(self) -> str:
        return "ok" if os.environ.get("DEEPSEEK_API_KEY") else "skipped (missing DEEPSEEK_API_KEY)"

    def enrich(self, item: ProviderItem) -> EnrichResult:
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is required for deepseek_v4_flash enricher")
        prompt = render_enrich_prompt(item)
        client = OpenAI(
            api_key=api_key,
            base_url=_deepseek_base_url(),
            timeout=float(os.environ.get("AI_RADAR_DEEPSEEK_TIMEOUT", "60")),
        )
        completion = client.chat.completions.create(
            model=os.environ.get("AI_RADAR_DEEPSEEK_ENRICH_MODEL", self.model_id),
            messages=[
                {"role": "system", "content": prompt["system"]},
                {"role": "user", "content": prompt["user"]},
            ],
            response_format={"type": "json_object"},
            temperature=float(os.environ.get("AI_RADAR_ENRICH_TEMPERATURE", "0.2")),
        )
        content = completion.choices[0].message.content
        if content is None:
            raise ValueError("DeepSeek response did not include message content")
        parsed: dict[str, Any] = json.loads(content)
        return EnrichResult(
            title_zh=str(parsed.get("title_zh", "")),
            summary_zh=str(parsed.get("summary_zh", "")),
            why_recommend=str(parsed.get("why_recommend", "")),
            tags=tuple(str(tag) for tag in parsed.get("tags", [])),
            raw={"provider": "deepseek", "model": self.model_id, "json": parsed},
        )
