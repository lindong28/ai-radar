"""Fixed, hand-computable controls for the four evaluation objects."""

import pytest

from evals._shared.metrics import score, spearman


@pytest.mark.parametrize("gold,values,expected", [
    ([10, 20, 30], [30, 40, 50], 1),
    ([10, 20, 30], [50, 40, 30], -1),
    ([1, 2, 3, 4, 5], [5, 6, 7, 8, 7], 0.8207826816681233),
    ([10, 10, 30, 40], [10, 30, 30, 40], 5 / 6),
    ([10, 20, 30, 40], [20, 40, 10, 30], 0),
    ([10, 20], [40, 30], -1),
])
def test_spearman_average_ties_and_order_not_distance(gold, values, expected):
    assert spearman(list(zip(gold, values))) == pytest.approx(expected)
    cases = [case(str(i), {"score": value}) for i, value in enumerate(gold)]
    predictions = [prediction(str(i), {"score": value}) for i, value in enumerate(values)]
    result = score("O2", cases, predictions)
    assert result["metrics"]["spearman"]["value"] == pytest.approx(expected)
    assert result["metrics"]["spearman"]["denominator"] == len(gold)


@pytest.mark.parametrize("gold,values,reason", [
    ([], [], "no eligible cases"),
    ([20], [30], "fewer than two eligible cases"),
    ([20, 20], [30, 40], "constant reference or prediction scores"),
    ([20, 30], [40, 40], "constant reference or prediction scores"),
])
def test_spearman_undefined_does_not_become_zero(gold, values, reason):
    cases = [case(str(i), {"score": value}) for i, value in enumerate(gold)]
    predictions = [prediction(str(i), {"score": value}) for i, value in enumerate(values)]
    result = score("O2", cases, predictions)
    assert result["metrics"]["spearman"] == {
        "value": None, "status": "not_computed", "denominator": len(gold), "reason": reason,
    }
    assert result["complete"] == bool(gold)  # Complete predictions do not imply defined rank correlation.


@pytest.mark.parametrize("missing", [True, False])
def test_spearman_keeps_mae_eligibility_and_incomplete_denominator(missing):
    cases = [case("a", {"score": 20}), case("b", {"score": 40}),
             case("c", {}), case("d", {"score": 80}, raw=False)]
    predictions = [prediction("a", {"score": 30})]
    if not missing:
        predictions.append(prediction("b", {"score": float("nan")}))
    result = score("O2", cases, predictions)
    for metric in result["metrics"].values():
        assert metric["value"] is None and metric["denominator"] == 2
        assert metric["reason"] == "incomplete predictions"
    result = score("O2", cases, [prediction("a", {"score": 30}), prediction("b", {"score": 50})])
    assert result["metrics"]["mae"]["value"] == 10
    assert result["metrics"]["spearman"]["value"] == 1


def case(key, reference, *, raw=True):
    return {"case_id": key, "input": {"text": key} if raw else None, "reference": reference, "split": "regression"}


def prediction(key, output, *, status="ok"):
    return {"case_id": key, "status": status, "output": output}


@pytest.mark.parametrize("target,field", [("O1", "member"), ("O4", "featured")])
def test_membership_good_bad_and_missing_raw(target, field):
    cases = [case("a", {field: True}), case("b", {field: False}), case("c", {field: True}, raw=False)]
    good = score(target, cases, [prediction("a", {field: True}), prediction("b", {field: False})])
    bad = score(target, cases, [prediction("a", {field: False}), prediction("b", {field: True})])
    assert good["metrics"]["precision"]["value"] == 1
    assert good["metrics"]["recall"]["value"] == 0.5
    assert good["metrics"]["recall"]["denominator"] == 2
    assert good["counts"]["missing_input"] == 1
    assert good["per_case"][2]["status"] == "reference_only"
    assert bad["metrics"]["precision"]["value"] == 0
    assert bad["metrics"]["recall"]["value"] == 0


