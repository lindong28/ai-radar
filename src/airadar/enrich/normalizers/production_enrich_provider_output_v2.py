from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any

from ...provider.base import ProviderItem
from .production_enrich_provider_output_v1 import (
    CONTROLLED_VOCABULARY,
    deterministic_tags,
)

NORMALIZER_ID = "production_enrich_provider_output_v2"
AUTHORITY_PATH = "src/airadar/enrich/normalizers/production_enrich_provider_output_v2.py"
TAG_MAP_VERSION = "aihot-radar-tag-map-v2"
AIHOT_TO_RADAR_TAG_MAP: dict[str, str] = {
    "Agent": "智能体",
    "MCP/工具调用": "MCP/工具",
    "arXiv": "论文/研究",
}
RADAR_TO_AIHOT_TAG_MAP: dict[str, str] = {radar: aihot for aihot, radar in AIHOT_TO_RADAR_TAG_MAP.items()}
AIHOT_VOCABULARY_ADDITIONS: tuple[str, ...] = (
    "DeepSeek",
    "Google",
    "Hugging Face",
    "Meta",
    "Microsoft",
    "xAI",
    # AIHOT uses both across the 584 reference items we hold; without them the model has no
    # in-vocabulary way to say what AIHOT said, and the reference tag is discarded when scoring.
    "政策/监管",
    "语音",
)
# Terms AIHOT never used across those 584 items. Emitting one cannot raise the intersection with
# an AIHOT reference and always enlarges the union, so each occurrence costs tag agreement --
# measured at 21 of our 1673 tag instances. Dropped by the 2026-09-06 decision that fitting AIHOT
# outranks keeping our own vocabulary; candidates to restore are listed in docs/issues/general.md.
AIHOT_VOCABULARY_REMOVALS: tuple[str, ...] = ("DeepMind", "GitHub", "RAG", "arXiv", "搜索")
CONTROLLED_VOCABULARY_V2 = tuple(
    tag for tag in (*CONTROLLED_VOCABULARY, *AIHOT_VOCABULARY_ADDITIONS) if tag not in AIHOT_VOCABULARY_REMOVALS
)
_VOCABULARY_SET = frozenset(CONTROLLED_VOCABULARY_V2)
# The vocabulary is a *write* contract: it says what the model may emit now. `EnrichOutputV2`
# also validates rows on the way back out, and 279 stored rows -- already paid for -- carry a
# retired term. Validating those against the tightened set makes model_validate raise,
# parse_enrichment swallow it, and the whole enrichment vanish from the page: Chinese title,
# summary, reason, category and every tag, not just the retired one. Nothing re-enriches them
# either, because current_version_v2() is a constant and the runner skips rows that succeeded.
# So reads accept everything writes ever accepted; only writes tighten.
_READABLE_VOCABULARY_SET = _VOCABULARY_SET | frozenset(AIHOT_VOCABULARY_REMOVALS)


