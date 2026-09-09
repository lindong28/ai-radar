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

Shipping counts. The release of an AI product, or of one feature of
one -- a model, an app, a mode, a tool, a service whose own function is
AI -- is about AI, however small the release and whoever ships it. A
music generator adding voice control, an agent platform posting its
research page, a lab describing a customer using its model: all true.
Do not read "product announcement" or "marketing post" as a reason for
false. The question is never what genre the writing is, only what the
thing it reports does.

is_ai_related = false when the thing it reports is something else that
merely involves AI. This is about *physical or unrelated* things: the
launch, pricing, colourway, availability or review of a device whose own
function is not AI -- a phone, a car, a pair of glasses, an appliance, a
laptop -- is about that device even when it ships an assistant, a
"smart" mode, or an AI chip. Same for entertainment, sports, celebrity
ventures, general business digests, and accessories. Deciding by the
presence of the word "AI", or by the vendor being a technology company,
gets these wrong -- and so does deciding by whether the item announces
something.

If the item carries no judgeable content -- an empty body, a bare link,
a title with no claim in it -- answer false: there is nothing to be
about. Do not infer a topic from the source or the URL.

判据是**这条内容在讲的那一件事**是不是 AI，不是它有没有提到、用到或内置 AI。
**一件本身就是 AI 的东西，它的发布也是 AI**——模型、应用、功能、工具、服务，
不论发布多小、由谁发布，都是 true（AI 音乐产品加个人声控制、Agent 公司贴出
研究页、实验室讲某个客户怎么用它的模型，全都是）。**别把"这是产品公告/营销
稿"当成判 false 的理由**：判的是它讲的那个东西干什么，不是这篇文章是什么文体。
反过来，**本身功能不是 AI 的实体物件**——手机、汽车、眼镜、家电、笔记本——
它的发布、定价、配色、上市或评测讲的是那件硬件，即使搭载助手、"智能"模式或
AI 芯片，也是 false。娱乐、体育、名人创业、泛商业早报、配件同理。
**别按"出现了 AI 这个词"、"厂商是科技公司"或"这是不是一条发布"来判。**
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