@pytest.mark.parametrize("target,field", [("O1", "member"), ("O4", "featured")])
@pytest.mark.parametrize("output,status", [({}, "ok"), ({"placeholder": True}, "error"), (None, "ok")])
def test_membership_missing_required_is_incomplete(target, field, output, status):
    result = score(target, [case("a", {field: True})], [prediction("a", output, status=status)])
    assert result["complete"] is False
    assert all(metric["value"] is None for metric in result["metrics"].values())


@pytest.mark.parametrize("target,field", [("O1", "member"), ("O4", "featured")])
@pytest.mark.parametrize("value", [1, "true", [], None])
def test_membership_does_not_coerce_truthy_values(target, field, value):
    result = score(target, [case("a", {field: True})], [prediction("a", {field: value})])
    assert result["complete"] is False
    assert result["metrics"]["precision"]["value"] is None


@pytest.mark.parametrize("target,field", [("O1", "member"), ("O4", "featured")])
def test_membership_zero_denominators_and_no_predictions(target, field):
    result = score(target, [case("a", {field: False})], [prediction("a", {field: False})])
    assert result["complete"] is True
    assert all(metric["value"] is None for metric in result["metrics"].values())
    missing = score(target, [case("a", {field: True})], [])
    assert missing["complete"] is False
    assert missing["metrics"]["recall"]["value"] is None


def test_mae_good_bad_on_visible_scale_and_exclusions():
    cases = [case("a", {"score": 90}), case("b", {"score": 20}), case("c", {}), case("d", {"score": 50}, raw=False)]
    good = score("O2", cases, [prediction("a", {"score": 90}), prediction("b", {"score": 20})])
    bad = score("O2", cases, [prediction("a", {"score": 70}), prediction("b", {"score": 50})])
    assert good["metrics"]["mae"]["value"] == 0
    assert bad["metrics"]["mae"]["value"] == 25
    assert bad["metrics"]["mae"]["denominator"] == 2


@pytest.mark.parametrize("value", [True, False, "50", float("nan"), float("inf"), -1, 101, None])
def test_mae_invalid_prediction_never_becomes_zero_or_dropped(value):
    cases = [case("a", {"score": 90}), case("b", {"score": 20})]
    result = score("O2", cases, [prediction("a", {"score": 90}), prediction("b", {"score": value})])
    assert result["metrics"]["mae"]["value"] is None
    assert result["metrics"]["mae"]["denominator"] == 2
    assert result["complete"] is False
    with pytest.raises(ValueError, match="invalid reference score"):
        score("O2", [case("a", {"score": value})], [])


def test_mae_error_and_missing_predictions_keep_denominator():
    cases = [case("a", {"score": 90}), case("b", {"score": 20})]
    for predictions in (
        [prediction("a", {"score": 90})],
        [prediction("a", {"score": 90}), prediction("b", {"score": 20}, status="error")],
    ):
        result = score("O2", cases, predictions)
        assert result["metrics"]["mae"]["value"] is None
        assert result["metrics"]["mae"]["denominator"] == 2


def test_o3_independent_fields_exact_set_and_empty_tags():
    cases = [
        case("a", {"category": "model", "tags": ["x", "y"]}),
        case("b", {"tags": []}),
        case("c", {"category": "paper"}),
        case("d", {"tags": []}, raw=False),
    ]
    good = score(
        "O3",
        cases,
        [
            prediction("a", {"category": "model", "tags": ["y", "x", "x"]}),
            prediction("b", {"tags": []}),
            prediction("c", {"category": "paper"}),
        ],
    )
    assert good["metrics"]["category_accuracy"]["value"] == 1
    assert good["metrics"]["tags_exact_set_accuracy"]["value"] == 1
    assert good["metrics"]["tags_exact_set_accuracy"]["denominator"] == 2
    bad = score(
        "O3",
        cases,
        [
            prediction("a", {"category": "paper", "tags": ["x", "y", "extra"]}),
            prediction("b", {}),
            prediction("c", {"category": "paper"}),
        ],
    )
    assert bad["metrics"]["category_accuracy"]["value"] == 0.5
    assert bad["metrics"]["tags_exact_set_accuracy"]["value"] == 0
    assert bad["complete"] is False


