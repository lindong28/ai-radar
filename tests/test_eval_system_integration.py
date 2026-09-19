"""Exercise the actual runner-to-metric and judge-to-SDK consumer boundaries."""

import json
from types import SimpleNamespace

from evals._shared import assets, judge, runner
from evals._shared.transport import DurableChat


def test_judge_and_durable_transport_share_real_request_contract(tmp_path):
    requests = []

    def create(**kwargs):
        requests.append(kwargs)
        assert json.loads(kwargs["messages"][1]["content"])["candidate"] == "candidate"
        result = SimpleNamespace(model=judge.DEFAULT_MODEL, usage=None,
                                 choices=[SimpleNamespace(message=SimpleNamespace(content='{"reason":"compared", "score":1}'))])
        result.model_dump = lambda **_: {"model": result.model, "choices": [{"message": {"content": result.choices[0].message.content}}]}
        return result

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)), close=lambda: None)
    chat = DurableChat(tmp_path, provider="ark", base_url="https://example.invalid/v3", api_key="fixture-secret",
                       client_factory=lambda **_: client)
    result = judge.judge_text("id", "title", {"title": "original"}, "reference", "candidate", chat=chat, provider="ark")
    assert result["status"] == "ok" and result["score"] == 1
    assert len(requests) == 1
    record = assets.read_json(next(tmp_path.glob("*.json")))
    assert record["status"] == "ok" and record["cost_usd"] is None


def test_four_object_run_uses_distinct_o1_cases_and_preserves_queries(tmp_path, monkeypatch):
    data = tmp_path / "data"
    evidence = data / "evidence"
    assets.write_json(evidence / "receipt.json", {"fixture": True})
    cases = [{"case_id": str(i), "input": {"case_id": str(i)}, "reference": {"member": True, "featured": False, "score": 80, "category": "ai-models"}, "split": "dev"} for i in range(2)]
    for target, benchmark in assets.benchmark_pairs():
        definition = assets.ROOT / "evals" / target / benchmark
        leaf = tmp_path / "evals" / target / benchmark
        for name in ("README.md", "evaluate.py", "metrics.json"):
            leaf.mkdir(parents=True, exist_ok=True)
            (leaf / name).write_bytes((definition / name).read_bytes())
    for target, benchmark in assets.BENCHMARKS.items():
        selected = cases[:1] if target == "news-admission" else cases
        destination = data / benchmark / target / "v1"
        assets.write_jsonl(destination / "cases.jsonl", selected)
        assets.write_json(destination / "manifest.json", {"target": target, "benchmark": benchmark, "version": "v1", "schema_version": 1,
            "case_count": len(selected), "files": {"cases.jsonl": assets.file_digest(destination / "cases.jsonl")},
            "shared_evidence": str(evidence), "evidence_files": {"receipt.json": assets.file_digest(evidence / "receipt.json")},
            "window": {"end_exclusive": "2026-09-17T12:00:00Z"}})
    monkeypatch.setattr(runner, "identity", lambda _: {"source_sha256": {}, "requests": {}, "now": "fixed", "archive_initial": []})
    monkeypatch.setattr(runner, "run_pool", lambda cases, *_, **__: ([{"input": c["input"], "stage_results": {}} for c in cases], {"stage_calls": 0}))
    monkeypatch.setattr(runner, "project_pool", lambda rows, _: [{"case_id": r["input"]["case_id"], "target_status": {t: "ok" for t in assets.BENCHMARKS},
        "stage_results": {"prefilter": {"status": "ok", "output": {"is_ai_related": True}}, "enrich": {"status": "ok"}},
        "pool_error_item_ids": ["other-failed-item"],
        "output": {"member": False, "featured": False, "score": 80, "category": "model", "title": "independent title", "reason": "uncertain"}} for r in rows])
    primary = data / "aihot-all-members/news-admission/v1"
    results = runner.evaluate(primary, config={}, chat_factory=lambda _: (lambda case_id: None), root=tmp_path)
    assert results["news-admission"]["metrics"]["recall"]["value"] == 1
    assert results["visible-score"]["metrics"]["mae"]["value"] == 0
    assert results["content-enrichment"]["metrics"]["category_accuracy"]["value"] == 1
    assert results["featured-members"]["metrics"]["recall"]["value"] is None
    index = assets.rebuild_index(tmp_path)
    assert len(index) == 10
    metadata = assets.read_json(__import__('pathlib').Path(results["news-admission"]["experiment"]) / "metadata.json")
    assert metadata["case_ids"] == ["0"]
    from evals._shared.assessment import source_run
    monkeypatch.chdir(tmp_path)
    relative = __import__('pathlib').Path(results["content-enrichment"]["experiment"]).relative_to(tmp_path)
    _, selected, predictions = source_run(relative, tmp_path)
    assert len(selected) == len(predictions) == 2
    assert all(p["status"] == "ok" and p["output"]["title"] == "independent title" and p["output"]["reason"] is None for p in predictions)
    assert (tmp_path / results["news-admission"]["experiment"] / "started.json").is_file()
