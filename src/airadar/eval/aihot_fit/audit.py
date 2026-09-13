"""Zero-call inventory for the long-lived AIHOT evaluation system."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any


def _has_direct_call(path: Path, enclosing: str, target: str) -> bool:
    """Return whether a named function contains a direct call to ``target``.

    This is deliberately a static syntax check, not a general call-graph claim. Imports,
    aliases, dynamic dispatch and runtime reachability remain outside its evidence scope.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == enclosing:
            return any(
                isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == target
                for call in ast.walk(node)
            )
    return False


def _entry(status: str, path: str, evidence: str) -> dict[str, str]:
    return {"status": status, "path": path, "evidence": evidence}


def _count_run_files(runs_dir: Path, name: str) -> int:
    return sum(1 for _path in runs_dir.glob(f"*/{name}")) if runs_dir.is_dir() else 0


def _benchmark_state(project_root: Path, questions: Path) -> str:
    if questions.is_file():
        return "initialized"
    gitmodules = project_root / ".gitmodules"
    if gitmodules.is_file() and "path = benchmarks/aihot" in gitmodules.read_text(encoding="utf-8"):
        return "gitlink present but submodule not initialized"
    return "benchmark asset not present in this checkout"


def audit_eval_system(*, project_root: Path, runs_dir: Path) -> dict[str, Any]:
    fit_root = project_root / "src/airadar/eval/aihot_fit"
    cli_path = fit_root / "cli.py"
    metrics_path = fit_root / "metrics.py"
    benchmark = project_root / "benchmarks/aihot"
    questions = benchmark / "evalsets/aihot-fit-v1/questions.jsonl"
    benchmark_state = _benchmark_state(project_root, questions)
    output_count = _count_run_files(runs_dir, "outputs.jsonl")
    metrics_count = _count_run_files(runs_dir, "metrics.json")
    judgment_count = _count_run_files(runs_dir, "judgments.jsonl")

    report_caller = _has_direct_call(cli_path, "run_eval_fit", "compute_metrics")
    audit_caller = _has_direct_call(cli_path, "run_eval_fit", "audit_eval_system")
    acceptance_caller = _has_direct_call(metrics_path, "compute_metrics", "judge_acceptance")
    comparison_caller = _has_direct_call(metrics_path, "compute_metrics", "compare_to_baseline")

    l1 = {
        "1_object": _entry(
            "located",
            "src/airadar/{prefilter,scorer,enrich,presentation}/ + curator/select.py",
            "production prefilter, scoring, enrichment, curation and final presentation form the evaluated workflow",
        ),
        "2_questions": _entry(
            "located" if questions.is_file() else "missing",
            "benchmarks/aihot/evalsets/aihot-fit-v1/questions.jsonl",
            benchmark_state,
        ),
        "3_judge": _entry(
            "partial",
            "src/airadar/eval/aihot_fit/{judge.py,judge_prompts.py}",
            "summary/reason have a judge; title and final presented score do not",
        ),
        "4_metrics": _entry(
            "partial",
            "src/airadar/eval/aihot_fit/metrics.py + scripts/eval/measure_archive_composition.py",
            "selection/category/summary/reason have direct or diagnostic readings; score_spearman and tag_jaccard are upstream proxies; title and final 62-92 score lack direct metrics",
        ),
        "5_judge_validation": _entry(
            "partial" if acceptance_caller else "missing",
            "src/airadar/eval/aihot_fit/{judge.py,metrics.py}",
            "calibration identity and acceptance are wired into compute_metrics" if acceptance_caller else "no static syntax caller found",
        ),
    }

    l2 = {
        "1_questions": _entry("located" if questions.is_file() else "missing", str(questions), benchmark_state),
        "2_per_question_outputs": _entry(
            "located" if output_count else "unverified",
            str(runs_dir / "*/outputs.jsonl"),
            f"{output_count} local retained run assets; absence in this checkout would not prove no historical runs",
        ),
        "3_metric_values": _entry(
            "located",
            "data/eval-fit/runs/*/metrics.json + scripts/eval/composition-history.jsonl",
            f"{metrics_count} local retained metrics assets; the composition series is tracked",
        ),
        "4_human_evaluations": _entry(
            "missing",
            "benchmarks/aihot/labels/",
            "agent-label provenance exists, but no user ballot series was located",
        ),
        "5_judge_raw": _entry(
            "located" if judgment_count else "unverified",
            str(runs_dir / "*/judgments.jsonl"),
            f"{judgment_count} conditional local assets; no LLM call is made by this audit",
        ),
        "6_attribution": _entry(
            "located",
            "docs/issues/aihot-fit-eval.md",
            "tracked hypothesis/differential-prediction history",
        ),
        "7_round_ledger": _entry(
            "partial",
            "docs/issues/aihot-fit-eval.md + scripts/eval/composition-history.jsonl",
            "history is preserved but there is no single per-round ledger covering ②③④⑤ identity, acceptance and every user-visible target distance",
        ),
    }

    l3 = {
        "object_identity": _entry(
            "partial",
            "data/eval-fit/runs/*/run.json",
            "run.json carries stage, git and input fields, but no eval-identity-generated receipt was located",
        ),
        "comparison_window": _entry(
            "partial",
            "data/eval-fit/runs/*/metrics.json",
            "new reports record ② input/reference, ③ judge, ④ emitter and ⑤ calibration identities; legacy reports need baseline recomputation",
        ),
        "provenance": _entry(
            "partial",
            "benchmarks/aihot/labels/*/PROVENANCE.md",
            "agent labels are distinguishable; no user-ballot asset was located",
        ),
        "attribution_admission": _entry(
            "missing",
            "CLAUDE.md + docs/issues/aihot-fit-eval.md",
            "manual policy and hypothesis records exist, but no mechanical caller verifies a supported hypothesis before adoption",
        ),
        "caller_wiring": _entry(
            "partial" if all((audit_caller, report_caller, acceptance_caller, comparison_caller)) else "missing",
            "src/airadar/eval/aihot_fit/{cli.py,metrics.py}",
            "static syntax only: run_eval_fit→audit_eval_system/compute_metrics and compute_metrics→judge_acceptance/compare_to_baseline; runtime invocation is separate evidence, and no scheduled evaluation or attribution-admission caller is connected",
        ),
    }

    workflow = {
        "establish": _entry("executable", "./run.sh eval-fit build", "writes questions + manifest"),
        "daily_eval": _entry(
            "executable",
            "uv run python scripts/eval/measure_archive_composition.py --record",
            "authoritative archive composition; per-item eval remains an explicit offline run",
        ),
        "attribution": _entry("manual", "docs/issues/aihot-fit-eval.md", "hypothesis record precedes diagnostic intervention"),
        "optimization": _entry(
            "executable",
            "docs/issues/aihot-fit-eval.md + scripts/eval/",
            "record differential_prediction, then run the cause-specific offline experiment before adoption",
        ),
        "regression": _entry(
            "executable" if report_caller else "missing", "./run.sh eval-fit report", "threshold and baseline report"
        ),
        "report": _entry(
            "executable" if audit_caller else "missing", "./run.sh eval-fit audit", "three-layer status and handoff map"
        ),
        "legacy_eval": _entry(
            "unverified",
            "./run.sh eval",
            "coexists as an independent presentation comparison entry; parent-system ownership was not established",
        ),
    }
    sections = {"l1": l1, "l2": l2, "l3": l3, "workflow": workflow}
    counts = {status: 0 for status in ("located", "partial", "missing", "unverified", "executable", "manual")}
    for section in sections.values():
        for item in section.values():
            counts[item["status"]] = counts.get(item["status"], 0) + 1
    return {**sections, "summary": counts}


def render_audit(result: dict[str, Any]) -> str:
    lines = [
        "AIHOT eval system audit (read-only; no LLM calls)",
        f"summary: missing={result['summary']['missing']} partial={result['summary']['partial']} unverified={result['summary']['unverified']}",
    ]
    for section in ("l1", "l2", "l3", "workflow"):
        lines.extend(("", section.upper()))
        for name, item in result[section].items():
            lines.append(f"[{item['status'].upper()}] {name}: {item['path']} — {item['evidence']}")
    return "\n".join(lines)
