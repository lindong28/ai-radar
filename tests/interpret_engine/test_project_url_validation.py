from __future__ import annotations

import pytest

from airadar.interpret.engine import embedding


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/openai/codex",
        "https://huggingface.co/datasets/MiniMaxAI/OctoCodingBench",
        "https://pypi.org/project/openai/",
        "https://www.npmjs.com/package/@anthropic-ai/claude-code",
    ],
)
def test_is_reusable_project_url_accepts_code_and_package_urls(url):
    assert embedding.is_reusable_project_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "https://mp.weixin.qq.com/s/demo",
        "https://arxiv.org/pdf/2605.10204",
        "https://www.bloomberg.com/news/articles/2026-05-04/demo",
        "https://www.seeles.ai",
    ],
)
def test_is_reusable_project_url_rejects_non_reusable_urls(url):
    assert embedding.is_reusable_project_url(url) is False
