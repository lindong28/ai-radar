from __future__ import annotations

from jinja2 import Template

from ..provider.base import ProviderItem

SYSTEM_PROMPT = (
    "You are an AI news relevance filter for an engineer's personal radar. "
    "Return strict JSON with is_ai_related and confidence only."
)

USER_TEMPLATE = Template(
    """
Decide what this item is ABOUT, then answer whether that thing is AI.

The test is aboutness, not association. Ask "what is the one thing this
item reports?" and judge that thing. An item mentioning AI, using AI, or
shipping with an AI feature is not thereby about AI.

is_ai_related = true when the thing it reports is: an AI model, system,
agent or dataset; model engineering, training, evaluation or inference;
AI developer tools or applied ML infrastructure; AI research; or a
business, funding, regulatory or personnel event whose subject is AI
itself (an AI company's raise, an AI chip order, an AI policy ruling).

is_ai_related = false when the thing it reports is something else that
merely involves AI. The launch, pricing, colourway, availability or
review of a consumer device -- a phone, car, pair of glasses, appliance,
laptop -- is about that device even when it ships an assistant, a
"smart" mode, or an AI chip. Same for entertainment, sports, general
business news, and accessories. Deciding by the presence of the word
"AI", or by the vendor being a technology company, gets these wrong.

If the item carries no judgeable content -- an empty body, a bare link,
a title with no claim in it -- answer false: there is nothing to be
about. Do not infer a topic from the source or the URL.

判据是**这条内容在讲的那一件事**是不是 AI，不是它有没有提到、用到或内置 AI。
一部手机、一辆车、一副眼镜、一台家电的发布、定价、配色、上市或评测，讲的是
那件硬件——即使它搭载助手、"智能"模式或 AI 芯片，也是 false。娱乐、体育、
泛商业新闻、配件同理。**别按"出现了 AI 这个词"或"厂商是科技公司"来判。**
正文空、只有一个裸链接、或标题不含任何主张时，答 false——没有可判的对象，
且不要从来源或 URL 反推主题。

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
