"""Judge prompts for aihot-fit (summary and recommendation-reason closeness).

The reference is AIHOT's editorial output. It is the target *taste*, not ground
truth: a candidate that is equally correct but picks different facts or a
different emphasis must lose points.
"""

from __future__ import annotations

import hashlib

CONTENT_CHAR_LIMIT = 3000

_COMMON_RULES = """你是内容评测的裁判。你会拿到三样东西：
1. 原文（标题 + 正文前 {limit} 字）
2. 参考文本：AIHOT 编辑对这篇内容写的版本，代表我们要拟合的目标口味
3. 候选文本：我们系统对同一篇内容写的版本

请只比较候选与参考的接近程度，输出 0–100 的整数分 closeness：
- 100 = 与参考几乎等价：覆盖同样的事实、同样的取舍重点、同样的编辑风格
- 60–80 = 主要事实一致，但重点或风格有可感知差别
- 30–50 = 只有部分事实重合，或取舍明显不同
- 0–20 = 几乎不相关、事实冲突、或写的是另一件事

三方面各占大致三分之一：事实覆盖（参考提到的关键事实候选覆盖了多少，候选有没有参考没有的内容）、取舍重点（先说什么、强调什么、省略什么）、编辑风格（句式、密度、语气、长度）。
参考是目标口味而不是绝对真值：候选即使同样正确，只要取舍或风格与参考不同，也应当扣分。不要因为候选"写得更好"而加分。
只输出一个 JSON 对象：{{"closeness": <0-100 整数>, "rationale": "<一句话说明扣分或得分的主要原因>"}}。不要输出其它文字。"""

SUMMARY_SYSTEM = _COMMON_RULES.format(limit=CONTENT_CHAR_LIMIT) + "\n本次比较的是「摘要」。"

REASON_SYSTEM = (
    _COMMON_RULES.format(limit=CONTENT_CHAR_LIMIT)
    + "\n本次比较的是「推荐理由」：它应是一句话判断，并带可核实的事实锚点（数字、名称、具体动作）。"
    "判断方向是否一致、事实锚点是否相同，比措辞更重要；但一句话判断的语气与长度也属于编辑风格，要计入。"
)

USER_TEMPLATE = """【原文标题】
{title}

【原文正文（截断到 {limit} 字）】
{content}

【参考文本（AIHOT）】
{reference}

【候选文本（我们的系统）】
{candidate}

请输出 JSON。"""


def render_user(*, title: str, content: str, reference: str, candidate: str) -> str:
    return USER_TEMPLATE.format(
        title=title,
        limit=CONTENT_CHAR_LIMIT,
        content=content[:CONTENT_CHAR_LIMIT],
        reference=reference,
        candidate=candidate,
    )


def prompt_sha256(dimension: str) -> str:
    system = SUMMARY_SYSTEM if dimension == "summary" else REASON_SYSTEM
    return hashlib.sha256((system + "\n---\n" + USER_TEMPLATE).encode("utf-8")).hexdigest()


def system_prompt(dimension: str) -> str:
    if dimension == "summary":
        return SUMMARY_SYSTEM
    if dimension == "reason":
        return REASON_SYSTEM
    raise ValueError(f"unknown judge dimension: {dimension}")
