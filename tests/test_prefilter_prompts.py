from __future__ import annotations

from airadar.prefilter.prompts import render_prefilter_prompt
from airadar.provider.base import ProviderItem


def test_prefilter_prompt_names_non_ai_boundary_examples() -> None:
    item = ProviderItem(
        id="fixture",
        title="Printer cartridge launch",
        url="https://example.com/printer",
        source_id="fixture",
        tier="T2",
        author="Ada",
        published_at="2026-05-13T00:00:00Z",
        content_text="A consumer electronics accessory announcement.",
    )

    prompt = render_prefilter_prompt(item)["user"]

    for keyword in ["消费电子配件", "家电", "影视娱乐", "汽车工业", "纯硬件评测"]:
        assert keyword in prompt
