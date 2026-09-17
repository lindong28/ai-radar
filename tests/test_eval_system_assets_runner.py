"""Offline consumer tests for dataset identity, artifacts and replay caching."""

import copy
import json
from datetime import UTC, datetime

import pytest

from evals._shared import assets, cli, dataset, runner


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def frozen_dataset(root, target="news-admission"):
    benchmark = assets.BENCHMARKS[target]
    leaf = root / benchmark / target / "v1"
    evidence = root / "shared"
    put(evidence / "evidence.json", {"original": True})
    row = {"case_id": "a", "input": {"body": "raw"}, "reference": {"member": True}, "split": "dev"}
    assets.write_jsonl(leaf / "cases.jsonl", [row])
    manifest = {
        "target": target,
        "benchmark": benchmark,
        "version": "v1",
        "schema_version": 1,
        "case_count": 1,
        "files": {"cases.jsonl": assets.file_digest(leaf / "cases.jsonl")},
        "shared_evidence": str(evidence),
        "evidence_files": {"evidence.json": assets.file_digest(evidence / "evidence.json")},
    }
    put(leaf / "manifest.json", manifest)
    return leaf, evidence, manifest


@pytest.mark.parametrize("changed", ["cases", "evidence"])
def test_dataset_checks_question_and_evidence_bytes(tmp_path, changed):
    leaf, evidence, _ = frozen_dataset(tmp_path)
    manifest, rows = assets.load_dataset(leaf, "news-admission")
    assert manifest["case_count"] == len(rows) == 1
    path = leaf / "cases.jsonl" if changed == "cases" else evidence / "evidence.json"
    path.write_text(path.read_text() + "\n")
    with pytest.raises(ValueError, match="integrity mismatch"):
        assets.load_dataset(leaf)


def test_dataset_layout_target_and_path_traversal_checks(tmp_path):
    leaf, _, manifest = frozen_dataset(tmp_path)
    with pytest.raises(ValueError, match="another target"):
        assets.load_dataset(leaf, "visible-score")
    manifest["version"] = "v2"
    put(leaf / "manifest.json", manifest)
    with pytest.raises(ValueError, match="hierarchy"):
        assets.load_dataset(leaf)
    manifest["version"] = "v1"
    manifest["files"]["../outside.json"] = "x"
    put(leaf / "manifest.json", manifest)
    with pytest.raises(ValueError, match="integrity mismatch"):
        assets.load_dataset(leaf)


def test_existing_version_files_cannot_be_overwritten(tmp_path):
    leaf, _, _ = frozen_dataset(tmp_path)
    before = (leaf / "cases.jsonl").read_bytes()
    with pytest.raises(FileExistsError):
        assets.write_jsonl(leaf / "cases.jsonl", [])
    assert (leaf / "cases.jsonl").read_bytes() == before


def definitions(root):
    for target, benchmark in assets.BENCHMARKS.items():
        source = assets.ROOT / "evals" / target / benchmark
        for name in ("README.md", "evaluate.py", "metrics.json"):
            path = root / "evals" / target / benchmark / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((source / name).read_bytes())


def test_leaf_layout_and_run_path_contract(tmp_path):
    definitions(tmp_path)
    assets.validate_layout(tmp_path)
    run, experiment = assets.create_run(
        tmp_path, "news-admission", "v1", created_at=datetime(2026, 9, 17, 1, 2, 3, tzinfo=UTC)
    )
    suffix = "news-admission/aihot-all-members/v1/2026-09-17/01-02-03"
    assert run == tmp_path / "runs" / suffix
    assert experiment == tmp_path / "experiments" / suffix
    with pytest.raises(FileExistsError):
        assets.create_run(tmp_path, "news-admission", "v1", created_at=datetime(2026, 9, 17, 1, 2, 3, tzinfo=UTC))
    with pytest.raises(ValueError, match="timezone"):
        assets.create_run(tmp_path, "news-admission", "v1", created_at=datetime(2026, 9, 17))
    (tmp_path / "evals/news-admission/aihot-all-members/evaluate.py").unlink()
    with pytest.raises(ValueError, match="missing canonical"):
        assets.validate_layout(tmp_path)


