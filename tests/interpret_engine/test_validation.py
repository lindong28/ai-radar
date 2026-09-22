"""Tests for the validation field computed by summarizer.

Validation is the project-side self-check that lets the orchestrating agent
trust the summary without re-reading it. It checks: required design-doc
modules are present, keywords use the backtick-hashtag format, and tags are
all in the controlled vocabulary.
"""

from __future__ import annotations

from airadar.interpret.engine.summarizer.schema import (
    REQUIRED_SUMMARY_MODULES,
    Validation,
    parse_summary_output,
    validate_summary,
)

VALID_SUMMARY = """### 📋 文章概况
摘要描述。

### ✨ 独特亮点
- 点 1

### 🔧 可动手实践
| 目录点 | 你可以做什么 | 可动手程度 |
| --- | --- | --- |
| 评测闭环 | 建一个回归评测 | ✅可实现 |

### 🧠 可复用认知
- 认知 1

### 🏷️ 关键词与分类标签
`#Agent架构` `#评测闭环`

**分类标签**: `Agent架构` `评测设计`

### 📊 价值判断
**推荐等级**: 值得一看（实践细节提供了足够的信息增量。）
"""


KNOWN_TAGS = {"Agent架构", "评测设计"}


def test_validation_passes_when_all_modules_keywords_tags_correct() -> None:
    validation = validate_summary(
        summary_md=VALID_SUMMARY,
        tags=["Agent架构", "评测设计"],
        keywords=["Agent架构", "评测闭环"],
        known_tags=KNOWN_TAGS,
    )

    assert isinstance(validation, Validation)
    assert validation.ok is True
    assert validation.modules_ok is True
    assert validation.keyword_format_ok is True
    assert validation.tags_ok is True
    assert validation.tag_violations == []
    assert set(validation.missing_modules) == set()


def test_validation_flags_missing_module() -> None:
    summary_without_value_judgment = VALID_SUMMARY.replace(
        "### 📊 价值判断\n**推荐等级**: 值得一看（实践细节提供了足够的信息增量。）\n",
        "",
    )

    validation = validate_summary(
        summary_md=summary_without_value_judgment,
        tags=["Agent架构"],
        keywords=["Agent架构"],
        known_tags=KNOWN_TAGS,
    )

    assert validation.ok is False
    assert validation.modules_ok is False
    assert "价值判断" in validation.missing_modules


def test_validation_flags_unknown_tag() -> None:
    validation = validate_summary(
        summary_md=VALID_SUMMARY,
        tags=["Agent架构", "未知标签"],
        keywords=["Agent架构"],
        known_tags=KNOWN_TAGS,
    )

    assert validation.ok is False
    assert validation.tags_ok is False
    assert validation.tag_violations == ["未知标签"]


def test_validation_flags_keyword_without_backtick_hashtag() -> None:
    summary_without_keywords = VALID_SUMMARY.replace("`#Agent架构` `#评测闭环`", "Agent架构, 评测闭环")

    validation = validate_summary(
        summary_md=summary_without_keywords,
        tags=["Agent架构"],
        keywords=[],
        known_tags=KNOWN_TAGS,
    )

    assert validation.keyword_format_ok is False
    assert validation.ok is False


def test_required_modules_match_design_doc() -> None:
    expected = {"文章概况", "独特亮点", "可动手实践", "可复用认知", "关键词与分类标签", "价值判断"}
    assert set(REQUIRED_SUMMARY_MODULES) == expected


def test_validation_accepts_split_keyword_and_tag_sections() -> None:
    """The prompt template renders keywords + tags as two separate blocks. Accept both forms."""
    summary_split_form = """### 📋 文章概况
摘要。

### ✨ 独特亮点
- 点

### 🔧 可动手实践
| 目录点 | 你可以做什么 | 可动手程度 |
| --- | --- | --- |
| 项 | 做 | ✅可实现 |

### 🧠 可复用认知
- 认知

### 🏷️ 关键词
`#Agent架构`

**分类标签**: `Agent架构`

### 📊 价值判断
**推荐等级**: 必读。
"""

    validation = validate_summary(
        summary_md=summary_split_form,
        tags=["Agent架构"],
        keywords=["Agent架构"],
        known_tags={"Agent架构"},
    )
    assert validation.modules_ok is True
    assert validation.ok is True


def test_parse_summary_output_includes_validation(monkeypatch) -> None:
    parsed = parse_summary_output(VALID_SUMMARY, known_tags=KNOWN_TAGS)
    assert parsed.validation is not None
    assert parsed.validation.ok is True
