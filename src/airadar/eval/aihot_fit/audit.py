"""Read-only inventory for the long-lived AIHOT evaluation system."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import Any

from .build import validate_evalset
from .governance import ledger_events


def _has_direct_call(path: Path, enclosing: str, target: str) -> bool:
    """Return whether a named function contains a direct call to ``target``."""
    if not path.is_file():
        return False
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == enclosing:
            return any(
                isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == target
                for call in ast.walk(node)
            )
    return False


def _call_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _has_subparser(path: Path, command: str) -> bool:
    if not path.is_file():
        return False
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return any(
        isinstance(node, ast.Call)
        and _call_name(node) == "add_parser"
        and bool(node.args)
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == command
        for node in ast.walk(tree)
    )


def _dispatches_command(
    path: Path,
    *,
    enclosing: str,
    command_field: str = "command",
    command: str,
    target: str,
) -> bool:
    if not path.is_file():
        return False
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)) or function.name != enclosing:
            continue
        for branch in (node for node in ast.walk(function) if isinstance(node, ast.If)):
            compares_command = any(
                isinstance(node, ast.Compare)
                and isinstance(node.left, ast.Attribute)
                and node.left.attr == command_field
                and any(isinstance(value, ast.Constant) and value.value == command for value in node.comparators)
                for node in ast.walk(branch.test)
            )
            dispatches = any(
                isinstance(node, ast.Call) and _call_name(node) == target
                for statement in branch.body
                for node in ast.walk(statement)
            )
            if compares_command and dispatches:
                return True
    return False


def _has_output_contract(path: Path, *, enclosing: str) -> bool:
    if not path.is_file():
        return False
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)) or function.name != enclosing:
            continue
        writes = sum(
            1 for node in ast.walk(function) if isinstance(node, ast.Call) and _call_name(node) == "write_text"
        )
        returns_artifacts = any(
            isinstance(node, ast.Call)
            and _call_name(node) == "EvaluationArtifacts"
            and {keyword.arg for keyword in node.keywords} >= {"report_path", "compare_path", "metrics"}
            for node in ast.walk(function)
        )
        return writes >= 2 and returns_artifacts
    return False


def _legacy_eval_state(project_root: Path) -> tuple[str, str, dict[str, bool]]:
    cli_path = project_root / "src/airadar/cli.py"
    judge_path = project_root / "src/airadar/eval/judge.py"
    probes = {
        "parser": _has_subparser(cli_path, "eval"),
        "dispatcher": _dispatches_command(cli_path, enclosing="main", command="eval", target="_eval"),
        "adapter": _has_direct_call(cli_path, "_eval", "run_eval"),
        "producer": any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "run_eval"
            for node in (
                ast.walk(ast.parse(judge_path.read_text(encoding="utf-8"), filename=str(judge_path)))
                if judge_path.is_file()
                else ()
            )
        ),
        "output_contract": _has_output_contract(judge_path, enclosing="run_eval"),
    }
    if all(probes.values()):
        status = "located"
    elif any(probes.values()):
        status = "invalid"
    else:
        status = "missing"
    evidence = "; ".join(f"{name}={value}" for name, value in probes.items())
    return status, evidence, probes


def _report_identity_inventory(runs_dir: Path) -> dict[str, int]:
    run_paths = sorted(runs_dir.glob("*/run.json")) if runs_dir.is_dir() else []
    metrics_paths = sorted(runs_dir.glob("*/metrics.json")) if runs_dir.is_dir() else []
    governed_runs: set[Path] = set()
    invalid = 0
    for run_path in run_paths:
        try:
            meta = json.loads(run_path.read_text(encoding="utf-8"))
            if not isinstance(meta, dict):
                invalid += 1
                continue
            outputs_path = run_path.parent / "outputs.jsonl"
            expected = meta.get("outputs_sha256")
            questions = meta.get("questions_sha256")
            if expected and not questions:
                invalid += 1
                continue
            if expected and questions:
                if not outputs_path.is_file() or hashlib.sha256(outputs_path.read_bytes()).hexdigest() != expected:
                    invalid += 1
                else:
                    governed_runs.add(run_path.parent)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            invalid += 1
    governed_reports = 0
    for metrics_path in metrics_paths:
        run_path = metrics_path.parent / "run.json"
        if metrics_path.parent not in governed_runs:
            if not run_path.is_file():
                invalid += 1
            continue
        try:
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
            run_meta = json.loads(run_path.read_text(encoding="utf-8"))
            if (
                isinstance(metrics, dict)
                and metrics.get("questions_sha256") == run_meta.get("questions_sha256")
                and isinstance(metrics.get("measurement_identity"), dict)
            ):
                governed_reports += 1
            else:
                invalid += 1
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            invalid += 1
    return {
        "runs": len(run_paths),
        "governed_runs": len(governed_runs),
        "reports": len(metrics_paths),
        "governed_reports": governed_reports,
        "invalid": invalid,
    }


def _entry(status: str, path: str, evidence: str) -> dict[str, str]:
    return {"status": status, "path": path, "evidence": evidence}


def _count_run_files(runs_dir: Path, name: str) -> int:
    return sum(1 for _path in runs_dir.glob(f"*/{name}")) if runs_dir.is_dir() else 0


def _benchmark_state(project_root: Path, questions: Path) -> str:
    if questions.is_file():
        return "initialized"
    gitmodules = project_root / ".gitmodules"
    if gitmodules.is_file() and "path = benchmarks/aihot" in gitmodules.read_text(encoding="utf-8"):
        return "gitlink present but submodule not initialized in this worktree"
    return "benchmark asset not present in this checkout"


def _evalset_state(evalset_dir: Path, *, base_questions_path: Path | None = None) -> tuple[str, str]:
    if not (evalset_dir / "questions.jsonl").is_file():
        return "missing", "asset not present"
    try:
        result = validate_evalset(evalset_dir=evalset_dir, base_questions_path=base_questions_path)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        return "invalid", f"offline validation failed: {type(exc).__name__}: {exc}"
    return "located", f"offline validation pass: n={result['question_count']} sha256={result['questions_sha256']}"


def _jsonl_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
    return rows


def _jsonl_rows_with_sha(path: Path) -> list[tuple[dict[str, Any], str]]:
    if not path.is_file():
        return []
    rows: list[tuple[dict[str, Any], str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError("JSONL rows must be objects")
        rows.append((value, hashlib.sha256(line.encode("utf-8")).hexdigest()))
    return rows


def audit_eval_system(*, project_root: Path, runs_dir: Path, ledger_path: Path | None = None) -> dict[str, Any]:
    ledger_path = ledger_path or (project_root / "scripts/eval/aihot-fit-history.jsonl")
    fit_root = project_root / "src/airadar/eval/aihot_fit"
    cli_path = fit_root / "cli.py"
    metrics_path = fit_root / "metrics.py"
    run_path = fit_root / "run.py"
    judge_path = fit_root / "judge.py"
    archive_measure_path = project_root / "scripts/eval/measure_archive_composition.py"
    real_run_replay_path = project_root / "scripts/eval/replay_real_runs.py"
    benchmark = project_root / "benchmarks/aihot"
    questions_v1 = benchmark / "evalsets/aihot-fit-v1/questions.jsonl"
    questions_v2 = benchmark / "evalsets/aihot-fit-v2/questions.jsonl"
    v1_state = _benchmark_state(project_root, questions_v1)
    v1_status, v1_validation = _evalset_state(questions_v1.parent)
    if v1_status == "missing":
        v1_validation = v1_state
    v2_status, v2_validation = _evalset_state(questions_v2.parent, base_questions_path=questions_v1)
    output_count = _count_run_files(runs_dir, "outputs.jsonl")
    metrics_count = _count_run_files(runs_dir, "metrics.json")
    judgment_count = _count_run_files(runs_dir, "judgments.jsonl")
    report_inventory = _report_identity_inventory(runs_dir)
    legacy_eval_status, legacy_eval_evidence, legacy_eval_probes = _legacy_eval_state(project_root)

    try:
        events = ledger_events(ledger_path)
        ledger_error = None
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        events = []
        ledger_error = f"{type(exc).__name__}: {exc}"
    attempts: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        attempts.setdefault(str(event.get("attempt_id")), []).append(event)
    terminal = {"completed", "failed"}
    orphan_attempts = sorted(
        attempt_id
        for attempt_id, rows in attempts.items()
        if any(row.get("event") == "started" for row in rows) and not any(row.get("event") in terminal for row in rows)
    )
    manifest_dir = project_root / "scripts/eval/aihot-fit-identities"
    identity_manifests = sorted(manifest_dir.glob("*.json")) if manifest_dir.is_dir() else []
    archive_history = project_root / "scripts/eval/composition-history.jsonl"
    try:
        archive_rows_with_sha = [
            (row, digest) for row, digest in _jsonl_rows_with_sha(archive_history) if row.get("surface") == "archive"
        ]
        archive_error = None
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        archive_rows_with_sha = []
        archive_error = f"{type(exc).__name__}: {exc}"
    archive_rows = [row for row, _digest in archive_rows_with_sha]
    archive_digests: dict[str, list[str]] = {}
    for row, digest in archive_rows_with_sha:
        if row.get("record_id"):
            archive_digests.setdefault(str(row["record_id"]), []).append(digest)
    archive_ids = set(archive_digests)
    archive_index_events = [row for row in events if (row.get("relations") or {}).get("composition_record_id")]
    indexed_archive_ids = {
        str((row.get("relations") or {}).get("composition_record_id")) for row in archive_index_events
    }
    unindexed_archive = sorted(archive_ids - indexed_archive_ids)
    dangling_archive_index = sorted(indexed_archive_ids - archive_ids)
    duplicate_archive_ids = sorted(record_id for record_id, digests in archive_digests.items() if len(digests) != 1)
    expected_archive_artifact = str(archive_history.relative_to(project_root))
    invalid_archive_index: list[str] = []
    for event in archive_index_events:
        record_id = str((event.get("relations") or {}).get("composition_record_id"))
        expected_sha = str((event.get("relations") or {}).get("composition_row_sha256") or "")
        artifact = str((event.get("artifacts") or {}).get("composition_history") or "")
        actual = archive_digests.get(record_id) or []
        if len(actual) != 1 or not expected_sha or actual[0] != expected_sha:
            invalid_archive_index.append(f"{record_id}:row_sha256")
        if artifact != expected_archive_artifact:
            invalid_archive_index.append(f"{record_id}:artifact={artifact or 'missing'}")
    unidentifiable_archive_rows = sum(1 for row in archive_rows if not row.get("record_id"))

    eval_fit_targets = {
        "build": "build_evalset",
        "validate": "validate_evalset",
        "run": "run_stages",
        "judge": "run_judge",
        "report": "compute_metrics",
        "audit": "audit_eval_system",
    }
    eval_fit_dispatch = {
        command: _dispatches_command(
            cli_path,
            enclosing="run_eval_fit",
            command_field="eval_fit_command",
            command=command,
            target=target,
        )
        for command, target in eval_fit_targets.items()
    }
    report_caller = eval_fit_dispatch["report"]
    audit_caller = eval_fit_dispatch["audit"]
    acceptance_caller = _has_direct_call(metrics_path, "compute_metrics", "judge_acceptance")
    comparison_caller = _has_direct_call(metrics_path, "compute_metrics", "compare_to_baseline")
    run_preflight = _has_direct_call(run_path, "run_stages", "run_identity_preflight")
    judge_preflight = _has_direct_call(judge_path, "run_judge", "run_identity_preflight")
    archive_ledger_caller = _has_direct_call(
        archive_measure_path, "main", "start_attempt"
    ) and _has_direct_call(archive_measure_path, "main", "record_event")
    real_run_replay_entry = (
        real_run_replay_path.is_file()
        and _has_direct_call(real_run_replay_path, "main", "replay")
        and _has_direct_call(real_run_replay_path, "main", "arm_b")
        and _has_direct_call(real_run_replay_path, "arm_b", "replay")
    )

    l1 = {
        "1_object": _entry(
            "located",
            "src/airadar/{prefilter,scorer,enrich,presentation}/ + curator/select.py",
            "production prefilter, scoring, enrichment, curation and final presentation form the evaluated workflow",
        ),
        "2_questions": _entry(
            v1_status,
            "benchmarks/aihot/evalsets/aihot-fit-v1/questions.jsonl",
            v1_validation,
        ),
        "2_questions_v2": _entry(
            v2_status,
            "benchmarks/aihot/evalsets/aihot-fit-v2/questions.jsonl",
            v2_validation if questions_v2.is_file() else "v2 authority has not been materialized",
        ),
        "3_judge": _entry(
            "partial",
            "src/airadar/eval/aihot_fit/{judge.py,judge_prompts.py}",
            "title/summary/reason are wired; title is deliberately diagnostic-only until title-specific validation is approved",
        ),
        "4_metrics": _entry(
            "located",
            "src/airadar/eval/aihot_fit/metrics.py + scripts/eval/measure_archive_composition.py",
            "per-item selection/category/summary/reason/topic_tags_v2 and archive final display score have dedicated readings on their authoritative surfaces",
        ),
        "5_judge_validation": _entry(
            "partial" if acceptance_caller else "missing",
            "src/airadar/eval/aihot_fit/{judge.py,metrics.py}",
            "summary/reason calibration identity is enforced; title-specific validation remains intentionally absent",
        ),
    }

    historical_ballot_adr = project_root / "docs/adr/20260903-bc36-quota-curated-selection-by-source-form.md"
    current_ballot_root = project_root / ".label-serve/quota-accept"
    current_ballot_present = current_ballot_root.exists()
    l2 = {
        "1_questions": _entry(v1_status, str(questions_v1), v1_validation),
        "2_per_question_outputs": _entry(
            "located" if output_count else "unverified",
            str(runs_dir / "*/outputs.jsonl"),
            f"{output_count} retained local assets; zero here does not disprove historical runs",
        ),
        "3_metric_values": _entry(
            "invalid" if archive_error else "located",
            "data/eval-fit/runs/*/metrics.json + scripts/eval/composition-history.jsonl",
            archive_error
            or f"{metrics_count} retained metrics assets and {len(archive_rows)} authoritative archive records",
        ),
        "4_human_evaluations": _entry(
            "located" if current_ballot_present else "unverified",
            "docs/adr/20260903-bc36... + .label-serve/quota-accept/",
            (
                "historical record and current ballot asset are both present"
                if current_ballot_present
                else f"historical record exists={historical_ballot_adr.is_file()}; current ballot asset was not located"
            ),
        ),
        "5_judge_raw": _entry(
            "located" if judgment_count else "unverified",
            str(runs_dir / "*/judgments.jsonl"),
            f"{judgment_count} conditional local assets; this audit makes no LLM call",
        ),
        "6_attribution": _entry(
            "located",
            "docs/issues/aihot-fit-eval.md",
            "tracked hypothesis and differential_prediction history; admission remains a human review step",
        ),
        "7_round_ledger": _entry(
            "invalid" if ledger_error else ("located" if events else "partial"),
            str(ledger_path),
            ledger_error
            or f"{len(events)} events across {len(attempts)} attempts; orphan started={len(orphan_attempts)}",
        ),
    }

    l3 = {
        "object_identity": _entry(
            "executable" if run_preflight and judge_preflight else "missing",
            "src/airadar/eval/aihot_fit/governance.py + scripts/eval/aihot-fit-identities/",
            f"run/judge identity preflight wired={run_preflight and judge_preflight}; persistent manifests={len(identity_manifests)}",
        ),
        "comparison_window": _entry(
            (
                "invalid"
                if report_inventory["invalid"]
                else "executable"
                if report_caller and comparison_caller
                else "missing"
            ),
            "data/eval-fit/runs/*/metrics.json",
            (
                f"future reports enforce questions/output/judge/calibration byte identity; "
                f"governed_runs={report_inventory['governed_runs']}/{report_inventory['runs']}; "
                f"governed_reports={report_inventory['governed_reports']}/{report_inventory['reports']}; "
                f"invalid={report_inventory['invalid']}; legacy assets are preserved as non-comparable and cannot "
                "be upgraded without producer-time identity"
            ),
        ),
        "provenance": _entry(
            "located" if current_ballot_present else "partial",
            "benchmarks/aihot/labels/*/PROVENANCE.md + docs/adr/20260903-bc36...",
            (
                "agent labels are attributable and the current ballot asset is present"
                if current_ballot_present
                else "agent labels are attributable; historical ballot record exists but current ballot asset was not located"
            ),
        ),
        "attribution_admission": _entry(
            "manual",
            "CLAUDE.md + docs/issues/aihot-fit-eval.md",
            "differential_prediction must precede intervention; no fake mechanical proof of causal support is claimed",
        ),
        "archive_index": _entry(
            (
                "invalid"
                if ledger_error
                or archive_error
                or invalid_archive_index
                or dangling_archive_index
                or duplicate_archive_ids
                else "partial"
                if unindexed_archive
                else "located"
            ),
            "scripts/eval/composition-history.jsonl → scripts/eval/aihot-fit-history.jsonl",
            (
                archive_error
                or ledger_error
                or f"records with record_id={len(archive_ids)}; unindexed={len(unindexed_archive)}; "
                f"pre_contract_legacy={unidentifiable_archive_rows}; invalid_refs={len(invalid_archive_index)}; "
                f"dangling={len(dangling_archive_index)}; duplicate_ids={len(duplicate_archive_ids)}"
            ),
        ),
        "caller_wiring": _entry(
            (
                "executable"
                if all(
                    (
                        audit_caller,
                        all(eval_fit_dispatch.values()),
                        acceptance_caller,
                        comparison_caller,
                        run_preflight,
                        judge_preflight,
                        archive_ledger_caller,
                        real_run_replay_entry,
                    )
                )
                else "missing"
            ),
            "src/airadar/eval/aihot_fit/{cli.py,run.py,judge.py,metrics.py} + scripts/eval/{measure_archive_composition,replay_real_runs}.py",
            (
                f"eval-fit command dispatch={sum(eval_fit_dispatch.values())}/{len(eval_fit_dispatch)}; "
                f"run identity caller={run_preflight}; judge identity caller={judge_preflight}; "
                f"archive ledger caller={archive_ledger_caller}; archive measurement intentionally remains explicit"
                f"; real-run replay entry={real_run_replay_entry}"
            ),
        ),
    }

    workflow = {
        "establish": _entry(
            "executable",
            "./run.sh eval-fit build → ./run.sh eval-fit validate",
            "writes versioned questions/manifest, then validates them offline",
        ),
        "daily_eval": _entry(
            "executable",
            "uv run python scripts/eval/measure_archive_composition.py --record",
            "authoritative archive composition; per-item eval remains an explicit offline run",
        ),
        "attribution": _entry("manual", "docs/issues/aihot-fit-eval.md", "hypothesis precedes diagnostic intervention"),
        "optimization": _entry(
            "partial",
            "docs/issues/aihot-fit-eval.md + scripts/eval/replay_real_runs.py",
            f"replay entry wired={real_run_replay_entry}; self-calibration gates paired run-level arm B; "
            "direct archive counterfactual remains unavailable",
        ),
        "regression": _entry(
            "executable" if report_caller else "missing", "./run.sh eval-fit report", "threshold and baseline report"
        ),
        "report": _entry(
            "executable" if audit_caller else "missing", "./run.sh eval-fit audit", "read-only three-layer status map"
        ),
        "legacy_eval": _entry(
            legacy_eval_status,
            "./run.sh eval",
            (
                "role=separate legacy snapshot comparison/reporting tool (V1-V5 metrics report + V6 presentation), "
                "outside eval-fit and archive authority; current callers and future retirement are unverified; "
                f"{legacy_eval_evidence}"
            ),
        ),
    }
    sections = {"l1": l1, "l2": l2, "l3": l3, "workflow": workflow}
    counts = {
        status: 0 for status in ("located", "partial", "missing", "invalid", "unverified", "executable", "manual")
    }
    for section in sections.values():
        for item in section.values():
            counts[item["status"]] = counts.get(item["status"], 0) + 1
    return {
        **sections,
        "details": {
            "orphan_attempt_ids": orphan_attempts,
            "unindexed_archive_record_ids": unindexed_archive,
            "dangling_archive_record_ids": dangling_archive_index,
            "duplicate_archive_record_ids": duplicate_archive_ids,
            "invalid_archive_index_refs": invalid_archive_index,
            "report_identity_inventory": report_inventory,
            "legacy_eval_probes": legacy_eval_probes,
            "eval_fit_dispatch": eval_fit_dispatch,
            "real_run_replay_entry": real_run_replay_entry,
        },
        "summary": counts,
    }


def render_audit(result: dict[str, Any]) -> str:
    lines = [
        "AIHOT eval system audit (read-only; no LLM calls)",
        f"summary: missing={result['summary']['missing']} invalid={result['summary']['invalid']} "
        f"partial={result['summary']['partial']} unverified={result['summary']['unverified']}",
    ]
    for section in ("l1", "l2", "l3", "workflow"):
        lines.extend(("", section.upper()))
        for name, item in result[section].items():
            lines.append(f"[{item['status'].upper()}] {name}: {item['path']} — {item['evidence']}")
    return "\n".join(lines)
