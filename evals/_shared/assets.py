"""Versioned questions and append-only experiment artifacts.

References are deliberately separate from inference inputs. JSON is the on-disk
consumer interface; hashes are checked on every load, not just at publication.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BENCHMARKS = {
    "news-admission": "aihot-all-members",
    "visible-score": "aihot-visible-score",
    "content-enrichment": "aihot-enrichment",
    "featured-members": "aihot-featured-members",
}
DEFAULT_DATA_ROOT = Path.home() / "research/video-eval-arena/data/benchmarks/ai-radar"
ROOT = Path(__file__).resolve().parents[2]


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def slug(value: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", value) or value in {".", ".."}:
        raise ValueError(f"invalid directory identity: {value!r}")
    return value


def code_identity(root: Path = ROOT, *, inference: bool = False) -> dict:
    paths = list((root / "src/airadar").rglob("*.py")) if inference else []
    paths += ([root / "evals/_shared/inference.py"] if inference else list((root / "evals").rglob("*.py")))
    files = {str(p.relative_to(root)): file_digest(p) for p in sorted(paths) if p.is_file()}
    if not files:
        raise ValueError("no implementation files found")
    return {"files": files, "sha256": digest(files)}


def load_dataset(path: Path, target: str | None = None) -> tuple[dict, list[dict]]:
    path = path.resolve()
    manifest = read_json(path / "manifest.json")
    selected = manifest["target"]
    if selected not in BENCHMARKS or manifest["benchmark"] != BENCHMARKS[selected]:
        raise ValueError("unknown target/benchmark pairing")
    if target is not None and target != selected:
        raise ValueError("dataset belongs to another target")
    if path.parts[-3:] != (manifest["benchmark"], selected, manifest["version"]):
        raise ValueError("dataset must use <benchmark>/<target>/<version> hierarchy")
    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported dataset schema")
    if "cases.jsonl" not in manifest["files"]:
        raise ValueError("questions are not bound to dataset identity")
    for name, expected in manifest["files"].items():
        candidate = (path / name).resolve()
        if not candidate.is_relative_to(path) or file_digest(candidate) != expected:
            raise ValueError(f"dataset integrity mismatch: {name}")
    evidence = Path(manifest["shared_evidence"])
    for name, expected in manifest["evidence_files"].items():
        candidate = (evidence / name).resolve()
        if not candidate.is_relative_to(evidence.resolve()) or file_digest(candidate) != expected:
            raise ValueError(f"shared evidence integrity mismatch: {name}")
    cases = read_jsonl(path / "cases.jsonl")
    if len(cases) != manifest["case_count"] or len({c["case_id"] for c in cases}) != len(cases):
        raise ValueError("case count or identity mismatch")
    for case in cases:
        if case["split"] not in {"dev", "regression"}:
            raise ValueError("invalid split")
        if not isinstance(case.get("reference"), dict):
            raise ValueError("reference must be explicit, never filled from predictions")
    return manifest, cases


def validate_layout(root: Path = ROOT) -> None:
    for target, benchmark in BENCHMARKS.items():
        leaf = root / "evals" / target / benchmark
        for name in ("README.md", "evaluate.py", "metrics.json"):
            if not (leaf / name).is_file():
                raise ValueError(f"missing canonical evaluation entry: {leaf / name}")
        definitions = read_json(leaf / "metrics.json")
        if definitions["benchmark_slug"] != benchmark:
            raise ValueError(f"metric identity mismatch: {leaf}")
        keys = [(d["task"], d["metric_name"]) for d in definitions["metrics"]]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate metric definition")
        for definition in definitions["metrics"]:
            if definition["direction"] not in {"higher", "lower", "neutral", "unknown"}:
                raise ValueError("invalid metric direction")


def create_run(root: Path, target: str, version: str, *, created_at: datetime | None = None) -> tuple[Path, Path]:
    benchmark = BENCHMARKS[target]
    instant = created_at or datetime.now(UTC)
    if instant.tzinfo is None:
        raise ValueError("run directory time must be timezone-aware")
    stamp = instant.astimezone(UTC)
    suffix = Path(target) / benchmark / slug(version) / stamp.strftime("%Y-%m-%d/%H-%M-%S")
    run, experiment = root / "runs" / suffix, root / "experiments" / suffix
    if run.exists() or experiment.exists():
        raise FileExistsError(f"run partition already exists: {suffix}")
    run.mkdir(parents=True)
    experiment.mkdir(parents=True)
    return run, experiment


def archive_metrics(root: Path, run: Path, experiment: Path, result: dict, metadata: dict) -> list[dict]:
    """Query rows derive exclusively from the immutable scorer result."""
    write_json(run / "scores.json", result)
    write_json(experiment / "metadata.json", metadata)
    definitions = read_json(root / "evals" / metadata["target"] / metadata["benchmark"] / "metrics.json")
    rows = []
    for definition in definitions["metrics"]:
        name = definition["metric_name"]
        value = result["metrics"].get(name)
        if value is None:
            raise ValueError(f"registered metric absent from scorer: {name}")
        rows.append({
            "target_slug": metadata["target"], "benchmark_name": metadata["benchmark"],
            "benchmark_version": metadata["version"], "run_id": str(run.relative_to(root / "runs")),
            "task": definition["task"], "metric_name": name, "metric_value": value["value"],
            "unit": definition["unit"], "status": value["status"], "reason": value.get("reason"),
            "source": str((run / "scores.json").relative_to(root)),
            "pointer": ["metrics", name, "value"],
            "metadata": str((experiment / "metadata.json").relative_to(root)),
        })
    write_json(experiment / "metrics/summary.json", rows)
    return rows


def rebuild_index(root: Path = ROOT) -> list[dict]:
    """Rebuildable projection. Atomic replace does not modify historical leaves."""
    rows = []
    for target, benchmark in BENCHMARKS.items():
        for path in sorted((root / "experiments" / target / benchmark).glob("*/*/*/metrics/summary.json")):
            for row in read_json(path):
                if row["target_slug"] != target or row["benchmark_name"] != benchmark:
                    raise ValueError(f"misfiled query row: {path}")
                value = read_json(root / row["source"])
                for key in row["pointer"]:
                    value = value[key]
                if value != row["metric_value"]:
                    raise ValueError(f"query projection drift: {path}")
                if not (root / row["metadata"]).is_file():
                    raise ValueError(f"missing run metadata: {path}")
                rows.append(row)
    destination = root / "experiments/metrics/summary.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", dir=destination.parent, encoding="utf-8", delete=False) as stream:
        json.dump(rows, stream, ensure_ascii=False, indent=2, allow_nan=False)
        temporary = Path(stream.name)
    temporary.replace(destination)
    return rows
