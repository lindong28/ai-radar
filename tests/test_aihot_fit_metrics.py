from __future__ import annotations

from airadar.curator import select as curator_select
from airadar.eval.aihot_fit import metrics  # noqa: F401
from airadar.eval.aihot_fit.metrics import (
    Joined,
    category_agreement,
    ranking_score,
    selected_auc,
    selected_auc_ranked,
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


def test_ranked_auc_reduces_to_plain_auc_when_no_category_is_demoted(monkeypatch) -> None:
    """The negative control the metric itself has to pass.

    `selected_auc_ranked` is a derivation, not a stored number, so the way it fails is by
    quietly ranking on something other than production's ordering score. With an empty
    multiplier table the ordering score IS `weighted_score`, so the two metrics must agree to
    the last digit; any indexing or fallback slip shows up here as a mismatch. Verified on the
    real FULL3 run too, where both read 0.7907 with the table emptied.
    """

    rows = [
        _row("q1", category="paper", predicted="paper", score=9.0, selected=True),
        _row("q2", category="model", predicted="model", score=8.0, selected=False),
        _row("q3", category="paper", predicted="paper", score=7.0, selected=False),
    ]
    monkeypatch.setattr(curator_select, "CATEGORY_MULTIPLIERS", {})
    assert selected_auc_ranked(rows).value == selected_auc(rows).value

    # And it must actually respond to the table -- otherwise the assert above is satisfied by a
    # metric that ignores the coefficient entirely, which is the whole defect being fixed.
    monkeypatch.setattr(curator_select, "CATEGORY_MULTIPLIERS", {"paper": 0.5})
    assert selected_auc_ranked(rows).value != selected_auc(rows).value


def test_ranked_auc_keeps_unenriched_rows_at_factor_one_like_production() -> None:
    """`_load_candidates` gives an un-enriched item `category == ""`, and
    `category_multiplier("")` is 1.0 -- it ranks, it is not dropped. Excluding those rows here
    would silently measure a different population than the one production ranks (2624 vs 2741
    rows on FULL3), and n is the only place that would show it."""

    rows = [
        _row("q1", category="paper", predicted="paper", score=9.0, selected=True),
        Joined(
            question_id="q2",
            reference={"primary_category": "model", "selected": False, "published_at": "2026-08-19T08:00:00Z"},
            enrich=None,
            weighted_score=8.0,
        ),
    ]
    metric = selected_auc_ranked(rows)
    assert metric.n == 2
    assert ranking_score(rows[1]) == 8.0
