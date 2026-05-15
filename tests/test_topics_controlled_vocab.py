from __future__ import annotations

from airadar.topics import CONTROLLED_VOCABULARY, is_in_vocabulary, topic_tags


def test_controlled_vocabulary_contains_core_aihot_tags() -> None:
    required = {
        "模型发布",
        "评测/基准",
        "安全/对齐",
        "教程/实践",
        "大佬观点",
        "现象/趋势",
        "行业动态",
        "MCP/工具",
        "智能体",
    }
    assert required.issubset(CONTROLLED_VOCABULARY)


def test_topic_tags_empty_list() -> None:
    assert topic_tags([]) == []


def test_topic_tags_filters_unknown_tags() -> None:
    assert topic_tags(["模型发布", "不在词表的标签"]) == ["模型发布"]


def test_topic_tags_keeps_aihot_brand_tags() -> None:
    assert topic_tags(["OpenAI", "模型发布", "GitHub", "教程/实践"]) == [
        "OpenAI",
        "模型发布",
        "GitHub",
        "教程/实践",
    ]


def test_topic_tags_adds_deterministic_source_brand_tags() -> None:
    tags = topic_tags(
        ["模型发布", "教程/实践"],
        source_id="claude_code_releases",
        source_name="Claude Code GitHub Releases",
        url="https://github.com/anthropics/claude-code/releases/tag/v2.1.139",
        title="Claude Code v2.1.139",
        content_text="Anthropic shipped a Claude Code update.",
    )

    assert tags[:2] == ["Anthropic", "GitHub"]
    assert "模型发布" in tags
    assert len(tags) <= 4


def test_is_in_vocabulary() -> None:
    assert is_in_vocabulary("模型发布") is True
    assert is_in_vocabulary("xxx") is False