def test_o3_failed_prediction_with_empty_reference_tags_is_wrong():
    result = score("O3", [case("a", {"tags": []})], [prediction("a", {"tags": []}, status="error")])
    assert result["metrics"]["tags_exact_set_accuracy"]["value"] == 0
    assert result["complete"] is False


def calibrated():
    return {
        "status": "passed",
        "judge_identity": "judge-v1",
        "fields": ["title", "summary", "reason"],
        "provenance": "user",
        "validation_correct": 6,
        "validation_total": 6,
        "validation_case_ids": [f"validation-{index}" for index in range(6)],
    }


def judge(key, field, value):
    return {"case_id": key, "field": field, "status": "ok", "score": value, "judge_identity": "judge-v1"}


@pytest.mark.parametrize("field", ["title", "summary", "reason"])
def test_text_judge_good_bad_and_calibration(field):
    cases = [case("a", {field: "reference a"}), case("b", {field: "reference b"})]
    predictions = [prediction("a", {field: "candidate a"}), prediction("b", {field: "candidate b"})]
    judgments = [judge("a", field, 2), judge("b", field, 1)]
    untrusted = score("O3", cases, predictions, judgments=judgments)
    trusted = score("O3", cases, predictions, judgments=judgments, calibration=calibrated())
    bad = score(
        "O3", cases, predictions, judgments=[judge("a", field, 0), judge("b", field, 0)], calibration=calibrated()
    )
    metric = f"{field}_mean_score"
    assert untrusted["metrics"][metric]["status"] == "untrusted"
    assert trusted["metrics"][metric]["status"] == "trusted"
    assert trusted["metrics"][metric]["value"] == 1.5
    assert bad["metrics"][metric]["value"] == 0


@pytest.mark.parametrize("value", [None, True, 3, -1, 1.0, "2"])
def test_missing_or_invalid_judgment_does_not_fill_zero(value):
    result = score(
        "O3",
        [case("a", {"title": "a"})],
        [prediction("a", {"title": "b"})],
        judgments=[judge("a", "title", value)],
        calibration=calibrated(),
    )
    assert result["metrics"]["title_mean_score"]["value"] is None
    assert result["complete"] is False


@pytest.mark.parametrize(
    "receipt",
    [
        True,
        {"status": "passed"},
        {**calibrated(), "fields": ["summary"]},
        {**calibrated(), "judge_identity": "other"},
        {**calibrated(), "validation_correct": True},
        {**calibrated(), "provenance": "model"},
    ],
)
def test_bare_or_mismatched_calibration_is_untrusted(receipt):
    result = score(
        "O3",
        [case("a", {"title": "a"})],
        [prediction("a", {"title": "b"})],
        judgments=[judge("a", "title", 2)],
        calibration=receipt,
    )
    assert result["metrics"]["title_mean_score"]["status"] == "untrusted"


@pytest.mark.parametrize("target", ["O1", "O2", "O3", "O4"])
def test_empty_cases_are_not_full_credit(target):
    result = score(target, [], [])
    assert result["complete"] is False
    assert all(metric["value"] is None for metric in result["metrics"].values())


def test_duplicate_unknown_and_non_executable_predictions_rejected():
    item = case("a", {"member": True})
    with pytest.raises(ValueError, match="duplicate"):
        score("O1", [item, item], [])
    pred = prediction("a", {"member": True})
    with pytest.raises(ValueError, match="duplicate"):
        score("O1", [item], [pred, pred])
    with pytest.raises(ValueError, match="unknown"):
        score("O1", [item], [prediction("b", {"member": True})])
    with pytest.raises(ValueError, match="non-executable"):
        score("O1", [case("a", {"member": True}, raw=False)], [pred])


def test_missing_judgment_and_failed_text_prediction_remain_incomplete():
    cases = [case("a", {"title": "a"})]
    for predictions, judgments in [
        ([prediction("a", {"title": "b"})], []),
        ([prediction("a", {})], [judge("a", "title", 2)]),
    ]:
        result = score("O3", cases, predictions, judgments=judgments, calibration=calibrated())
        assert result["metrics"]["title_mean_score"]["value"] is None
        assert result["complete"] is False
