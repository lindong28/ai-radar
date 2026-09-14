from __future__ import annotations

import json
import subprocess
from hashlib import sha256
from pathlib import Path

import pytest

from airadar.eval.aihot_fit import judge as judge_module
from airadar.eval.aihot_fit import run as run_module
from airadar.eval.aihot_fit.governance import (
    EVAL_IDENTITY_BIN,
    IdentityRejected,
    behavior_identity_sha256,
    identity_spec_template,
    ledger_events,
    record_event,
    run_identity_preflight,
    start_attempt,
)


def _executable(path: Path, exit_code: int) -> Path:
    path.write_text(f"#!/bin/sh\nprintf 'identity readout\\n'\nexit {exit_code}\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _spec(path: Path, behavior: dict[str, object]) -> Path:
    digest = behavior_identity_sha256(behavior)
    payload = {
        "run": "local",
        "at": "before provider credentials",
        "baseline": {"id": "expected", "kind": "explicit", "authority": "caller"},
        "sources": [{"id": "obj", "role": "behavior", "name": "behavior", "count": 1, "origin": "caller"}],
        "anchors": [
            {
                "field": "behavior_identity_sha256",
                "source": "obj",
                "covers": 1,
                "base": {"value": digest, "provenance": "explicit baseline"},
                "run": {"value": digest, "provenance": "captured before provider construction"},
            }
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
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_identity_preflight_persists_receipt_before_accepting(tmp_path: Path) -> None:
    ledger = tmp_path / "history.jsonl"
    manifests = tmp_path / "manifests"
    behavior = {"prompt": "sha", "questions": "sha"}
    attempt = start_attempt("run", round_id="round-1", ledger_path=ledger)

    receipt = run_identity_preflight(
        attempt=attempt,
        identity_spec_path=_spec(tmp_path / "spec.json", behavior),
        local_run_dir=tmp_path / "run",
        behavior_identity=behavior,
        ledger_path=ledger,
        manifest_dir=manifests,
        executable=_executable(tmp_path / "eval-identity", 0),
    )

    assert receipt["disposition"] == "accepted"
    assert (manifests / f"{attempt.attempt_id}.json").is_file()
    assert [row["event"] for row in ledger_events(ledger)] == ["started", "identity_accepted"]


def test_identity_rejection_still_persists_manifest_and_event(tmp_path: Path) -> None:
    ledger = tmp_path / "history.jsonl"
    manifests = tmp_path / "manifests"
    behavior = {"prompt": "sha"}
    attempt = start_attempt("judge", ledger_path=ledger)

    with pytest.raises(IdentityRejected, match="exit 1"):
        run_identity_preflight(
            attempt=attempt,
            identity_spec_path=_spec(tmp_path / "spec.json", behavior),
            local_run_dir=tmp_path / "run",
            behavior_identity=behavior,
            ledger_path=ledger,
            manifest_dir=manifests,
            executable=_executable(tmp_path / "eval-identity", 1),
        )

    manifest = json.loads((manifests / f"{attempt.attempt_id}.json").read_text())
    assert manifest["disposition"] == "rejected"
    assert [row["event"] for row in ledger_events(ledger)] == ["started", "identity_rejected"]


def test_run_rejects_identity_before_reading_provider_credentials(monkeypatch, tmp_path: Path) -> None:
    questions = tmp_path / "questions.jsonl"
    questions.write_text("", encoding="utf-8")
    credentials_read = False

    def reject(**_kwargs):
        raise IdentityRejected("no")

    def credentials():
        nonlocal credentials_read
        credentials_read = True
        return {}

    monkeypatch.setattr(run_module, "run_identity_preflight", reject)
    monkeypatch.setattr(run_module, "require_ark_only", credentials)

    with pytest.raises(IdentityRejected):
        run_module.run_stages(
            questions_path=questions,
            out_dir=tmp_path / "run",
            identity_spec_path=tmp_path / "spec.json",
            ledger_path=tmp_path / "history.jsonl",
            identity_manifest_dir=tmp_path / "manifests",
        )

    assert credentials_read is False
    assert [row["event"] for row in ledger_events(tmp_path / "history.jsonl")] == ["started", "failed"]


def test_conflicting_duplicate_event_id_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "history.jsonl"
    start_attempt("build", ledger_path=path)
    first = ledger_events(path)[0]
    conflicting = {**first, "round_id": "different-round"}
    path.write_text(path.read_text(encoding="utf-8") + json.dumps(conflicting) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="conflicting duplicate"):
        ledger_events(path)


def test_invalid_ledger_transition_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "history.jsonl"
    attempt = start_attempt("run", ledger_path=path)
    record_event(attempt=attempt, event="completed", status="complete", ledger_path=path)

    with pytest.raises(ValueError, match="lacks accepted identity"):
        ledger_events(path)


def test_run_executes_the_same_questions_snapshot_that_identity_checked(monkeypatch, tmp_path: Path) -> None:
    questions = tmp_path / "questions.jsonl"
    initial = b'{"question_id":"q1","input":{"item_id":"i1"},"reference":{}}\n'
    questions.write_bytes(initial)
    captured: dict[str, object] = {}

    def preflight(**kwargs):
        captured["behavior"] = kwargs["behavior_identity"]
        questions.write_text('{"question_id":"q2","input":{"item_id":"i2"},"reference":{}}\n', encoding="utf-8")
        return {"disposition": "accepted"}

    def impl(**kwargs):
        captured["questions"] = kwargs["questions_snapshot"]
        captured["questions_sha256"] = kwargs["questions_sha256"]
        return {"stopped_early": False, "questions_sha256": kwargs["questions_sha256"]}

    monkeypatch.setattr(run_module, "run_identity_preflight", preflight)
    monkeypatch.setattr(run_module, "_run_stages_impl", impl)
    run_module.run_stages(
        questions_path=questions,
        out_dir=tmp_path / "run",
        identity_spec_path=tmp_path / "spec.json",
        ledger_path=tmp_path / "history.jsonl",
    )

    assert [row["question_id"] for row in captured["questions"]] == ["q1"]
    assert captured["questions_sha256"] == sha256(initial).hexdigest()
    assert captured["behavior"]["questions_sha256"] == sha256(initial).hexdigest()


def test_judge_executes_the_same_artifact_snapshots_that_identity_checked(monkeypatch, tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    questions_bytes = b'{"question_id":"q1","input":{"item_id":"i1"},"reference":{}}\n'
    questions = tmp_path / "questions.jsonl"
    questions.write_bytes(questions_bytes)
    questions_digest = sha256(questions_bytes).hexdigest()
    (run_dir / "run.json").write_text(
        json.dumps({"run_id": "r1", "questions_sha256": questions_digest}), encoding="utf-8"
    )
    (run_dir / "outputs.jsonl").write_text('{"question_id":"q1"}\n', encoding="utf-8")
    captured: dict[str, object] = {}

    def preflight(**kwargs):
        captured["behavior"] = kwargs["behavior_identity"]
        questions.write_text('{"question_id":"q2","input":{"item_id":"i2"},"reference":{}}\n', encoding="utf-8")
        (run_dir / "outputs.jsonl").write_text('{"question_id":"q2"}\n', encoding="utf-8")
        (run_dir / "run.json").write_text('{"run_id":"changed"}', encoding="utf-8")
        return {"disposition": "accepted"}

    def impl(**kwargs):
        captured["questions"] = kwargs["questions_snapshot"]
        captured["rows"] = kwargs["rows_snapshot"]
        captured["run_meta"] = kwargs["run_meta_snapshot"]
        return {"stopped_early": False, "questions_sha256": questions_digest}

    monkeypatch.setattr(judge_module, "run_identity_preflight", preflight)
    monkeypatch.setattr(judge_module, "_run_judge_impl", impl)
    judge_module.run_judge(
        run_dir=run_dir,
        questions_path=questions,
        identity_spec_path=tmp_path / "spec.json",
        ledger_path=tmp_path / "history.jsonl",
    )

    assert set(captured["questions"]) == {"q1"}
    assert captured["rows"] == [{"question_id": "q1"}]
    assert captured["run_meta"]["run_id"] == "r1"
    assert captured["behavior"]["dimensions"] == ["summary", "reason"]
    assert "title" not in captured["behavior"]["judge"]["prompt_sha256"]


def test_identity_spec_template_covers_all_required_identity_roles() -> None:
    behavior = {"questions_sha256": "a" * 64, "prompt": "b" * 64}

    payload = identity_spec_template(behavior_identity=behavior, stage="run")

    assert {source["role"] for source in payload["sources"]} == {
        "deploy_version",
        "effective_config",
        "input_assets",
    }
    assert {anchor["field"] for anchor in payload["anchors"]} == {
        "deploy_version",
        "behavior_identity_sha256",
        "questions_sha256",
    }
    assert payload["anchors"][1]["run"]["value"] == behavior_identity_sha256(behavior)


@pytest.mark.skipif(not EVAL_IDENTITY_BIN.is_file(), reason="user-scope eval-identity is not installed")
def test_identity_spec_template_is_accepted_after_explicit_baseline_is_filled(tmp_path: Path) -> None:
    behavior = {"questions_sha256": "a" * 64, "prompt": "b" * 64}
    payload = identity_spec_template(behavior_identity=behavior, stage="run")
    payload["baseline"] = {
        "id": "current-explicit-baseline",
        "kind": "explicit_per_run",
        "authority": "test fixture",
    }
    deploy_version = payload["anchors"][0]["run"]["value"]
    for anchor in payload["anchors"]:
        anchor["base"]["value"] = anchor["run"]["value"]
        anchor["base"]["provenance"] = "independent test baseline fixture"
        if anchor["field"] == "deploy_version":
            anchor["base"]["locator"] = "repo:test-fixture"
        else:
            anchor["base"]["deploy_ref"] = deploy_version
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps(payload), encoding="utf-8")

    completed = subprocess.run([str(EVAL_IDENTITY_BIN), str(spec)], capture_output=True, text=True, check=False)

    assert completed.returncode == 0, completed.stderr
    assert "→ 可否代表 current-explicit-baseline 口径：是" in completed.stdout
