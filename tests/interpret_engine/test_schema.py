import pytest

from airadar.interpret.engine.summarizer.schema import parse_summary_output

SUMMARY_WITH_JSON = """### 📋 文章概况
这是一篇关于 Agent 工作流的文章。

### 🔧 可动手实践
| 目录点 | 你可以做什么 | 可动手程度 |
| --- | --- | --- |
| 评测闭环 | 建一个回归评测 | ✅可实现 |

### 🏷️ 关键词
`#Agent架构` `#评测闭环`

**分类标签**: `Agent架构` `评测设计`

### 📊 价值判断
**推荐等级**: 值得一看，摘要已足够。

```json
{
  "recommendation": "值得一看",
  "criteria_reason": "实现细节提供了足以占用原文阅读时间的信息增量。",
  "save_decision": false,
  "save_reason": "模型判断会被程序重算",
  "tags": ["Agent架构", "评测设计"],
  "keywords": ["Agent架构", "评测闭环"],
  "projects": [{"name": "Example", "url": "", "description": "demo"}]
}
```
"""


def test_parse_summary_output_prefers_json_but_recomputes_save_decision() -> None:
    parsed = parse_summary_output(SUMMARY_WITH_JSON)

    assert "```json" not in parsed.summary_md
    assert parsed.recommendation == "值得一看"
    assert parsed.criteria_reason == "实现细节提供了足以占用原文阅读时间的信息增量。"
    assert parsed.criteria_reason_source == "json"
    assert parsed.save_decision is True
    assert "primary ✅可实现" in parsed.save_reason
    assert parsed.tags == ["Agent架构", "评测设计"]
    assert parsed.keywords == ["Agent架构", "评测闭环"]
    assert parsed.projects[0]["name"] == "Example"


def test_parse_summary_output_fails_when_criteria_reason_is_missing() -> None:
    output = SUMMARY_WITH_JSON.replace(
        '  "criteria_reason": "实现细节提供了足以占用原文阅读时间的信息增量。",\n',
        "",
    )

    with pytest.raises(ValueError, match="criteria_reason"):
        parse_summary_output(output)


def test_parse_summary_output_falls_back_to_markdown() -> None:
    parsed = parse_summary_output(
        """### 🏷️ 关键词
`#上下文管理` `#Keep-recent-k`

**分类标签**: `Agent架构` `工具设计`

### 📊 价值判断
**推荐等级**: 可跳过，摘要已覆盖全部有用信息。

```json
{
  "recommendation": "可跳过",
  "criteria_reason": "摘要已覆盖全部信息，原文没有额外信息增量。"
}
```
"""
    )

    assert parsed.recommendation == "可跳过"
    assert parsed.save_decision is False
    assert parsed.keywords == ["上下文管理", "Keep-recent-k"]
    assert parsed.tags == ["Agent架构", "工具设计"]


def test_parse_summary_output_recovers_unique_value_judgment_line_reason() -> None:
    parsed = parse_summary_output(
        """### 📋 文章概况
摘要已经覆盖主要事实。

### 📊 价值判断
**推荐等级**: 可跳过（信息增量有限：摘要已经覆盖主要结论。）
**最值得看**: 已在摘要中呈现。
"""
    )

    assert parsed.recommendation == "可跳过"
    assert parsed.criteria_reason == "信息增量有限：摘要已经覆盖主要结论。"
    assert parsed.criteria_reason_source == "markdown_value_judgment_line"


def test_parse_summary_output_rejects_markdown_reason_for_a_different_json_recommendation() -> None:
    output = """### 📊 价值判断
**推荐等级**: 可跳过（摘要已经覆盖主要结论。）

```json
{
  "recommendation": "必读"
}
```
"""

    with pytest.raises(ValueError, match="criteria_reason"):
        parse_summary_output(output)


def test_parse_summary_output_rejects_multiple_value_judgment_reason_candidates() -> None:
    output = """### 📊 价值判断
**推荐等级**: 可跳过（摘要已经覆盖主要结论。）
**推荐等级**: 可跳过（原文没有额外信息增量。）
"""

    with pytest.raises(ValueError, match="criteria_reason"):
        parse_summary_output(output)


def test_parse_summary_output_rejects_reason_outside_value_judgment_section() -> None:
    output = """### 📋 文章概况
**推荐等级**: 可跳过（这不是价值判断模块里的理由。）

### 📊 价值判断
**推荐等级**: 可跳过
"""

    with pytest.raises(ValueError, match="criteria_reason"):
        parse_summary_output(output)


@pytest.mark.parametrize(
    "output",
    [
        """### 📊 价值判断
**推荐等级**: 可跳过（旧理由。）
**推荐等级**: 必读
""",
        """### 📊 价值判断
**推荐等级**: 可跳过（旧理由。）

### 📊 价值判断
**推荐等级**: 必读
""",
        """### 📊 价值判断
**推荐等级**: 可跳过（理由一）（理由二）
""",
    ],
)
def test_parse_summary_output_rejects_ambiguous_value_judgment_reason(output: str) -> None:
    with pytest.raises(ValueError, match="criteria_reason"):
        parse_summary_output(output)
