"""LLM judge via Gateway for title / summary / reason closeness to AIHOT."""

from __future__ import annotations

import hashlib
import inspect
import json
import statistics
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from ...provider.deepseek_chat import chat_json
from ...provider.llm_gateway import gateway_base_url
from . import judge_prompts
from .common import (
    DEFAULT_WORKERS,
    JUDGE_SCHEMA_VERSION,
    REFERENCE_FIELD,
    is_stop_signal,
    isolate_side_effects,
    json_dumps,
    load_questions_bytes,
    read_jsonl,
    read_jsonl_bytes,
    redact,
    require_gateway,
    sha256_file,
    sha256_text,
    utc_now,
    write_json,
    write_jsonl,
)
from .governance import (
    DEFAULT_IDENTITY_MANIFEST_DIR,
    DEFAULT_LEDGER_PATH,
    IdentityRejected,
    record_event,
    run_identity_preflight,
    start_attempt,
)

DEFAULT_JUDGE_MODEL = "deepseek-v4-flash"
JUDGE_TEMPERATURE = 0.0
JUDGE_MAX_TOKENS = 512
DIMENSIONS: tuple[str, ...] = ("title", "summary", "reason")
DEFAULT_DIMENSIONS: tuple[str, ...] = ("summary", "reason")
CALIBRATED_DIMENSIONS: tuple[str, ...] = ("summary", "reason")
POSITIVE_MIN_MEAN = 80.0
NEGATIVE_MAX_MEAN = 40.0
_JUDGE_ARK_MODEL_ENV = "AI_RADAR_ARK_FIT_JUDGE_MODEL"


_CANDIDATE_FIELD = {"title": "title_zh", "summary": "summary_zh", "reason": "why_recommend"}


def judge_identity(model: str, dimensions: tuple[str, ...] = DEFAULT_DIMENSIONS) -> dict[str, Any]:
    provider_file = inspect.getsourcefile(chat_json)
    return {
        "schema_version": JUDGE_SCHEMA_VERSION,
        "requested_model": model,
        "model": model,
        "temperature": JUDGE_TEMPERATURE,
        "max_tokens": JUDGE_MAX_TOKENS,
        "prompt_sha256": {dimension: judge_prompts.prompt_sha256(dimension) for dimension in dimensions},
        "gateway_base_url": gateway_base_url(),
        "provider_module_sha256": sha256_file(Path(provider_file)) if provider_file else None,
        "usage_recorded": False,
    }


def _condition_identity(identity: dict[str, Any], readings: list[dict[str, Any]]) -> dict[str, Any]:
    providers = sorted(
        {str(raw["provider"]) for row in readings if isinstance((raw := row.get("raw")), dict) and raw.get("provider")}
    )
    models = sorted(
        {str(raw["model"]) for row in readings if isinstance((raw := row.get("raw")), dict) and raw.get("model")}
    )
    return {**identity, "served_providers": providers, "served_models": models}


def judge_once(
    *, model: str, dimension: str, title: str, content: str, reference: str, candidate: str
) -> dict[str, Any]:
    user = judge_prompts.render_user(title=title, content=content, reference=reference, candidate=candidate)
    result = chat_json(
        system=judge_prompts.system_prompt(dimension),
        user=user,
        default_model=model,
        model_env=None,  # Explicit run identity wins without mutating process-wide environment.
        ark_model_env=_JUDGE_ARK_MODEL_ENV,
        temperature=JUDGE_TEMPERATURE,
        max_tokens=JUDGE_MAX_TOKENS,
        stage=None,
    )
    payload = result.json
    raw_closeness = payload.get("closeness")
    try:
        closeness = int(round(float(raw_closeness)))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"judge returned non-numeric closeness: {raw_closeness!r}") from exc
    closeness = max(0, min(100, closeness))
    return {
        "closeness": closeness,
        "rationale": str(payload.get("rationale", "")),
        "raw": {"provider": result.provider, "model": result.model, "json": payload,
                "llm_gateway": result.gateway},
    }


def _candidate_text(row: dict[str, Any], dimension: str) -> str | None:
    enrich = row.get("enrich")
    if not isinstance(enrich, dict) or enrich.get("error") or not isinstance(enrich.get("output"), dict):
        return None
    value = enrich["output"].get(_CANDIDATE_FIELD[dimension])
    return str(value) if value else None


def _reference_text(question: dict[str, Any], dimension: str) -> str | None:
    value = question["reference"].get(REFERENCE_FIELD[dimension])
    return str(value) if value else None


