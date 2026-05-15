from __future__ import annotations

import json
import os

from openai import OpenAI
from pydantic import BaseModel, Field

from .base import ProviderItem, ScoringResult
from .heuristics import heuristic_score


class _OpenAIScoringResponse(BaseModel):
    relevance: float = Field(ge=0.0, le=10.0)
    density: float = Field(ge=0.0, le=10.0)
    recency: float = Field(ge=0.0, le=10.0)
    authority: float = Field(ge=0.0, le=10.0)
    engineering: float = Field(ge=0.0, le=10.0)
    reasoning: str = Field(max_length=200)


class CodexGptMiniScorer:
    model_id = "codex-gpt-mini"

    def smoke_test(self) -> str:
        return "ok" if os.environ.get("OPENAI_API_KEY") else "ok (offline heuristic fallback)"

    def score_5d(self, item: ProviderItem) -> ScoringResult:
        if os.environ.get("OPENAI_API_KEY") and not os.environ.get("AI_RADAR_FORCE_HEURISTIC"):
            try:
                return self._score_with_openai(item)
            except Exception:
                if not os.environ.get("AI_RADAR_ALLOW_LLM_FALLBACK", "1"):
                    raise
        return heuristic_score(item)

    def _score_with_openai(self, item: ProviderItem) -> ScoringResult:
        client = OpenAI(timeout=float(os.environ.get("AI_RADAR_OPENAI_TIMEOUT", "30")))
        model = os.environ.get("AI_RADAR_OPENAI_SCORER_MODEL", "gpt-4o-mini")
        completion = client.chat.completions.parse(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You score AI news for an engineer's personal radar. "
                        "Return only the requested structured object. Do not output a final recommendation."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Score this item on five independent 0-10 dimensions.\n"
                        "relevance: AI/model/systems/tooling/research relevance.\n"
                        "density: new information per word, excluding fluff.\n"
                        "recency: freshness against the current AI/engineering state.\n"
                        "authority: first-party or verified source strength.\n"
                        "engineering: usefulness for code, architecture, APIs, benchmarks, evals, or operations.\n\n"
                        f"Source tier: {item.tier}\n"
                        f"Source id: {item.source_id}\n"
                        f"Title: {item.title}\n"
                        f"Author: {item.author or 'unknown'}\n"
                        f"Published: {item.published_at}\n"
                        f"URL: {item.url}\n\n"
                        f"Content:\n{item.content_text[:5000]}"
                    ),
                },
            ],
            response_format=_OpenAIScoringResponse,
        )
        parsed = completion.choices[0].message.parsed
        if parsed is None:
            raise ValueError("OpenAI response did not parse into scoring schema")
        return ScoringResult(
            relevance=parsed.relevance,
            density=parsed.density,
            recency=parsed.recency,
            authority=parsed.authority,
            engineering=parsed.engineering,
            reasoning=parsed.reasoning,
            topics=(),
            raw={"provider": "openai", "model": model, "json": json.loads(parsed.model_dump_json())},
        )
