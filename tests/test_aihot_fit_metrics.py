from __future__ import annotations

import argparse

from airadar.curator import select as curator_select
from airadar.eval.aihot_fit import metrics  # noqa: F401
from airadar.eval.aihot_fit.cli import run_eval_fit
from airadar.eval.aihot_fit.metrics import (
    Joined,
    category_agreement,
    compare_to_baseline,
    evaluate_thresholds,
    judge_acceptance,
    metric_emitter_identity,
    ranking_score,
    selected_auc,
    selected_auc_ranked,
    selected_p_at_k,
    tag_jaccard_mean,
)


def _judge_identity(*, served_model: str = "served-v1") -> dict[str, object]:
    return {
        "schema_version": "aihot-fit-judge-v2",
        "requested_model": "requested-v1",
        "temperature": 0.0,
        "max_tokens": 512,
        "prompt_sha256": {"summary": "s", "reason": "r"},
        "ark_host": "ark.example",
        "provider_module_sha256": "provider-sha",
        "served_providers": ["ark"],
        "served_models": [served_model],
    }


def _calibration(identity: dict[str, object], *, scale_ok: bool | None = True) -> dict[str, object]:
    return {
        "identity": identity,
        "scale_ok": scale_ok,
        "calibration_identity": {
            "implementation_sha256": "judge-sha",
            "thresholds": {"positive_min_mean": 80.0, "negative_max_mean": 40.0},
            "control_tasks_sha256": "controls-sha",
        },
    }


def test_judge_acceptance_requires_matching_complete_calibration_identity() -> None:
    identity = _judge_identity()
    assert judge_acceptance(identity, _calibration(identity))["accepted"] is True
    assert judge_acceptance(identity, None) == {
        "accepted": False,
        "reason": "judge calibration missing",
        "condition_identity": {**identity, "complete": True},
    }
    assert judge_acceptance(identity, _calibration(identity, scale_ok=False))["accepted"] is False
    mismatch = _calibration(_judge_identity(served_model="served-v2"))
    assert judge_acceptance(identity, mismatch)["reason"] == "judge and calibration identities differ"


def test_unaccepted_judge_metric_is_excluded_from_threshold_and_baseline_verdicts() -> None:
    diagnostic = {
        "n": 30,
        "value": 0.9,
        "ci95": [0.8, 0.95],
        "acceptance": {"accepted": False, "reason": "judge calibration missing"},
    }
    verdict = evaluate_thresholds(
        {"summary_closeness_mean": diagnostic}, {"summary_closeness_mean": {"min": 0.5}}
    )
    assert verdict["summary_closeness_mean"]["confident"] is None
    assert verdict["summary_closeness_mean"]["value_meets"] is None
    assert "unaccepted diagnostic" in verdict["summary_closeness_mean"]["reason"]

    emitter = {"module": "m", "metric_name": "summary_closeness_mean", "emitter_sha256": "same"}
    common = {
        "questions_sha256": "questions",
        "subset_sha256": "subset",
        "stopped_early": False,
        "stages": {},
        "ranking": {"category_multipliers": {}},
        "judge": _judge_identity(),
        "measurement_identity": {
            "metric_emitters": {"summary_closeness_mean": emitter},
            "calibration": _calibration(_judge_identity())["calibration_identity"],
        },
        "metrics": {"summary_closeness_mean": diagnostic},
    }
    comparison = compare_to_baseline(common, common)
    assert comparison["comparable"] is False
    assert comparison["excluded_metrics"] == {
        "summary_closeness_mean": "judge-dependent metric is not accepted on both sides"
    }
    assert comparison["metrics"]["summary_closeness_mean"]["improved"] is None


def test_calibration_identity_difference_excludes_judge_metric() -> None:
    accepted = {
        "n": 30,
        "value": 0.9,
        "ci95": [0.8, 0.95],
        "acceptance": {"accepted": True, "reason": "covered"},
    }
    emitter = {"module": "m", "metric_name": "summary_closeness_mean", "emitter_sha256": "same"}
    common = {
        "questions_sha256": "questions",
        "subset_sha256": "subset",
        "stopped_early": False,
        "stages": {},
        "ranking": {"category_multipliers": {}},
        "judge": _judge_identity(),
        "metrics": {"summary_closeness_mean": accepted},
    }
    current = {
        **common,
        "measurement_identity": {
            "metric_emitters": {"summary_closeness_mean": emitter},
            "calibration": {
                "implementation_sha256": "judge-new",
                "thresholds": {"positive_min_mean": 80.0, "negative_max_mean": 40.0},
                "control_tasks_sha256": "controls",
            },
        },
    }
    baseline = {
        **common,
        "measurement_identity": {
            "metric_emitters": {"summary_closeness_mean": emitter},
            "calibration": {
                "implementation_sha256": "judge-old",
                "thresholds": {"positive_min_mean": 80.0, "negative_max_mean": 40.0},
                "control_tasks_sha256": "controls",
            },
        },
    }

    comparison = compare_to_baseline(current, baseline)

    assert comparison["comparable"] is False
    assert comparison["excluded_metrics"] == {
        "summary_closeness_mean": "judge calibration identity differs or is missing"
    }


