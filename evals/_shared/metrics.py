"""Pure scoring of fixed cases; no model calls or production data access.

Calibration is an identity-checked receipt supplied by the judge integration,
not a boolean switch. The integration must verify its provenance before calling
this module; matching strings here cannot authenticate an external receipt.
"""

from __future__ import annotations

import math


def _number(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 100


def _valid(field: str, value: object) -> bool:
    if field in ("member", "featured"):
        return type(value) is bool
    if field == "score":
        return _number(value)
    if field == "tags":
        return isinstance(value, list) and all(isinstance(tag, str) for tag in value)
    return isinstance(value, str)


def _index(rows: list[dict], label: str) -> dict:
    indexed = {}
    for row in rows:
        key = row.get("case_id")
        if not isinstance(key, str) or not key or key in indexed:
            raise ValueError(f"{label}: missing or duplicate case_id")
        indexed[key] = row
    return indexed


def _metric(value, denominator: int, reason: str = "", *, trusted: bool = True) -> dict:
    return {
        "value": value,
        "status": "not_computed" if value is None else "trusted" if trusted else "untrusted",
        "reason": reason,
        "denominator": denominator,
    }


def score(
    target: str,
    cases: list[dict],
    predictions: list[dict],
    *,
    judgments: list[dict] | None = None,
    calibration: dict | None = None,
) -> dict:
    """Score O1–O4 using final visible outputs on the declared 0–100 scale.

    Judgment rows carry case_id, field, status, score (integer 0/1/2), and
    judge_identity. A calibration receipt carries status='passed', a nonempty
    judge_identity, calibrated fields, provenance='user', six distinct
    validation_case_ids, and validation_correct=validation_total=6.
    """
    if target not in ("O1", "O2", "O3", "O4"):
        raise ValueError("unknown target")
    case_map = _index(cases, "cases")
    pred_map = _index(predictions, "predictions")
    fields = {
        "O1": ("member",),
        "O2": ("score",),
        "O3": ("category", "tags", "title", "summary", "reason"),
        "O4": ("featured",),
    }[target]
    for case in cases:
        if "input" not in case or (case["input"] is not None and not isinstance(case["input"], dict)):
            raise ValueError("case input must be a dict or explicit None")
        if not isinstance(case.get("reference"), dict):
            raise ValueError("case reference must be a dict")
        for field in fields:
            if field in case["reference"] and not _valid(field, case["reference"][field]):
                raise ValueError(f"invalid reference {field}")
        if target in ("O1", "O4") and fields[0] not in case["reference"]:
            raise ValueError("membership reference required for every case")
    for key in pred_map:
        if key not in case_map or case_map[key]["input"] is None:
            raise ValueError("prediction for unknown or non-executable case")

    judge_map = {}
    for judgment in judgments or []:
        key = (judgment.get("case_id"), judgment.get("field"))
        if key in judge_map:
            raise ValueError("duplicate judgment")
        if key[0] not in case_map or key[1] not in ("title", "summary", "reason"):
            raise ValueError("judgment for unknown case or field")
        case = case_map[key[0]]
        if target != "O3" or case["input"] is None or key[1] not in case["reference"]:
            raise ValueError("judgment for non-executable field")
        judge_map[key] = judgment

    per_case = []
    for case in cases:
        key = case["case_id"]
        prediction = pred_map.get(key)
        output = prediction.get("output") if prediction and prediction.get("status") == "ok" else None
        required = [field for field in fields if field in case["reference"]]
        errors = []
        if case["input"] is not None and required:
            if not isinstance(output, dict):
                errors.append("prediction missing or failed")
                output = {}
            errors.extend(f"missing or invalid {field}" for field in required if not _valid(field, output.get(field)))
        per_case.append(
            {
                "case_id": key,
                "split": case.get("split"),
                "status": "reference_only" if case["input"] is None else "error" if errors else "ok",
                "errors": errors,
                "fields": {},
            }
        )
    traces = {row["case_id"]: row for row in per_case}
    metrics = {}
    complete = bool(cases) and not any(row["errors"] for row in per_case)

    def candidate(case: dict, field: str):
        prediction = pred_map.get(case["case_id"], {})
        output = prediction.get("output")
        if prediction.get("status") == "ok" and isinstance(output, dict) and _valid(field, output.get(field)):
            return output[field], True
        return None, False

    if target in ("O1", "O4"):
        field = fields[0]
        reference = {case["case_id"] for case in cases if case["reference"][field] is True}
        selected = set()
        for case in cases:
            value, valid = candidate(case, field)
            if valid and value is True:
                selected.add(case["case_id"])
            traces[case["case_id"]]["fields"][field] = {
                "reference": case["reference"][field],
                "prediction": value,
                "valid": valid,
                "missing_input": case["input"] is None,
            }
        intersection = len(reference & selected)
        for name, denominator in (("precision", len(selected)), ("recall", len(reference))):
            reason = "incomplete predictions" if not complete else "zero denominator" if not denominator else ""
            metrics[name] = _metric(
                intersection / denominator if complete and denominator else None, denominator, reason
            )
    elif target == "O2":
        eligible = [case for case in cases if case["input"] is not None and "score" in case["reference"]]
        errors = []
        for case in eligible:
            value, valid = candidate(case, "score")
            error = abs(value - case["reference"]["score"]) if valid else None
            traces[case["case_id"]]["fields"]["score"] = {"absolute_error": error, "prediction": value}
            if valid:
                errors.append(error)
        complete = bool(eligible) and len(errors) == len(eligible)
        metrics["mae"] = _metric(
            sum(errors) / len(eligible) if complete else None,
            len(eligible),
            "" if complete else "no eligible cases" if not eligible else "incomplete predictions",
        )
    else:
        any_eligible = False
        for field in fields:
            eligible = [case for case in cases if case["input"] is not None and field in case["reference"]]
            any_eligible |= bool(eligible)
            values = []
            identities = []
            for case in eligible:
                value, valid = candidate(case, field)
                result = None
                if field in ("category", "tags"):
                    result = int(
                        valid
                        and (
                            set(value) == set(case["reference"][field])
                            if field == "tags"
                            else value == case["reference"][field]
                        )
                    )
                elif valid:
                    judgment = judge_map.get((case["case_id"], field), {})
                    judged_score = judgment.get("score")
                    if judgment.get("status") == "ok" and type(judged_score) is int and judged_score in (0, 1, 2):
                        result = judged_score
                        identities.append(judgment.get("judge_identity"))
                if result is not None:
                    values.append(result)
                if result is None or not valid:
                    complete = False
                if result is None and valid:
                    trace = traces[case["case_id"]]
                    trace["errors"].append(f"missing or invalid {field} judgment")
                    trace["status"] = "error"
                traces[case["case_id"]]["fields"][field] = {"value": result, "prediction_valid": valid}
            name = {"category": "category_accuracy", "tags": "tags_exact_set_accuracy"}.get(
                field, f"{field}_mean_score"
            )
            computed = bool(eligible) and len(values) == len(eligible)
            calibrated = field in ("category", "tags") or (
                isinstance(calibration, dict)
                and calibration.get("status") == "passed"
                and isinstance(calibration.get("judge_identity"), str)
                and bool(calibration["judge_identity"])
                and isinstance(calibration.get("fields"), list)
                and field in calibration["fields"]
                and calibration.get("provenance") == "user"
                and type(calibration.get("validation_correct")) is int
                and calibration["validation_correct"] == 6
                and type(calibration.get("validation_total")) is int
                and calibration["validation_total"] == 6
                and isinstance(calibration.get("validation_case_ids"), list)
                and len(calibration["validation_case_ids"]) == 6
                and all(isinstance(key, str) and key for key in calibration["validation_case_ids"])
                and len(set(calibration["validation_case_ids"])) == 6
                and all(identity == calibration["judge_identity"] for identity in identities)
            )
            reason = (
                "no eligible cases"
                if not eligible
                else "incomplete judgments or predictions"
                if not computed
                else ""
                if calibrated
                else "calibration missing or identity/field mismatch"
            )
            metrics[name] = _metric(
                sum(values) / len(eligible) if computed else None, len(eligible), reason, trusted=calibrated
            )
        complete &= any_eligible
    return {
        "metrics": metrics,
        "counts": {
            "cases": len(cases),
            "executable": sum(case["input"] is not None for case in cases),
            "missing_input": sum(case["input"] is None for case in cases),
            "predictions": len(predictions),
            "failed_cases": sum(bool(row["errors"]) for row in per_case),
        },
        "per_case": per_case,
        "complete": complete,
    }