class _Judge:
    def __init__(self, model: str, workers: int) -> None:
        self.model = model
        self.workers = workers
        self.stop = threading.Event()
        self.stop_reasons: list[str] = []
        self._lock = threading.Lock()

    def _task(self, task: dict[str, Any]) -> dict[str, Any]:
        base = {key: task[key] for key in ("question_id", "dimension") if key in task}
        base.update({key: task[key] for key in ("control",) if key in task})
        if self.stop.is_set():
            return {**base, "closeness": None, "rationale": None, "raw": None, "error": None, "skipped": "stopped"}
        try:
            verdict = judge_once(
                model=self.model,
                dimension=task["dimension"],
                title=task["title"],
                content=task["content"],
                reference=task["reference"],
                candidate=task["candidate"],
            )
            return {**base, **verdict, "error": None}
        except Exception as exc:
            error = redact(f"{type(exc).__name__}: {exc}")
            if is_stop_signal(error) or "cash signal" in error:
                with self._lock:
                    self.stop_reasons.append(error)
                self.stop.set()
            return {**base, "closeness": None, "rationale": None, "raw": None, "error": error}

    def run(self, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not tasks:
            return []
        results: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=max(1, min(self.workers, len(tasks)))) as executor:
            futures = [executor.submit(self._task, task) for task in tasks]
            for future in as_completed(futures):
                results.append(future.result())
        results.sort(key=lambda row: (str(row.get("question_id")), str(row.get("dimension")), str(row.get("control"))))
        return results