def archived(root, second, *, precision=0.5, smoke=None, case_identity="same-cases", status="trusted"):
    run, experiment = assets.create_run(
        root, "news-admission", "v1", created_at=datetime(2026, 9, 17, 1, 2, second, tzinfo=UTC)
    )
    metadata = {
        "target": "news-admission",
        "benchmark": "aihot-all-members",
        "version": "v1",
        "case_identity": case_identity,
        "split": "all",
        "scorer_identity": {"sha256": "scorer"},
        "smoke": smoke,
        "status": "complete",
    }
    result = {
        "metrics": {"precision": {"value": precision, "status": status}, "recall": {"value": 0.5, "status": "trusted"}}
    }
    rows = assets.archive_metrics(root, run, experiment, result, metadata)
    return run, experiment, rows


def test_archive_index_keeps_exact_original_values_and_detects_drift(tmp_path):
    definitions(tmp_path)
    run, experiment, rows = archived(tmp_path, 1, precision=1 / 3)
    assert rows[0]["metric_value"] == 1 / 3
    assert assets.rebuild_index(tmp_path) == rows
    summary = assets.read_json(experiment / "metrics/summary.json")
    summary[0]["metric_value"] = 0.33
    put(experiment / "metrics/summary.json", summary)
    with pytest.raises(ValueError, match="projection drift"):
        assets.rebuild_index(tmp_path)
    assert assets.read_json(run / "scores.json")["metrics"]["precision"]["value"] == 1 / 3


@pytest.mark.parametrize("mutation", ["case_identity", "scorer_identity", "version", "split", "smoke"])
def test_compare_rejects_different_questions_rulers_and_smoke(tmp_path, mutation):
    definitions(tmp_path)
    _, baseline, _ = archived(tmp_path, 1)
    _, candidate, _ = archived(tmp_path, 2, precision=1)
    assert runner.compare(baseline, candidate, root=tmp_path)["accepted"] is True
    metadata = assets.read_json(candidate / "metadata.json")
    metadata[mutation] = 3 if mutation == "smoke" else "different"
    put(candidate / "metadata.json", metadata)
    with pytest.raises(ValueError):
        runner.compare(baseline, candidate, root=tmp_path)


def test_untrusted_improvement_does_not_win(tmp_path):
    definitions(tmp_path)
    _, baseline, _ = archived(tmp_path, 1)
    _, candidate, _ = archived(tmp_path, 2, precision=1, status="untrusted")
    result = runner.compare(baseline, candidate, root=tmp_path)
    assert result["accepted"] is False
    assert result["untrusted_or_missing"] == ["precision"]


@pytest.mark.parametrize("direction,expected", [("higher", True), ("lower", False), ("neutral", False), ("unknown", False)])
def test_compare_reads_current_metric_direction_without_changing_scores(tmp_path, direction, expected):
    definitions(tmp_path)
    _, baseline, _ = archived(tmp_path, 1)
    run, candidate, _ = archived(tmp_path, 2, precision=1)
    before = (run / "scores.json").read_bytes()
    path = tmp_path / "evals/news-admission/aihot-all-members/metrics.json"
    definitions_json = assets.read_json(path)
    definitions_json["metrics"][0]["direction"] = direction
    put(path, definitions_json)
    assert runner.compare(baseline, candidate, root=tmp_path)["accepted"] is expected
    assert (run / "scores.json").read_bytes() == before


@pytest.mark.parametrize("changed", ["value", "status"])
def test_compare_cannot_accept_summary_changed_from_archived_score(tmp_path, changed):
    definitions(tmp_path)
    _, baseline, _ = archived(tmp_path, 1)
    _, candidate, _ = archived(
        tmp_path, 2, precision=0.5 if changed == "value" else 1, status="trusted" if changed == "value" else "untrusted"
    )
    rows = assets.read_json(candidate / "metrics/summary.json")
    rows[0]["metric_value" if changed == "value" else "status"] = 1 if changed == "value" else "trusted"
    put(candidate / "metrics/summary.json", rows)
    with pytest.raises(ValueError, match="drift|integrity|original"):
        runner.compare(baseline, candidate, root=tmp_path)


def raw_case():
    raw = {
        "case_id": "a",
        "item_id": "a",
        "title": "Model release",
        "url": "https://example.org/a",
        "source_id": "source",
        "tier": "T1",
        "author": None,
        "published_at": "2026-09-17T01:00:00Z",
        "content_text": "Model supports tools",
        "content_hash": "hash",
        "fetched_at": "2026-09-17T01:01:00Z",
        "source_enabled": True,
        "source_kind": "feed",
    }
    return {"case_id": "a", "input": raw, "reference": {"score": 80, "category": "model"}, "split": "dev"}


