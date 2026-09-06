from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ... import db

BUILDER_VERSION = "aihot-fit-build-v1"
RUN_SCHEMA_VERSION = "aihot-fit-run-v1"
JUDGE_SCHEMA_VERSION = "aihot-fit-judge-v1"
METRICS_SCHEMA_VERSION = "aihot-fit-metrics-v1"
REFERENCE_FIELD = {"summary": "summary", "reason": "reason"}


def has_reference(question: dict[str, Any], dimension: str) -> bool:
    """True when the AIHOT reference carries a non-empty value for this judge dimension."""
    value = question["reference"].get(REFERENCE_FIELD[dimension])
    return bool(value) and bool(str(value).strip())


DEFAULT_WORKERS = 8
DEFAULT_EVALSET_DIR = db.PROJECT_ROOT / "data" / "eval-fit" / "evalset-staging" / "aihot-fit-v1"
DEFAULT_RUNS_DIR = db.PROJECT_ROOT / "data" / "eval-fit" / "runs"

EVAL_USAGE_DB = DEFAULT_RUNS_DIR.parent / "llm-usage-eval.db"
EVAL_BREAKER_STATE = DEFAULT_RUNS_DIR.parent / "ark-breaker-eval.json"


def isolate_side_effects() -> dict[str, str]:
    """Point the production usage ledger and ARK breaker at eval-local files.

    An eval run drives the production entrypoints, so it writes `llm_usage` rows under the
    production stage names -- 970 of them for three small runs, 23.9% of that day's table and
    indistinguishable from real traffic in cost reports. The same path lets a 429 from a large
    eval run trip `data/ark-breaker.json`, which pushes *production* onto the pay-per-token
    DeepSeek endpoint for two hours. Both modules resolve their file from an environment
    variable, so redirecting those is enough and no production code changes.

    Returns the paths in effect, for the run identity record.
    """
    os.environ["AI_RADAR_LLM_USAGE_DB"] = str(EVAL_USAGE_DB)
    os.environ["AI_RADAR_ARK_BREAKER_STATE"] = str(EVAL_BREAKER_STATE)
    EVAL_USAGE_DB.parent.mkdir(parents=True, exist_ok=True)
    return {"llm_usage_db": str(EVAL_USAGE_DB), "ark_breaker_state": str(EVAL_BREAKER_STATE)}


# Single owner of the AIHOT category slug -> PrimaryCategory mapping used by the
# evalset. Values match ``airadar.enrich.classification.PrimaryCategory``.
CATEGORY_SLUG_TO_PRIMARY: dict[str, str] = {
    "ai-models": "model",
    "ai-products": "product",
    "industry": "industry",
    "paper": "paper",
    "tip": "tutorial",
}
PRIMARY_CATEGORIES: tuple[str, ...] = ("model", "product", "industry", "paper", "tutorial")

# Failure texts that mean "stop spending": auth / quota / rate-limit / WAF.
_STOP_PATTERN = re.compile(
    r"\b(401|402|403|429)\b|unauthorized|forbidden|rate.?limit|too many requests|"
    r"insufficient.?(balance|quota)|quota exceeded|\bwaf\b|access denied",
    re.IGNORECASE,
)
_SECRET_ENV_PATTERN = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD)", re.IGNORECASE)


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_dumps(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def load_questions(path: Path) -> list[dict[str, Any]]:
    questions = list(read_jsonl(path))
    questions.sort(key=lambda question: str(question["question_id"]))
    return questions


def secret_values() -> list[str]:
    values = [value for key, value in os.environ.items() if _SECRET_ENV_PATTERN.search(key) and len(value) >= 8]
    values.sort(key=len, reverse=True)
    return values


def redact(text: str) -> str:
    """Strip credential values and bearer tokens from a message before it is persisted."""
    redacted = text
    for value in secret_values():
        redacted = redacted.replace(value, "<redacted>")
    return re.sub(r"(?i)bearer\s+[A-Za-z0-9._\-]+", "Bearer <redacted>", redacted)


def is_stop_signal(error: str | None) -> bool:
    return bool(error) and bool(_STOP_PATTERN.search(error or ""))


def require_ark_only() -> dict[str, Any]:
    """Make chat_json ARK-only for this process and report the credential surface.

    The production provider falls back to the pay-per-token DeepSeek endpoint when
    ``DEEPSEEK_API_KEY`` is set; an evaluation run must never take that path, so the
    fallback key is removed from this process's environment before any call.
    """
    had_deepseek = "DEEPSEEK_API_KEY" in os.environ
    os.environ.pop("DEEPSEEK_API_KEY", None)
    ark_key = os.environ.get("ARK_API_KEY")
    if not ark_key:
        raise RuntimeError("ARK_API_KEY is not set; aihot-fit only calls the ARK subscription endpoint")
    return {
        "ark_only": True,
        "ark_api_key_present": True,
        "deepseek_fallback_removed": had_deepseek,
    }


def model_selection_env() -> dict[str, str]:
    """Non-credential env variables that select models or providers."""
    names = {
        "AI_RADAR_PREFILTER",
        "AI_RADAR_SCORER",
        "AI_RADAR_ENRICHER",
        "AI_RADAR_ENRICH_V2",
        "AI_RADAR_ARK_THINKING",
    }
    selected: dict[str, str] = {}
    for key, value in sorted(os.environ.items()):
        if key in names or (key.startswith("AI_RADAR_") and "MODEL" in key):
            if _SECRET_ENV_PATTERN.search(key):
                continue
            selected[key] = value
    return selected


def git_identity() -> dict[str, Any]:
    root = str(db.PROJECT_ROOT)
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True, timeout=10
        ).stdout.strip()
        dirty_lines = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return {"head": None, "dirty": None, "error": type(exc).__name__}
    return {"head": head, "dirty": bool(dirty_lines)}


def readonly_db_uri(path: Path) -> str:
    return f"file:{path.resolve()}?mode=ro"
