from __future__ import annotations

from jinja2 import Template

from ..provider.base import ProviderItem

SYSTEM_PROMPT = (
    "You are an AI news relevance filter for an engineer's personal radar. "
    "Return strict JSON with is_ai_related and confidence only."
)

USER_TEMPLATE = Template(
    """
Decide whether this RSS item is meaningfully related to AI models, AI systems,
model engineering, AI developer tools, evaluation, inference, agents, or applied
ML infrastructure.

Non-AI content includes, but is not limited to: consumer electronics accessories
such as printers, PC cases, ink, and peripherals; home appliances; film and TV
entertainment such as series or movie release news; automotive manufacturing,
factories, or parts; and pure hardware reviews that do not involve AI.

非 AI 内容包括（不限于此）：消费电子配件（打印机、机箱、墨水）、家电、影视娱乐（剧集、电影上映消息）、
汽车工业（工厂、零部件）、纯硬件评测但不涉及 AI。

Source tier: {{ item.tier }}
Source id: {{ item.source_id }}
Title: {{ item.title }}
Author: {{ item.author or "unknown" }}
Published: {{ item.published_at }}
URL: {{ item.url }}

Content:
{{ item.content_text[:4000] }}

Output JSON:
{"is_ai_related": true|false, "confidence": 0.0-1.0}
""".strip()
)


def render_prefilter_prompt(item: ProviderItem) -> dict[str, str]:
    return {"system": SYSTEM_PROMPT, "user": USER_TEMPLATE.render(item=item)}