def test_metric_emitter_identity_difference_excludes_only_affected_metric() -> None:
    metric = {"n": 30, "value": 0.8, "ci95": [0.7, 0.9]}
    common = {
        "questions_sha256": "questions",
        "subset_sha256": "subset",
        "stopped_early": False,
        "stages": {},
        "ranking": {"category_multipliers": {}},
        "judge": None,
        "metrics": {"ai_recall": metric},
    }
    current = {
        **common,
        "measurement_identity": {
            "metric_emitters": {
                "ai_recall": {"module": "m", "metric_name": "ai_recall", "emitter_sha256": "new"}
            }
        },
    }
    baseline = {
        **common,
        "measurement_identity": {
            "metric_emitters": {
                "ai_recall": {"module": "m", "metric_name": "ai_recall", "emitter_sha256": "old"}
            }
        },
    }

    comparison = compare_to_baseline(current, baseline)

    assert comparison["comparable"] is False
    assert comparison["excluded_metrics"] == {"ai_recall": "metric emitter identity differs or is missing"}
    assert comparison["metrics"]["ai_recall"]["accepted"] is False


def test_production_emitter_identity_changes_only_the_affected_metric(monkeypatch) -> None:
    names = ("ai_recall", "category_agreement")
    before = metric_emitter_identity(names)
    original_getsource = metrics.inspect.getsource

    def changed_getsource(symbol):
        source = original_getsource(symbol)
        return source + "\n# simulated emitter change" if symbol is metrics.ai_recall else source

    monkeypatch.setattr(metrics.inspect, "getsource", changed_getsource)
    after = metric_emitter_identity(names)
    assert {name for name in names if before[name] != after[name]} == {"ai_recall"}

    metric = {"n": 30, "value": 0.8, "ci95": [0.7, 0.9]}
    common = {
        "questions_sha256": "questions",
        "subset_sha256": "subset",
        "stopped_early": False,
        "stages": {},
        "ranking": {"category_multipliers": {}},
        "judge": None,
        "metrics": {name: metric for name in names},
    }
    comparison = compare_to_baseline(
        {**common, "measurement_identity": {"metric_emitters": after}},
        {**common, "measurement_identity": {"metric_emitters": before}},
    )
    assert comparison["comparable"] is True
    assert comparison["excluded_metrics"] == {"ai_recall": "metric emitter identity differs or is missing"}


def test_report_source_change_does_not_change_metric_emitter_identity(monkeypatch) -> None:
    names = tuple(metrics._METRIC_EMITTERS)
    before = metric_emitter_identity(names)
    original_getsource = metrics.inspect.getsource

    def changed_getsource(symbol):
        source = original_getsource(symbol)
        return source + "\n# simulated report-only change" if symbol is metrics.render_report else source

    monkeypatch.setattr(metrics.inspect, "getsource", changed_getsource)
    assert metric_emitter_identity(names) == before


def test_tag_vocabulary_behavior_change_changes_tag_emitter_identity(monkeypatch) -> None:
    tag = "new-readable-tag"
    before_behavior = metrics.is_in_v2_vocabulary(tag)
    before = metric_emitter_identity(("tag_jaccard_mean",))

    monkeypatch.setattr(
        metrics.tag_normalizer,
        "_READABLE_VOCABULARY_SET",
        metrics.tag_normalizer._READABLE_VOCABULARY_SET | {tag},
    )
    monkeypatch.setattr(metrics, "is_in_v2_vocabulary", metrics.tag_normalizer.is_in_v2_vocabulary)

    assert before_behavior is False
    assert metrics.is_in_v2_vocabulary(tag) is True
    assert metric_emitter_identity(("tag_jaccard_mean",)) != before


def test_ranking_config_behavior_change_changes_only_ranked_auc_emitter_identity(monkeypatch) -> None:
    names = ("selected_auc", "selected_auc_ranked")
    before = metric_emitter_identity(names)

    monkeypatch.setattr(curator_select, "CATEGORY_MULTIPLIERS", {"paper": 0.5})
    after = metric_emitter_identity(names)

    assert {name for name in names if before[name] != after[name]} == {"selected_auc_ranked"}


def test_report_keeps_exit_zero_when_only_judge_metrics_are_unaccepted(monkeypatch, capsys, tmp_path) -> None:
    payload = {
        "metrics": {
            "summary_closeness_mean": {
                "n": 4,
                "value": 0.9,
                "ci95": [0.8, 1.0],
                "acceptance": {"accepted": False, "reason": "judge calibration missing"},
            }
        },
        "comparison": None,
        "threshold_verdicts": {
            "summary_closeness_mean": {
                "confident": None,
                "reason": "unaccepted diagnostic: judge calibration missing",
            }
        },
        "judge_acceptance": {"accepted": False, "reason": "judge calibration missing"},
        "stopped_early": False,
    }
    monkeypatch.setattr(metrics, "compute_metrics", lambda **_kwargs: payload)
    args = argparse.Namespace(
        eval_fit_command="report",
        run=str(tmp_path / "run"),
        questions=str(tmp_path / "questions.jsonl"),
        baseline=None,
        thresholds=None,
    )

    assert run_eval_fit(args) == 0
    output = capsys.readouterr().out
    assert "thresholds: below=none undetermined=summary_closeness_mean" in output
    assert "NOT ACCEPTED: judge-dependent summary/reason metrics are diagnostic only" in output


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
