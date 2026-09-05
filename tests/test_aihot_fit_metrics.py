from __future__ import annotations

from airadar.eval.aihot_fit.metrics import (
    Joined,
    category_agreement,
    selected_p_at_k,
    tag_jaccard_mean,
)


def _row(
    question_id: str,
    *,
    category: str,
    predicted: str,
    ref_tags: list[str] | None = None,
    our_tags: list[str] | None = None,
    score: float | None = None,
    selected: bool = False,
    published_at: str = "2026-08-19T08:00:00Z",
) -> Joined:
    return Joined(
        question_id=question_id,
        reference={
            "primary_category": category,
            "tags": ref_tags,
            "selected": selected,
            "published_at": published_at,
            "score_0_100": 50,
        },
        enrich={"primary_category": predicted, "tags": our_tags or []},
        weighted_score=score,
    )


def test_category_agreement_separates_right_from_wrong() -> None:
    right = [
        _row("q1", category="model", predicted="model"),
        _row("q2", category="paper", predicted="paper"),
        _row("q3", category="tutorial", predicted="tutorial"),
    ]
    wrong = [
        _row("q1", category="model", predicted="product"),
        _row("q2", category="paper", predicted="paper"),
        _row("q3", category="tutorial", predicted="industry"),
    ]
    good = category_agreement(right)
    bad = category_agreement(wrong)
    assert good.n == 3 and good.value == 1.0
    assert bad.n == 3 and bad.value == 0.3333
    assert bad.extra["confusion_matrix"]["counts"]["model"]["product"] == 1
    assert bad.baseline["kind"] == "majority_class" and bad.baseline["value"] == 0.3333


def test_tag_jaccard_maps_reference_aliases_and_drops_unknown() -> None:
    exact = [
        _row("q1", category="model", predicted="model", ref_tags=["Agent", "MCP"], our_tags=["智能体", "MCP/工具"]),
        _row("q2", category="model", predicted="model", ref_tags=["OpenAI", "不在词表的标签"], our_tags=["OpenAI"]),
    ]
    disjoint = [
        _row("q1", category="model", predicted="model", ref_tags=["Agent", "MCP"], our_tags=["视频", "搜索"]),
        _row("q2", category="model", predicted="model", ref_tags=["OpenAI", "不在词表的标签"], our_tags=["Meta"]),
    ]
    good = tag_jaccard_mean(exact)
    bad = tag_jaccard_mean(disjoint)
    assert good.n == 2 and good.value == 1.0
    assert good.extra["reference_tags_dropped_out_of_vocabulary"] == 1
    assert bad.n == 2 and bad.value == 0.0


def test_selected_p_at_k_rewards_ranking_selected_items_first() -> None:
    day = "2026-08-19T10:00:00Z"
    ranked_right = [
        _row("q1", category="model", predicted="model", score=9.0, selected=True, published_at=day),
        _row("q2", category="model", predicted="model", score=8.0, selected=True, published_at=day),
        _row("q3", category="model", predicted="model", score=3.0, selected=False, published_at=day),
        _row("q4", category="model", predicted="model", score=1.0, selected=False, published_at=day),
    ]
    ranked_wrong = [
        _row("q1", category="model", predicted="model", score=1.0, selected=True, published_at=day),
        _row("q2", category="model", predicted="model", score=2.0, selected=True, published_at=day),
        _row("q3", category="model", predicted="model", score=8.0, selected=False, published_at=day),
        _row("q4", category="model", predicted="model", score=9.0, selected=False, published_at=day),
    ]
    good = selected_p_at_k(ranked_right)
    bad = selected_p_at_k(ranked_wrong)
    assert good.n == 1 and good.value == 1.0 and good.extra["days"][0]["k"] == 2
    assert bad.n == 1 and bad.value == 0.0
    assert good.baseline["value"] == 0.5  # k/n = 2/4 selected rate