def canonical_tag_map_bytes() -> bytes:
    payload = {
        "schema_version": "aihot-radar-tag-map-v1",
        "version": TAG_MAP_VERSION,
        "aihot_to_radar": AIHOT_TO_RADAR_TAG_MAP,
        "radar_to_aihot": RADAR_TO_AIHOT_TAG_MAP,
        "radar_vocabulary": list(CONTROLLED_VOCABULARY_V2),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


TAG_MAP_SHA256 = hashlib.sha256(canonical_tag_map_bytes()).hexdigest()


def is_in_v2_vocabulary(tag: str) -> bool:
    """Whether a stored tag is readable. Retired terms stay readable -- see the set's comment."""
    return tag in _READABLE_VOCABULARY_SET


def topic_tags_v2(
    enriched_tags: list[str],
    *,
    source_id: str | None = None,
    source_name: str | None = None,
    url: str | None = None,
    title: str | None = None,
    content_text: str | None = None,
    limit: int = 4,
) -> list[str]:
    # Two different filters on purpose. Stored tags render as stored -- a row written under the
    # old vocabulary keeps its tag, the same reason reads use the wider set. Tags derived here are
    # computed fresh, so they follow current policy and go through the alias map first, or this
    # renderer would drop an arxiv.org source's tag while normalize() turns it into 论文/研究.
    merged: list[str] = []
    for tag in enriched_tags:
        if tag in _READABLE_VOCABULARY_SET and tag not in merged:
            merged.append(tag)
    for raw in deterministic_tags(
        source_id=source_id,
        source_name=source_name,
        url=url,
        title=title,
        content_text=content_text,
    ):
        tag = AIHOT_TO_RADAR_TAG_MAP.get(raw, raw)
        if tag in _VOCABULARY_SET and tag not in merged:
            merged.append(tag)
    return merged[:limit]


def adapt_raw(value: Mapping[str, object]) -> dict[str, Any]:
    raw_tags = value.get("tags")
    if not isinstance(raw_tags, (list, tuple)):
        raise TypeError("tags must be an array")
    primary_category = value.get("primary_category")
    if not isinstance(primary_category, str):
        raise TypeError("primary_category must be one explicit string")
    is_opinion = value.get("is_opinion")
    if type(is_opinion) is not bool:
        raise TypeError("is_opinion must be a boolean")
    return {
        "title_zh": str(value.get("title_zh", "")),
        "summary_zh": str(value.get("summary_zh", "")),
        "why_recommend": str(value.get("why_recommend", "")),
        "tags": [str(tag) for tag in raw_tags],
        "primary_category": primary_category,
        "is_opinion": is_opinion,
    }


def _unique(tags: Iterable[str]) -> list[str]:
    seen: list[str] = []
    for tag in tags:
        if tag not in seen:
            seen.append(tag)
    return seen


def normalize(value: Mapping[str, object], *, item: ProviderItem) -> dict[str, Any]:
    normalized = adapt_raw(value)
    # An over-long tag list is truncated, not rejected. merged[:4] below already establishes that
    # truncation is an acceptable outcome, so refusing five tags up front threw away the summary,
    # reason and category for a field the code was about to trim anyway -- 75 of 2741 items on the
    # 2026-09-06 full run, the single largest failure bucket. Too few is left to the floor further
    # down, which can still be met by the deterministic layer.
    #
    # This does close a measurement channel: non-compliance with the 2-4 instruction used to be a
    # countable `item_evaluations.error` row and now is not. It is still readable in an eval run,
    # whose outputs.jsonl keeps the model's own `raw.json.tags` for accepted and rejected rows
    # alike; it is not readable in production, which stores only the normalized output.
    #
    # The trim happens AFTER the vocabulary filter below, not here. Truncating first cuts by
    # position, so a compliant tag sitting fifth is lost while an out-of-vocabulary one sitting
    # first survives to be dropped anyway -- which is how the first cut of this change moved four
    # items out of the tag-count bucket and eight into the vocabulary floor, a net loss.
    raw_tags = normalized["tags"]

    # Drop tags outside the vocabulary instead of rejecting the whole output. AIHOT tags a brand
    # only when it is one of the labs on its own short list -- our vocabulary already carries that
    # list -- and uses topic tags for every other company: it tagged the Xiaomi humanoid piece
    # 产品更新/具身智能, not 小米. So an out-of-vocabulary tag sits alongside the topic tags AIHOT
    # would have chosen, and dropping just that one leaves the AIHOT-shaped remainder. Rejecting
    # instead discarded the summary, reason and category too: 117 of 2741 items on the 2026-09-06
    # baseline, 110 of them over a single offending tag.
    mapped_tags = [AIHOT_TO_RADAR_TAG_MAP.get(tag, tag) for tag in raw_tags]
    dropped = [tag for tag in mapped_tags if tag not in _VOCABULARY_SET]
    # Filter, then de-duplicate, then trim -- in that order. Trimming first cuts by position, so
    # a compliant tag sitting fifth is lost while an out-of-vocabulary or repeated one sitting
    # first survives only to be discarded a line later. Model tag order does carry information
    # (agreement with the AIHOT reference runs 0.79 / 0.55 / 0.48 / 0.38 across the first four
    # positions, against 0.62 flat when the lists are shuffled), so keeping the earliest survivors
    # is right -- it is trimming before the survivors are known that is wrong.
    mapped_tags = _unique([tag for tag in mapped_tags if tag in _VOCABULARY_SET])[:4]

    # deterministic_tags guards against v1's vocabulary, which still holds GitHub and arXiv, so
    # removals have to be applied here as well or the source-derived layer reintroduces them.
    # The alias map runs over them for the same reason it runs over model tags: arXiv's AIHOT
    # equivalent is 论文/研究, a tag AIHOT does use.
    derived = [
        AIHOT_TO_RADAR_TAG_MAP.get(tag, tag)
        for tag in deterministic_tags(
            source_id=item.source_id,
            url=item.url,
            title=item.title,
            content_text=item.content_text,
        )
    ]
    # Require one surviving MODEL tag specifically. The deterministic layer below does not depend
    # on the model at all -- keyword hits on title and body supply OpenAI/Anthropic, the URL host
    # the rest -- so without this check a response that understood none of the vocabulary would
    # still be accepted on the strength of its source. Dropping a single offending tag was the
    # point; accepting a response that got every tag wrong was not.
    if not mapped_tags:
        raise ValueError(f"no provider tag survived the controlled vocabulary: dropped {dropped}")

    merged = _unique([*mapped_tags, *(tag for tag in derived if tag in _VOCABULARY_SET)])
    if not merged:
        raise ValueError(
            "tags must normalize to at least 1 unique controlled value"
            + (f" (dropped outside vocabulary: {dropped})" if dropped else "")
        )
    normalized["tags"] = merged[:4]
    return normalized
