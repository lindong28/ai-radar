"""Append-only round ledger and fail-closed identity preflight for aihot-fit."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from ... import db
from .common import git_identity, json_dumps, sha256_file, sha256_text, utc_now

LEDGER_SCHEMA_VERSION = "aihot-fit-ledger-event-v1"
IDENTITY_MANIFEST_SCHEMA_VERSION = "aihot-fit-identity-manifest-v1"
DEFAULT_LEDGER_PATH = db.PROJECT_ROOT / "scripts/eval/aihot-fit-history.jsonl"
DEFAULT_IDENTITY_MANIFEST_DIR = db.PROJECT_ROOT / "scripts/eval/aihot-fit-identities"
EVAL_IDENTITY_BIN = Path.home() / ".claude/bin/eval-identity"
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_LEDGER_EVENT_STATUS = {
    "started": frozenset({"incomplete"}),
    "identity_accepted": frozenset({"accepted"}),
    "identity_rejected": frozenset({"rejected"}),
    "completed": frozenset({"succeeded", "complete", "partial"}),
    "failed": frozenset({"failed"}),
}
_LEDGER_STAGES = frozenset({"build", "run", "judge", "report", "archive"})


class IdentityRejected(RuntimeError):
    """The identity comparison did not authorize an irreversible model call."""


@dataclass(frozen=True)
class Attempt:
    attempt_id: str
    round_id: str
    stage: str
    started_at: str


def _safe_token(value: str, field: str) -> str:
    if not _SAFE_TOKEN.fullmatch(value):
        raise ValueError(f"{field} must be a portable identifier, got {value!r}")
    return value


def _relative_artifact(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(db.PROJECT_ROOT.resolve()))
    except ValueError:
        # Custom test/analysis outputs may intentionally live outside the checkout. The tracked
        # observer must neither reject a completed run nor leak a maintainer-local absolute path.
        return f"external/{path.name}"


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json_dumps(row) + "\n").encode("utf-8")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        with os.fdopen(descriptor, "ab", closefd=False) as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def record_event(
    *,
    attempt: Attempt,
    event: str,
    status: str,
    artifacts: dict[str, str] | None = None,
    relations: dict[str, str] | None = None,
    ledger_path: Path = DEFAULT_LEDGER_PATH,
) -> dict[str, Any]:
    """Append one schema-constrained observer event; metric values remain in their owner."""
    event = _safe_token(event, "event")
    status = _safe_token(status, "status")
    normalized_artifacts: dict[str, str] = {}
    for name, value in sorted((artifacts or {}).items()):
        _safe_token(name, "artifact key")
        candidate = Path(value)
        if candidate.is_absolute():
            normalized_artifacts[name] = _relative_artifact(candidate)
        else:
            normalized_artifacts[name] = str(candidate)
    normalized_relations = {
        _safe_token(str(name), "relation key"): _safe_token(str(value), f"relation {name}")
        for name, value in sorted((relations or {}).items())
    }
    row = {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "event_id": str(uuid4()),
        "attempt_id": attempt.attempt_id,
        "round_id": attempt.round_id,
        "stage": attempt.stage,
        "event": event,
        "status": status,
        "recorded_at": utc_now(),
        "artifacts": normalized_artifacts,
        "relations": normalized_relations,
    }
    _append_jsonl(ledger_path, row)
    return row


def start_attempt(
    stage: str,
    *,
    round_id: str | None = None,
    ledger_path: Path = DEFAULT_LEDGER_PATH,
) -> Attempt:
    attempt_id = str(uuid4())
    attempt = Attempt(
        attempt_id=attempt_id,
        round_id=_safe_token(round_id or attempt_id, "round_id"),
        stage=_safe_token(stage, "stage"),
        started_at=utc_now(),
    )
    record_event(attempt=attempt, event="started", status="incomplete", ledger_path=ledger_path)
    return attempt


def behavior_identity_sha256(payload: dict[str, Any]) -> str:
    return sha256_text(json_dumps(payload))


def identity_spec_template(*, behavior_identity: dict[str, Any], stage: str) -> dict[str, Any]:
    """Return an intentionally unapproved spec skeleton; the caller must supply the baseline."""
    digest = behavior_identity_sha256(behavior_identity)
    questions_sha256 = str(behavior_identity.get("questions_sha256") or "MISSING_QUESTIONS_SHA256")
    checkout = git_identity()
    head = str(checkout.get("head") or "UNAVAILABLE_GIT_HEAD")
    deploy_version = f"{head}+dirty:{digest[:12]}" if checkout.get("dirty") else head
    baseline_deploy = "REPLACE_WITH_BASELINE_DEPLOY_VERSION"
    return {
        "run": f"aihot-fit-{stage}",
        "at": "before provider credentials and irreversible model calls",
        "baseline": {
            "id": "REPLACE_WITH_BASELINE_ID",
            "kind": "explicit_per_run",
            "authority": "REPLACE_WITH_BASELINE_AUTHORITY",
        },
        "sources": [
            {
                "id": "deploy",
                "role": "deploy_version",
                "name": "evaluated checkout version",
                "count": 1,
                "origin": "git rev-parse HEAD plus captured dirty behavior digest",
            },
            {
                "id": "config",
                "role": "effective_config",
                "name": "captured aihot-fit behavior identity",
                "count": 1,
                "origin": "ai-radar eval-fit identity-spec before provider construction",
            },
            {
                "id": "inputs",
                "role": "input_assets",
                "name": "versioned AIHOT questions",
                "count": 1,
                "origin": "questions.jsonl sha256 before provider construction",
            },
        ],
        "anchors": [
            {
                "field": "deploy_version",
                "source": "deploy",
                "covers": 1,
                "base": {
                    "value": baseline_deploy,
                    "provenance": "REPLACE_WITH_INDEPENDENT_BASELINE_DEPLOY_READOUT",
                    "locator": "REPLACE_WITH_REPO_OR_DEPLOY_RECORD_LOCATOR",
                },
                "run": {
                    "value": deploy_version,
                    "provenance": "git checkout captured by eval-fit identity-spec",
                },
            },
            {
                "field": "behavior_identity_sha256",
                "source": "config",
                "covers": 1,
                "base": {
                    "value": "REPLACE_WITH_BASELINE_BEHAVIOR_SHA256",
                    "deploy_ref": baseline_deploy,
                    "provenance": "REPLACE_WITH_BASELINE_PROVENANCE",
                },
                "run": {"value": digest, "provenance": "captured before provider construction"},
            },
            {
                "field": "questions_sha256",
                "source": "inputs",
                "covers": 1,
                "base": {
                    "value": "REPLACE_WITH_BASELINE_QUESTIONS_SHA256",
                    "deploy_ref": baseline_deploy,
                    "provenance": "REPLACE_WITH_BASELINE_QUESTIONS_PROVENANCE",
                },
                "run": {
                    "value": questions_sha256,
                    "provenance": "questions.jsonl read independently by eval-fit identity-spec",
                },
            },
        ],
        "exclusions": [],
        "cells": {
            "isolation_confirmed": 0,
            "isolation_unconfirmed": 0,
            "nonessential_confirmed": 0,
            "nonessential_unconfirmed": 0,
        },
        "confirmations": [],
    }


def _exclusive_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _identity_anchor(spec: dict[str, Any]) -> dict[str, Any] | None:
    for anchor in spec.get("anchors") or []:
        if isinstance(anchor, dict) and anchor.get("field") == "behavior_identity_sha256":
            return anchor
    return None


def _write_identity_manifest(
    *,
    attempt: Attempt,
    behavior_sha256: str,
    spec_sha256: str,
    readout_sha256: str,
    exit_code: int,
    disposition: str,
    local_receipt_dir: Path,
    manifest_dir: Path,
) -> Path:
    manifest = {
        "schema_version": IDENTITY_MANIFEST_SCHEMA_VERSION,
        "attempt_id": attempt.attempt_id,
        "round_id": attempt.round_id,
        "stage": attempt.stage,
        "checked_at": utc_now(),
        "behavior_identity_sha256": behavior_sha256,
        "spec_sha256": spec_sha256,
        "readout_sha256": readout_sha256,
        "eval_identity_exit_code": exit_code,
        "disposition": disposition,
        "local_receipt": _relative_artifact(local_receipt_dir),
    }
    target = manifest_dir / f"{attempt.attempt_id}.json"
    _exclusive_write((target), (json_dumps(manifest) + "\n").encode("utf-8"))
    return target


def run_identity_preflight(
    *,
    attempt: Attempt,
    identity_spec_path: Path,
    local_run_dir: Path,
    behavior_identity: dict[str, Any],
    ledger_path: Path = DEFAULT_LEDGER_PATH,
    manifest_dir: Path = DEFAULT_IDENTITY_MANIFEST_DIR,
    executable: Path = EVAL_IDENTITY_BIN,
) -> dict[str, Any]:
    """Run eval-identity and persist both exact local receipts and a tracked safe manifest."""
    behavior_sha256 = behavior_identity_sha256(behavior_identity)
    spec_bytes = identity_spec_path.read_bytes()
    try:
        spec = json.loads(spec_bytes)
    except (TypeError, ValueError) as exc:
        raise IdentityRejected(f"identity spec is not valid JSON: {exc}") from exc
    if not isinstance(spec, dict):
        raise IdentityRejected("identity spec must be a JSON object")
    anchor = _identity_anchor(spec)
    run_value = ((anchor or {}).get("run") or {}).get("value")
    if run_value != behavior_sha256:
        raise IdentityRejected(
            f"identity spec must carry anchors[].field=behavior_identity_sha256 with run.value={behavior_sha256}"
        )

    receipt_dir = local_run_dir / "identity" / attempt.attempt_id
    spec_copy = receipt_dir / "spec.json"
    readout_path = receipt_dir / "readout.txt"
    _exclusive_write(spec_copy, spec_bytes)
    completed = subprocess.run(
        [str(executable), str(spec_copy)],
        capture_output=True,
        check=False,
    )
    readout = completed.stdout + (b"\n[stderr]\n" + completed.stderr if completed.stderr else b"")
    _exclusive_write(readout_path, readout)
    disposition = "accepted" if completed.returncode == 0 else "rejected"
    manifest_path = _write_identity_manifest(
        attempt=attempt,
        behavior_sha256=behavior_sha256,
        spec_sha256=hashlib.sha256(spec_bytes).hexdigest(),
        readout_sha256=hashlib.sha256(readout).hexdigest(),
        exit_code=completed.returncode,
        disposition=disposition,
        local_receipt_dir=receipt_dir,
        manifest_dir=manifest_dir,
    )
    record_event(
        attempt=attempt,
        event="identity_accepted" if completed.returncode == 0 else "identity_rejected",
        status=disposition,
        artifacts={"identity_manifest": str(manifest_path)},
        relations={"behavior_identity_sha256": behavior_sha256},
        ledger_path=ledger_path,
    )
    receipt = {
        "attempt_id": attempt.attempt_id,
        "behavior_identity_sha256": behavior_sha256,
        "manifest": _relative_artifact(manifest_path),
        "spec_sha256": sha256_file(spec_copy),
        "readout_sha256": sha256_file(readout_path),
        "eval_identity_exit_code": completed.returncode,
        "disposition": disposition,
    }
    if completed.returncode != 0:
        raise IdentityRejected(
            f"eval-identity rejected the run (exit {completed.returncode}); exact readout: {readout_path}"
        )
    return receipt


def ledger_events(path: Path = DEFAULT_LEDGER_PATH) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    by_id: dict[str, str] = {}
    attempts: dict[str, dict[str, Any]] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"{path}:{number}: ledger row must be an object")
        required = {
            "schema_version",
            "event_id",
            "attempt_id",
            "round_id",
            "stage",
            "event",
            "status",
            "recorded_at",
            "artifacts",
            "relations",
        }
        missing = sorted(required - row.keys())
        if missing:
            raise ValueError(f"{path}:{number}: missing ledger fields {missing}")
        if row["schema_version"] != LEDGER_SCHEMA_VERSION:
            raise ValueError(f"{path}:{number}: unsupported schema_version {row['schema_version']!r}")
        event_id = str(row["event_id"])
        attempt_id = str(row["attempt_id"])
        try:
            UUID(event_id)
            UUID(attempt_id)
        except ValueError as exc:
            raise ValueError(f"{path}:{number}: event_id and attempt_id must be UUIDs") from exc
        round_id = _safe_token(str(row["round_id"]), "round_id")
        stage = _safe_token(str(row["stage"]), "stage")
        event = _safe_token(str(row["event"]), "event")
        status = _safe_token(str(row["status"]), "status")
        if stage not in _LEDGER_STAGES:
            raise ValueError(f"{path}:{number}: unknown stage {stage!r}")
        if event not in _LEDGER_EVENT_STATUS or status not in _LEDGER_EVENT_STATUS[event]:
            raise ValueError(f"{path}:{number}: invalid event/status pair {event!r}/{status!r}")
        try:
            datetime.fromisoformat(str(row["recorded_at"]).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{path}:{number}: recorded_at is not ISO-8601") from exc
        for field in ("artifacts", "relations"):
            mapping = row[field]
            if not isinstance(mapping, dict) or not all(
                isinstance(key, str) and isinstance(value, str) for key, value in mapping.items()
            ):
                raise ValueError(f"{path}:{number}: {field} must be a string-to-string object")
        canonical = json_dumps(row)
        if event_id in by_id and by_id[event_id] != canonical:
            raise ValueError(f"{path}:{number}: conflicting duplicate event_id {event_id}")
        if event_id in by_id:
            continue
        state = attempts.get(attempt_id)
        if state is None:
            if event != "started":
                raise ValueError(f"{path}:{number}: attempt must begin with started")
            state = {
                "round_id": round_id,
                "stage": stage,
                "identity": None,
                "terminal": False,
            }
            attempts[attempt_id] = state
        else:
            if (round_id, stage) != (state["round_id"], state["stage"]):
                raise ValueError(f"{path}:{number}: attempt metadata changed")
            if state["terminal"]:
                raise ValueError(f"{path}:{number}: event appears after terminal state")
            if event == "started":
                raise ValueError(f"{path}:{number}: attempt has more than one started event")
            if event in {"identity_accepted", "identity_rejected"}:
                if stage not in {"run", "judge"} or state["identity"] is not None:
                    raise ValueError(f"{path}:{number}: invalid identity transition")
                state["identity"] = event
            elif event == "completed":
                if stage in {"run", "judge"} and state["identity"] != "identity_accepted":
                    raise ValueError(f"{path}:{number}: completed {stage} lacks accepted identity")
                state["terminal"] = True
            elif event == "failed":
                state["terminal"] = True
        events.append(row)
        by_id[event_id] = canonical
    return events
