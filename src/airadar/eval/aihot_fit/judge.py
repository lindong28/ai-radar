"""LLM judge (DeepSeek via ARK) for summary / reason closeness to the AIHOT reference."""

from __future__ import annotations

import os
import statistics
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ...provider.deepseek_chat import _ark_base_url, chat_json
from . import judge_prompts
from .common import (
    DEFAULT_WORKERS,
    JUDGE_SCHEMA_VERSION,
    REFERENCE_FIELD,
    is_stop_signal,
    isolate_side_effects,
    load_questions,
    read_json,
    read_jsonl,
    redact,
    require_ark_only,
    utc_now,
    write_json,
    write_jsonl,
)

DEFAULT_JUDGE_MODEL = "deepseek-v4-flash-ga-260731"
JUDGE_TEMPERATURE = 0.0
JUDGE_MAX_TOKENS = 512
DIMENSIONS: tuple[str, ...] = ("summary", "reason")
POSITIVE_MIN_MEAN = 80.0
NEGATIVE_MAX_MEAN = 40.0
_JUDGE_MODEL_ENV = "AI_RADAR_FIT_JUDGE_MODEL"
_JUDGE_ARK_MODEL_ENV = "AI_RADAR_ARK_FIT_JUDGE_MODEL"


_CANDIDATE_FIELD = {"summary": "summary_zh", "reason": "why_recommend"}


def judge_identity(model: str) -> dict[str, Any]:
    return {
        "schema_version": JUDGE_SCHEMA_VERSION,
        "model": model,
        "temperature": JUDGE_TEMPERATURE,
        "max_tokens": JUDGE_MAX_TOKENS,
        "prompt_sha256": {dimension: judge_prompts.prompt_sha256(dimension) for dimension in DIMENSIONS},
        "ark_host": urlsplit(_ark_base_url()).hostname,
        "usage_recorded": False,
    }


def judge_once(
    *, model: str, dimension: str, title: str, content: str, reference: str, candidate: str
) -> dict[str, Any]:
    user = judge_prompts.render_user(title=title, content=content, reference=reference, candidate=candidate)
    result = chat_json(
        system=judge_prompts.system_prompt(dimension),
        user=user,
        default_model=model,
        model_env=_JUDGE_MODEL_ENV,
        ark_model_env=_JUDGE_ARK_MODEL_ENV,
        temperature=JUDGE_TEMPERATURE,
        max_tokens=JUDGE_MAX_TOKENS,
        stage=None,
    )
    payload = result.json
    if result.provider != "ark":
        raise RuntimeError(f"cash signal: provider={result.provider}")
    raw_closeness = payload.get("closeness")
    try:
        closeness = int(round(float(raw_closeness)))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"judge returned non-numeric closeness: {raw_closeness!r}") from exc
    closeness = max(0, min(100, closeness))
    return {
        "closeness": closeness,
        "rationale": str(payload.get("rationale", "")),
        "raw": {"provider": result.provider, "model": result.model, "json": payload},
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
    for dimension in DIMENSIONS:
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


def run_judge(
    *,
    run_dir: Path,
    questions_path: Path,
    model: str = DEFAULT_JUDGE_MODEL,
    limit: int | None = None,
    calibrate: int | None = None,
    workers: int = DEFAULT_WORKERS,
) -> dict[str, Any]:
    credentials = require_ark_only()
    side_effects = isolate_side_effects()
    # Pin the ARK model for this process so a global AI_RADAR_ARK_DEEPSEEK_MODEL cannot swap the judge.
    os.environ[_JUDGE_ARK_MODEL_ENV] = model
    questions = {str(question["question_id"]): question for question in load_questions(questions_path)}
    rows = sorted(read_jsonl(run_dir / "outputs.jsonl"), key=lambda row: str(row["question_id"]))
    if limit is not None:
        rows = rows[:limit]
    run_meta = read_json(run_dir / "run.json")

    identity = judge_identity(model)
    judge = _Judge(model, workers)
    started_at = utc_now()

    calibration: dict[str, Any] | None = None
    if calibrate:
        control_results = judge.run(_calibration_tasks(questions, rows, calibrate))
        means = _control_means(control_results)
        verdicts: dict[str, Any] = {}
        for dimension in DIMENSIONS:
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
            "identity": identity,
        }
        write_json(run_dir / "judge-calibration.json", calibration)

    skipped: dict[str, dict[str, int]] = {}
    tasks: list[dict[str, Any]] = []
    for dimension in DIMENSIONS:
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
        **identity,
        "run_id": run_meta.get("run_id"),
        "questions_sha256": run_meta.get("questions_sha256"),
        "started_at": started_at,
        "finished_at": utc_now(),
        "limit": limit,
        "workers": workers,
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
        "skipped": skipped,
        "stopped_early": judge.stop.is_set(),
        "stop_reasons": judge.stop_reasons,
        "calibration_summary": None
        if calibration is None
        else {"means": calibration["means"], "scale_ok": calibration["scale_ok"]},
    }
    write_json(run_dir / "judge.json", judge_json)
    return judge_json
