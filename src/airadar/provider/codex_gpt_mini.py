from __future__ import annotations

import json
import logging
import os
from uuid import uuid4

from pydantic import BaseModel, Field

from .base import ProviderItem, ScoringResult
from .heuristics import heuristic_score
from .llm_gateway import gateway_client, gateway_error, gateway_headers, gateway_identity, gateway_smoke_status

logger = logging.getLogger(__name__)


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
        return gateway_smoke_status(heuristic=True)

    def score_5d(self, item: ProviderItem) -> ScoringResult:
        if os.environ.get("AI_RADAR_FORCE_HEURISTIC"):
            return heuristic_score(item)
        return self._score_with_openai(item)

    def _score_with_openai(self, item: ProviderItem) -> ScoringResult:
        request_id = str(uuid4())
        client = None
        completion = None
        model = os.environ.get("AI_RADAR_OPENAI_SCORER_MODEL", "gpt-4o-mini")
        try:
            timeout = float(os.environ.get("AI_RADAR_OPENAI_TIMEOUT", "30"))
            client = gateway_client(
                callsite_id="provider.codex_gpt_mini.score",
                timeout=timeout,
            )
            schema = _OpenAIScoringResponse.model_json_schema()
            schema["additionalProperties"] = False
            completion = client.chat.completions.create(
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
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "_OpenAIScoringResponse", "strict": True, "schema": schema},
                },
                extra_headers=gateway_headers(request_id),
                extra_body={"timeout": timeout},
            )
            identity = gateway_identity(completion, request_id)
            if identity["requested_logical_model"] != model:
                raise ValueError("gateway requested logical model does not match the sent model")
            content = completion.choices[0].message.content
            if content is None:
                raise ValueError("Gateway response did not include scoring content")
            parsed = _OpenAIScoringResponse.model_validate_json(content)
        except Exception as exc:
            raise gateway_error(exc, request_id, completion=completion) from exc
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception as exc:
                    logger.warning("LLM Gateway client close failed request_id=%s error_type=%s", request_id, type(exc).__name__)
        return ScoringResult(
            relevance=parsed.relevance,
            density=parsed.density,
            recency=parsed.recency,
            authority=parsed.authority,
            engineering=parsed.engineering,
            reasoning=parsed.reasoning,
            topics=(),
            raw={
                "provider": identity["provider_id"], "model": identity["actual_model"],
                "json": json.loads(parsed.model_dump_json()), "llm_gateway": identity, "sent_request_id": request_id,
            },
        )
