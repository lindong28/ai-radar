"""Text-judge runs reuse frozen predictions; human votes are never synthesized."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from .assets import (
    ROOT,
    archive_metrics,
    code_identity,
    create_run,
    digest,
    file_digest,
    load_dataset,
    read_json,
    read_jsonl,
    rebuild_index,
    utc_now,
    write_json,
    write_jsonl,
)
from .judge import DEFAULT_MODEL, FIELDS, calibrate, judge_identity, judge_text, prepare_calibration
from .metrics import score


def source_run(experiment: Path, root: Path):
    experiment, root = experiment.resolve(), root.resolve()
    metadata = read_json(experiment / "metadata.json")
    if metadata["target"] != "content-enrichment":
        raise ValueError("text judging requires a content-enrichment run")
    dataset = Path(metadata["dataset"])
    _, cases = load_dataset(dataset, "content-enrichment")
    if file_digest(dataset / "manifest.json") != metadata["dataset_manifest_sha256"]:
        raise ValueError("source dataset identity changed")
    selected = {c["case_id"]: c for c in cases if c["case_id"] in metadata["case_ids"]}
    run = root / "runs" / experiment.relative_to(root / "experiments")
    predictions = read_jsonl(run / "predictions.jsonl")
    receipt = read_json(run / "prediction-receipt.json")
    if receipt["sha256"] != file_digest(run / "predictions.jsonl"):
        raise ValueError("source predictions were modified")
    return metadata, list(selected.values()), predictions


def export_calibration(experiment: Path, output: Path, *, root: Path = ROOT, model: str = DEFAULT_MODEL, provider: str = "ark") -> dict:
    metadata, cases, predictions = source_run(experiment, root)
    candidates = {p["case_id"]: p["output"] for p in predictions if p["status"] == "ok"}
    # Fix news identity across fields/splits; do not inspect model judge scores to pick easy validation examples.
    eligible = [c for c in cases if c["input"] is not None and c["case_id"] in candidates
                and all(isinstance(c["reference"].get(f), str) and isinstance(candidates[c["case_id"]].get(f), str) for f in FIELDS)]
    eligible.sort(key=lambda c: c["case_id"])
    if len(eligible) < 4:
        raise ValueError(f"need four distinct paired news with all three text fields; available={len(eligible)}; no labels fabricated")
    samples = [{"sample_id": f"{field}-{case['case_id']}", "case_id": case["case_id"], "field": field,
                "split": "dev" if index < 2 else "validation", "input": case["input"],
                "reference": case["reference"][field], "candidate": candidates[case["case_id"]][field]}
               for field in FIELDS for index, case in enumerate(eligible[:4])]
    material = prepare_calibration(samples, model=model, provider=provider)
    write_json(output / "material.json", material)
    write_json(output / "labels-to-complete.json", material["labels_template"])
    write_json(output / "source.json", {"experiment": str(experiment), "predictions_identity": digest(predictions),
                                       "model": model, "provider": provider, "user_votes": 0})
    return {"material": str(output / "material.json"), "labels": str(output / "labels-to-complete.json"), "user_votes": 0}


def calibrate_file(material_path: Path, labels_path: Path, confirmed_sha: str, *, chat_factory,
                   root: Path = ROOT, model: str = DEFAULT_MODEL, provider: str = "ark") -> dict:
    from .human_store import append_batch

    # Model attempts / calibration results are run assets, not human votes.
    output, _ = create_run(root, "content-enrichment", "v1")
    result = calibrate(read_json(material_path), labels_path=labels_path, user_confirmed_sha256=confirmed_sha,
                       chat=chat_factory(output / "attempts")("calibration"), model=model, provider=provider)
    write_json(output / "material.json", read_json(material_path))
    write_json(output / "user-labels.json", read_json(labels_path))
    write_json(output / "result.json", result)
    write_json(output / "receipt.json", {"result_sha256": file_digest(output / "result.json"), "user_confirmed_sha256": confirmed_sha,
                                        "source_labels": str(labels_path.resolve()), "source_material": str(material_path.resolve())})
    append_batch(root / "human-evals/content-enrichment/reviews.json", "content-enrichment", {
        "metadata": {"batch_id": str(uuid4()), "recorded_at": utc_now(), "reviewed_at": None,
                     "feedback_exported_at": None, "source_run": str(output.resolve()),
                     "kind": "judge-calibration", "user_authority": "user-confirmed labels SHA",
                     "user_confirmed_sha256": confirmed_sha},
        "data": {"feedback_raw": labels_path.read_bytes().decode("utf-8"),
                 "material": read_json(material_path), "result": result, "annotations": []},
    })
    return {"status": result["status"], "result": str(output / "result.json")}


def load_calibration(directory: Path, *, model: str, provider: str):
    result = read_json(directory / "result.json")
    receipt = read_json(directory / "receipt.json")
    if file_digest(directory / "result.json") != receipt["result_sha256"]:
        raise ValueError("calibration result integrity mismatch")
    if file_digest(Path(receipt["source_labels"])) != receipt["user_confirmed_sha256"]:
        raise ValueError("confirmed user-label bytes changed or unavailable")
    material = prepare_calibration(read_json(directory / "material.json")["samples"], model=model, provider=provider)
    if result["judge_identity"] != judge_identity(model, provider=provider) or result["material_sha256"] != material["material_sha256"]:
        raise ValueError("calibration belongs to another judge or question set")
    if result["status"] != "passed" or len(result["validation"]) != 6 or not all(r["correct"] for r in result["validation"]):
        raise ValueError("calibration validation has not passed")
    return result["calibration"]


def judge_run(experiment: Path, *, chat_factory, root: Path = ROOT, model: str = DEFAULT_MODEL, provider: str = "ark",
              calibration: Path | None = None, workers: int = 8) -> dict:
    if not 1 <= workers <= 32:
        raise ValueError("workers must be between 1 and 32")
    metadata, cases, predictions = source_run(experiment, root)
    accepted = load_calibration(calibration, model=model, provider=provider) if calibration else None
    run, destination = create_run(root, "content-enrichment", metadata["version"])
    factory = chat_factory(run / "attempts")
    pred_map = {p["case_id"]: p for p in predictions}
    questions = [(case, field) for case in cases if case["input"] is not None
                 for field in FIELDS if isinstance(case["reference"].get(field), str)
                 and pred_map.get(case["case_id"], {}).get("status") == "ok"
                 and isinstance(pred_map[case["case_id"]]["output"].get(field), str)]
    start = utc_now()
    write_json(run / "started.json", {"source_experiment": str(experiment), "judge_identity": judge_identity(model, provider=provider),
                                      "question_count": len(questions), "started_at": start})

    def apply(question):
        case, field = question
        value = judge_text(case["case_id"], field, case["input"], case["reference"][field],
                           pred_map[case["case_id"]]["output"][field], chat=factory(case["case_id"]), model=model, provider=provider)
        write_json(run / "judgments" / f"{case['case_id']}-{field}.json", value)
        return value

    with ThreadPoolExecutor(max_workers=workers) as pool:
        judgments = list(pool.map(apply, questions))
    result = score("O3", cases, predictions, judgments=judgments, calibration=accepted)
    write_jsonl(run / "predictions.jsonl", predictions)
    write_json(run / "prediction-receipt.json", {"sha256": file_digest(run / "predictions.jsonl")})
    write_jsonl(run / "judgments.jsonl", judgments)
    archive_metrics(root, run, destination, result, {**metadata, "source_experiment": str(experiment), "label": "text-judged",
        "judge_identity": judge_identity(model, provider=provider), "calibration": str(calibration) if calibration else None,
        "calibration_identity": digest(read_json(calibration / "receipt.json")) if calibration else None,
        "started_at": start, "ended_at": utc_now(), "scorer_identity": code_identity(root),
        "timing": {"configured_workers": workers, "observed_peak_workers": None, "work_unit": "text field", "elapsed_seconds": None},
        "status": "complete" if result["complete"] else "incomplete", "text_calibration": "passed" if accepted else "not_trusted"})
    rebuild_index(root)
    return {"experiment": str(destination), "metrics": result["metrics"], "calibration": "passed" if accepted else "not_trusted"}