def _judgeable(
    questions: dict[str, dict[str, Any]], rows: list[dict[str, Any]], dimension: str
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    tasks: list[dict[str, Any]] = []
    skipped = {"no_reference": 0, "no_candidate": 0}
    for row in rows:
        question = questions.get(str(row["question_id"]))
        if question is None:
            continue
        reference = _reference_text(question, dimension)
        if reference is None:
            skipped["no_reference"] += 1
            continue
        candidate = _candidate_text(row, dimension)
        if candidate is None:
            skipped["no_candidate"] += 1
            continue
        tasks.append(
            {
                "question_id": question["question_id"],
                "dimension": dimension,
                "title": question["input"]["title"],
                "content": question["input"].get("content_text") or "",
                "reference": reference,
                "candidate": candidate,
            }
        )
    return tasks, skipped


def _calibration_tasks(
    questions: dict[str, dict[str, Any]], rows: list[dict[str, Any]], count: int
) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for dimension in CALIBRATED_DIMENSIONS:
        eligible = [
            questions[str(row["question_id"])]
            for row in rows
            if str(row["question_id"]) in questions and _reference_text(questions[str(row["question_id"])], dimension)
        ][:count]
        if len(eligible) < 2:
            continue
        for index, question in enumerate(eligible):
            reference = _reference_text(question, dimension) or ""
            other = eligible[(index + 1) % len(eligible)]
            common = {
                "question_id": question["question_id"],
                "dimension": dimension,
                "title": question["input"]["title"],
                "content": question["input"].get("content_text") or "",
                "reference": reference,
            }
            tasks.append({**common, "control": "positive", "candidate": reference})
            tasks.append({**common, "control": "negative", "candidate": _reference_text(other, dimension) or ""})
    return tasks


def _control_means(results: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for dimension in DIMENSIONS:
        for control in ("positive", "negative"):
            values = [
                int(row["closeness"])
                for row in results
                if row.get("dimension") == dimension
                and row.get("control") == control
                and row.get("closeness") is not None
            ]
            summary[f"{dimension}_{control}"] = {
                "n": len(values),
                "mean": round(statistics.fmean(values), 2) if values else None,
            }
    return summary


def _run_judge_impl(
    *,
    run_dir: Path,
    questions_path: Path,
    model: str = DEFAULT_JUDGE_MODEL,
    limit: int | None = None,
    calibrate: int | None = None,
    workers: int = DEFAULT_WORKERS,
    identity_receipt: dict[str, Any] | None = None,
    dimensions: tuple[str, ...] = DEFAULT_DIMENSIONS,
    questions_snapshot: dict[str, dict[str, Any]],
    rows_snapshot: list[dict[str, Any]],
    run_meta_snapshot: dict[str, Any],
) -> dict[str, Any]:
    credentials = require_gateway()
    side_effects = isolate_side_effects()
    questions = dict(questions_snapshot)
    rows = sorted(rows_snapshot, key=lambda row: str(row["question_id"]))
    if limit is not None:
        rows = rows[:limit]
    run_meta = dict(run_meta_snapshot)

    identity_dimensions = tuple(dict.fromkeys((*dimensions, *(CALIBRATED_DIMENSIONS if calibrate else ()))))
    identity = judge_identity(model, identity_dimensions)
    judge = _Judge(model, workers)
    started_at = utc_now()

    calibration: dict[str, Any] | None = None
    if calibrate:
        control_tasks = _calibration_tasks(questions, rows, calibrate)
        control_results = judge.run(control_tasks)
        means = _control_means(control_results)
        verdicts: dict[str, Any] = {}
        for dimension in CALIBRATED_DIMENSIONS:
            positive = means[f"{dimension}_positive"]["mean"]
            negative = means[f"{dimension}_negative"]["mean"]
            # None, not False, when the controls never ran: a dimension with fewer than two
            # eligible questions produces no control at all, and reporting that as "scale not
            # ok" sends the reader after the judge when the problem is the sample. Only 79 of
            # 2730 questions carry a reference reason, so an unfiltered --limit 20 run hits
            # this by default -- use `run --require-reference reason` to get a judgeable pool.
            ran = positive is not None and negative is not None
            verdicts[dimension] = {
                "scale_ok": (positive >= POSITIVE_MIN_MEAN and negative <= NEGATIVE_MAX_MEAN) if ran else None,
                "controls_ran": ran,
                "rule": f"positive mean >= {POSITIVE_MIN_MEAN} and negative mean <= {NEGATIVE_MAX_MEAN}",
            }
        calibration = {
            "requested": calibrate,
            "means": means,
            "verdicts": verdicts,
            "scale_ok": None
            if not verdicts or any(verdict["scale_ok"] is None for verdict in verdicts.values())
            else all(verdict["scale_ok"] for verdict in verdicts.values()),
            "readings": control_results,
            "identity": _condition_identity(identity, control_results),
            "calibration_identity": {
                "implementation_sha256": sha256_file(Path(__file__)),
                "thresholds": {
                    "positive_min_mean": POSITIVE_MIN_MEAN,
                    "negative_max_mean": NEGATIVE_MAX_MEAN,
                },
                "control_tasks_sha256": sha256_text(json_dumps(control_tasks)),
            },
        }
        write_json(run_dir / "judge-calibration.json", calibration)

    skipped: dict[str, dict[str, int]] = {}
    tasks: list[dict[str, Any]] = []
    for dimension in dimensions:
        dimension_tasks, dimension_skipped = _judgeable(questions, rows, dimension)
        tasks.extend(dimension_tasks)
        skipped[dimension] = dimension_skipped
    judgments = judge.run(tasks) if not judge.stop.is_set() else []
    # Merge instead of overwrite: a rerun that stopped early (quota) or was scoped with
    # --limit would otherwise wipe or truncate readings that cost real calls to produce,
    # and the recomputed metrics would silently use the smaller set.
    judgments_path = run_dir / "judgments.jsonl"
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    if judgments_path.exists():
        for previous in read_jsonl(judgments_path):
            merged[(str(previous["question_id"]), str(previous["dimension"]))] = previous
    # A task whose stop event fires mid-flight returns closeness=None, so an unconditional
    # merge lets a quota-interrupted rerun blank the very readings the merge was added to
    # protect. Keep the scored row unless the new one is scored too.
    replaced = 0
    discarded_blank = 0
    for row in judgments:
        key = (str(row["question_id"]), str(row["dimension"]))
        previous = merged.get(key)
        if previous is not None and previous.get("closeness") is not None and row.get("closeness") is None:
            discarded_blank += 1
            continue
        replaced += previous is not None
        merged[key] = row
    write_jsonl(judgments_path, [merged[key] for key in sorted(merged)])

    judge_json = {
        **_condition_identity(identity, list(merged.values())),
        "identity_preflight": identity_receipt,
        "run_id": run_meta.get("run_id"),
        "questions_sha256": run_meta.get("questions_sha256"),
        "started_at": started_at,
        "finished_at": utc_now(),
        "limit": limit,
        "workers": workers,
        "dimensions": list(dimensions),
        "credentials": credentials,
        "side_effects": side_effects,
        "judged": {
            dimension: sum(1 for row in judgments if row["dimension"] == dimension and row.get("closeness") is not None)
            for dimension in DIMENSIONS
        },
        "errors": sum(1 for row in judgments if row.get("error")),
        "judgments_total_on_disk": len(merged),
        "judgments_replaced_this_call": replaced,
        "judgments_blank_discarded_this_call": discarded_blank,
        "judgments_sha256": sha256_file(judgments_path),
        "calibration_sha256": sha256_file(run_dir / "judge-calibration.json") if calibration is not None else None,
        "tasks_this_call_sha256": sha256_text(json_dumps(tasks)),
        "skipped": skipped,
        "stopped_early": judge.stop.is_set(),
        "stop_reasons": judge.stop_reasons,
        "calibration_summary": None
        if calibration is None
        else {"means": calibration["means"], "scale_ok": calibration["scale_ok"]},
    }
    write_json(run_dir / "judge.json", judge_json)
    return judge_json


def planned_judge_behavior_identity(
    *,
    run_dir: Path,
    questions_path: Path,
    model: str,
    limit: int | None,
    calibrate: int | None,
    dimensions: tuple[str, ...] = DEFAULT_DIMENSIONS,
    run_digest: str | None = None,
    outputs_digest: str | None = None,
    questions_digest: str | None = None,
) -> dict[str, Any]:
    """Capture judge behavior without reading provider credentials or issuing a request."""
    return {
        "schema_version": "aihot-fit-judge-behavior-v1",
        "run_sha256": run_digest or sha256_file(run_dir / "run.json"),
        "outputs_sha256": outputs_digest or sha256_file(run_dir / "outputs.jsonl"),
        "questions_sha256": questions_digest or sha256_file(questions_path),
        "judge": judge_identity(
            model,
            tuple(dict.fromkeys((*dimensions, *(CALIBRATED_DIMENSIONS if calibrate else ())))),
        ),
        "dimensions": list(dimensions),
        "calibrated_dimensions": list(CALIBRATED_DIMENSIONS if calibrate else ()),
        "limit": limit,
        "calibrate": calibrate,
    }


def run_judge(
    *,
    run_dir: Path,
    questions_path: Path,
    identity_spec_path: Path,
    model: str = DEFAULT_JUDGE_MODEL,
    limit: int | None = None,
    calibrate: int | None = None,
    workers: int = DEFAULT_WORKERS,
    dimensions: tuple[str, ...] = DEFAULT_DIMENSIONS,
    round_id: str | None = None,
    ledger_path: Path = DEFAULT_LEDGER_PATH,
    identity_manifest_dir: Path = DEFAULT_IDENTITY_MANIFEST_DIR,
) -> dict[str, Any]:
    unknown = [dimension for dimension in dimensions if dimension not in DIMENSIONS]
    if unknown or not dimensions:
        raise ValueError(f"dimensions must be a non-empty subset of {DIMENSIONS}, got {dimensions}")
    attempt = start_attempt("judge", round_id=round_id or run_dir.name, ledger_path=ledger_path)
    try:
        run_bytes = (run_dir / "run.json").read_bytes()
        outputs_bytes = (run_dir / "outputs.jsonl").read_bytes()
        questions_bytes = questions_path.read_bytes()
        run_meta = json.loads(run_bytes)
        if not isinstance(run_meta, dict):
            raise ValueError("run.json must be a JSON object")
        questions_sha256 = hashlib.sha256(questions_bytes).hexdigest()
        if run_meta.get("questions_sha256") != questions_sha256:
            raise IdentityRejected("questions snapshot does not match run.json questions_sha256; refusing judge calls")
        questions_snapshot = {
            str(question["question_id"]): question for question in load_questions_bytes(questions_bytes)
        }
        rows_snapshot = list(read_jsonl_bytes(outputs_bytes))
        behavior = planned_judge_behavior_identity(
            run_dir=run_dir,
            questions_path=questions_path,
            model=model,
            limit=limit,
            calibrate=calibrate,
            dimensions=dimensions,
            run_digest=hashlib.sha256(run_bytes).hexdigest(),
            outputs_digest=hashlib.sha256(outputs_bytes).hexdigest(),
            questions_digest=questions_sha256,
        )
        receipt = run_identity_preflight(
            attempt=attempt,
            identity_spec_path=identity_spec_path,
            local_run_dir=run_dir,
            behavior_identity=behavior,
            ledger_path=ledger_path,
            manifest_dir=identity_manifest_dir,
        )
        result = _run_judge_impl(
            run_dir=run_dir,
            questions_path=questions_path,
            model=model,
            limit=limit,
            calibrate=calibrate,
            workers=workers,
            identity_receipt=receipt,
            dimensions=dimensions,
            questions_snapshot=questions_snapshot,
            rows_snapshot=rows_snapshot,
            run_meta_snapshot=run_meta,
        )
        record_event(
            attempt=attempt,
            event="completed",
            status="partial" if result.get("stopped_early") else "complete",
            artifacts={"judge": str(run_dir / "judge.json"), "judgments": str(run_dir / "judgments.jsonl")},
            relations={"questions_sha256": str(result["questions_sha256"])},
            ledger_path=ledger_path,
        )
        return result
    except Exception:
        record_event(attempt=attempt, event="failed", status="failed", ledger_path=ledger_path)
        raise