def fake_chat(calls):
    def chat(*, stage, prompt, request):
        calls.append(stage)
        payload = {
            "prefilter": {"is_ai_related": True, "confidence": 0.9},
            "score": {
                **dict.fromkeys(("relevance", "density", "recency", "authority", "engineering", "significance"), 8),
                "reasoning": "Detailed model release",
                "topics": [],
            },
            "enrich": {
                "title_zh": "新模型支持工具调用",
                "summary_zh": "新模型提供工具调用能力，团队公开了部署方式和性能测试结果。",
                "why_recommend": "这次发布公开了工具调用接口的实现和部署测试结果，为模型工程团队评估接入成本提供依据。",
                "primary_category": "model",
                "is_opinion": False,
                "tags": ["模型发布"],
            },
        }[stage]
        return {"json": payload, "model": request["model"], "provider": "fixture", "usage": {}, "raw": payload}

    return chat


@pytest.mark.parametrize("changed", ["model", "raw"])
def test_cache_reuses_successes_and_changes_invalidate(tmp_path, changed):
    calls = []
    config = {"transport_identity": {"provider": "fixture", "base_url": "https://invalid"}}
    case = raw_case()
    def factory(_):
        return fake_chat(calls)
    first, initial = runner.run_pool([case], config, chat_factory=factory, cache=tmp_path, workers=1)
    assert initial["stage_calls"] == 3
    assert all(row["status"] == "ok" for row in first[0]["stage_results"].values())
    replay, cached = runner.run_pool([case], config, chat_factory=factory, cache=tmp_path, workers=1)
    assert replay == first
    assert cached["stage_calls"] == 0 and cached["cache_hits"] == 3
    assert len(calls) == 3
    if changed == "model":
        config["models"] = {"score": "explicit-new-model"}
    else:
        case = copy.deepcopy(case)
        case["input"]["content_text"] = "Different source body"
    _, fresh = runner.run_pool([case], config, chat_factory=factory, cache=tmp_path, workers=1)
    assert fresh["stage_calls"] > 0
    assert len(calls) > 3


def test_failed_calls_are_not_success_cache(tmp_path):
    calls = []

    def failing(**kwargs):
        calls.append(kwargs["stage"])
        raise TimeoutError("fixture")

    config = {"transport_identity": {"provider": "fixture"}}
    for _ in range(2):
        rows, timing = runner.run_pool([raw_case()], config, chat_factory=lambda _: failing, cache=tmp_path, workers=1)
        assert timing["cache_hits"] == 0
        assert all(row["status"] == "error" for row in rows[0]["stage_results"].values())
    assert len(calls) == 6


def raw_runs(root, monkeypatch, *, second_slot=True):
    records = {}
    entries = [("failed", "2026-09-17T00:00:00Z", "error"), ("retry", "2026-09-17T00:05:00Z", "success")]
    if second_slot:
        entries.append(("second", "2026-09-17T00:15:00Z", "success"))
    for run_id, started, status in entries:
        manifest = {
            "run_id": run_id,
            "started_at": started,
            "sources": {"source": {"status": status, "configuration_sha256": "stable"}},
        }
        put(root / "runs" / run_id / "manifest.json", manifest)
        records[run_id] = (manifest, [{"source_id": "source", "url": f"https://example.org/{run_id}"}])
    monkeypatch.setattr(dataset, "read_run", lambda _, run_id: records[run_id])
    return records


def test_raw_successful_retry_covers_slot_and_retains_failure(tmp_path, monkeypatch):
    raw_runs(tmp_path, monkeypatch)
    rows, coverage = dataset.read_raw_interval(tmp_path, "2026-09-17T00:00:00Z", "2026-09-17T00:30:00Z", {"source"})
    assert len(rows) == 2
    assert coverage["slot_count"] == 2
    assert coverage["run_ids"] == ["retry", "second"]
    assert coverage["failed_attempts"] == [{"run_id": "failed", "source_id": "source", "status": "error"}]


def test_missing_cadence_slot_is_rejected(tmp_path, monkeypatch):
    raw_runs(tmp_path, monkeypatch, second_slot=False)
    with pytest.raises(ValueError, match="uncovered source/slots"):
        dataset.read_raw_interval(tmp_path, "2026-09-17T00:00:00Z", "2026-09-17T00:30:00Z", {"source"})


def test_cli_validation_reports_layout_not_live_success(monkeypatch, capsys):
    monkeypatch.setattr(cli, "validate_layout", lambda: None)
    assert cli.main(["--json", "validate"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["live_evaluation"] == "not_run"
