"""Production category 0.1.0: frozen V2 prompt and explicit business attempts."""
from __future__ import annotations

from pathlib import Path

from ..provider.deepseek_chat import chat_json
from ..provider.llm_gateway import GatewayRequestError
from .category import category_output, render_category_prompt
from .category_materials import render_materials

VERSION = "0.1.0"
RUBRIC = Path(__file__).with_name("category_v2.txt").read_text(encoding="utf-8")
CONFIG = dict(default_model="personal_ark::deepseek-v4-pro-ga-260813", model_env=None,
              ark_model_env="", temperature=0.0, thinking="enabled",
              reasoning_effort="high", max_tokens=32768, timeout=90.0)


def render_prompt(raw: dict, supplement: dict, quotes: list[dict]) -> dict[str, str]:
    prompt = render_category_prompt(raw, RUBRIC, body_limit=None)
    prompt["user"] = render_materials(raw, supplement, quotes)
    return prompt


def production_prompt(raw: dict, article: dict, quotes: list[dict]) -> dict[str, str]:
    """Adapt already retrieved sources, without fetching or consuming model labels."""
    materials = []
    if article.get("status") == "available":
        materials.append({"kind": "article", "url": article.get("url", raw.get("url")),
                          "content_text": article["content_text"]})
    for linked in article.get("linked_articles", []):
        if linked.get("status") == "available":
            materials.append({"kind": "linked-article", "url": linked.get("url"),
                              "content_text": linked["content_text"]})
    supplement = {"status": "available" if materials else "unavailable",
                  "content_text": "\n\n".join(m["content_text"] for m in materials),
                  "repair_materials": materials}
    nested_quotes = [{"status": q.get("status"), "input": q.get("input", q)} for q in quotes]
    return render_prompt(raw, supplement, nested_quotes)


class ClassificationFailed(ValueError):
    def __init__(self, trace: dict):
        super().__init__("category 0.1.0 failed after three attempts; no category fallback")
        self.trace = trace
        self.output_rejected = all(a.get("failure_kind") == "output" for a in trace["attempts"])


def classify(prompt: dict[str, str], *, item_id: str, db_path=None) -> tuple[str, dict]:
    trace = {"version": VERSION, "prompt": prompt, "config": dict(CONFIG), "attempts": []}
    # Explicit user-authorized business retries; transport itself keeps max_retries=0.
    # Each attempt retains its gateway request identity, including ambiguous failures.
    for _ in range(3):
        attempt = {}
        trace["attempts"].append(attempt)
        try:
            result = chat_json(**prompt, **CONFIG, stage="enrich", item_id=item_id,
                               attribution={"component": "category", "version": VERSION}, db_path=db_path)
            attempt.update(sent_request_id=result.sent_request_id, gateway=result.gateway,
                           provider=result.provider, model=result.model, output=result.json,
                           reasoning=result.reasoning)
            category_output(result.json)
            return result.json["primary_category"], trace
        except Exception as exc:
            attempt.update(error_type=type(exc).__name__, error=str(exc))
            invalid_output = "output" in attempt
            if isinstance(exc, GatewayRequestError):
                attempt.update(error_code=exc.code, response_body=exc.body, gateway=exc.identity)
                invalid_output = exc.code in {"JSONDecodeError", "ValueError", "TypeError"}
            attempt["failure_kind"] = "output" if invalid_output else "transport"
            request_id = getattr(exc, "sent_request_id", None)
            if request_id is not None:
                attempt["sent_request_id"] = request_id
    raise ClassificationFailed(trace)
