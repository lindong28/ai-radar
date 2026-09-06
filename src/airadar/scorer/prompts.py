from __future__ import annotations

from jinja2 import Template

from ..provider.base import ProviderItem

SYSTEM_PROMPT = (
    "You score AI news for an engineer's personal radar. Return strict JSON with six 0-10 scores "
    "and a concise reasoning string. Do not output a final score or recommendation."
)

USER_TEMPLATE = Template(
    """
Score this item on exactly these dimensions:
- relevance: AI/model/systems/tooling/research relevance.
- density: new information per word, excluding fluff.
- recency: freshness against the current AI/engineering state.
- authority: first-party or verified source strength.
- engineering: direct usefulness for code, architecture, APIs, benchmarks, evals, or operations.
- significance: how consequential the event or subject this item is ABOUT is to the AI field
  right now. Judge the underlying story, not how thoroughly this particular item covers it: a
  one-line post about a major lab acquisition or a frontier model release is highly
  significant, a careful walkthrough of a niche tool is not, and chatter that reports no
  event at all is near zero.

Source tier: {{ item.tier }}
Source id: {{ item.source_id }}
Title: {{ item.title }}
Author: {{ item.author or "unknown" }}
Published: {{ item.published_at }}
URL: {{ item.url }}

Content:
{{ item.content_text[:5000] }}

Output JSON:
{
  "relevance": 0.0-10.0,
  "density": 0.0-10.0,
  "recency": 0.0-10.0,
  "authority": 0.0-10.0,
  "engineering": 0.0-10.0,
  "significance": 0.0-10.0,
  "reasoning": "<=200 chars"
}
""".strip()
)


def render_scoring_prompt(item: ProviderItem) -> dict[str, str]:
    return {"system": SYSTEM_PROMPT, "user": USER_TEMPLATE.render(item=item)}
