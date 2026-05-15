from __future__ import annotations

from jinja2 import Template

from ..provider.base import ProviderItem
from ..topics import CONTROLLED_VOCABULARY

SYSTEM_PROMPT = (
    "你是 AI Hot 风格的中文 AI 内容编辑。你要把英文或中文 AI 资讯改写成适合中文 AI 从业者快速浏览的内容包。"
    "摘要必须是 3-5 句中文，覆盖 WHY 和 SO WHAT，不能只是翻译原文。"
    "推荐理由必须像 AI Hot 一样短而有判断：默认一句话，35-90 个中文字符，先给事实锚点或判断，再说明为什么值得读。"
    "不要以“适合”“必读”“必看”“推荐给”开头；不要使用“适合 X 必读/必看”模板；"
    "避免“如果你是...”这类长铺垫和模板化解释。"
    "标题要中文化；中文标题可保留原意但要更适合信息流阅读。"
    "标签只能从给定词表中选择 2-4 个，不得创造新标签；优先选择最能解释内容的主题/品牌标签。只输出 JSON。"
)

USER_TEMPLATE = Template(
    """
请为下面这条 AI 资讯生成中文内容包。

可选标签词表：
{{ vocabulary }}

Source id: {{ item.source_id }}
Source tier: {{ item.tier }}
Title: {{ item.title }}
Author: {{ item.author or "unknown" }}
Published: {{ item.published_at }}
URL: {{ item.url }}

Content:
{{ item.content_text[:5000] }}

输出 JSON，字段必须完全如下：
{
  "title_zh": "2-120 字中文标题",
  "summary_zh": "3-5 句中文摘要，20-400 字，覆盖核心事实、WHY、SO WHAT",
  "why_recommend": "35-90 字短推荐理由，说明谁应该读、为什么值得读、有明确判断",
  "tags": ["从词表选择", "2-4 个"]
}
""".strip()
)


def render_enrich_prompt(item: ProviderItem) -> dict[str, str]:
    return {
        "system": SYSTEM_PROMPT,
        "user": USER_TEMPLATE.render(item=item, vocabulary="、".join(CONTROLLED_VOCABULARY)),
    }
