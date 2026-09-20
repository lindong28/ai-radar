"""Hybrid candidate controls: retain the denominator and the model response."""
import copy
from pathlib import Path

import pytest

from evals._shared import admission_policy as policy, assets, prefilter_eval


def raw(**overrides):
    return {"case_id": "a", "title": "AI item", "url": "https://example.org/a",
            "source_id": "buzzing_hn", "source_kind": "feed", "tier": "T2",
            "published_at": "2026-09-17T00:00:00Z", "content_text": "99 HN Points", **overrides}


@pytest.mark.parametrize("points,expected", [("99 HN Points", False), ("100 HN Points", True),
                                           ("101 HN Points", True), ("no points", False)])
def test_hn_threshold_changes_prediction_not_model(points, expected):
    prediction = {"status": "ok", "output": {"member": True}, "raw": "unchanged"}
    result = policy.apply_policy(raw(content_text=points), prediction)
    assert result["output"]["member"] is expected
    assert result["model_output"] == prediction["output"]
    assert prediction["output"]["member"] is True and result["raw"] == "unchanged"
    assert policy.apply_policy(raw(source_id="other", content_text=points), prediction)["output"]["member"]


def test_reply_body_and_failure_controls():
    p = {"status": "ok", "output": {"member": True}}
    reply = raw(source_id="x", source_kind="x", extra={"referenced_tweets": [{"type": "replied_to"}]})
    assert not policy.apply_policy(reply, p)["output"]["member"]
    quoted = {**reply, "extra": {"referenced_tweets": [{"type": "quoted"}]}}
    assert policy.apply_policy(quoted, p)["output"]["member"]
    web = raw(source_id="blog", source_kind="web", content_text="AI item", fetched_at="2026-09-17T00:00:00Z")
    assert not policy.apply_policy(web, p)["output"]["member"]
    assert policy.apply_policy({**web, "source_id": "hf_daily_papers"}, p)["output"]["member"]
    assert policy.apply_policy(quoted, {"status": "ok", "output": {"member": False}})["output"]["member"] is False
    failed = policy.apply_policy(reply, {"status": "error", "output": None})
    assert failed["status"] == "error" and failed["output"] is None


def test_projection_real_consumer_preserves_cases_and_rejects_mutated_source(tmp_path):
    dataset = tmp_path / "data/news-admission/aihot-prefilter/v1"
    cases = [{"case_id": key, "split": "dev", "input": raw(case_id=key, content_text=points),
              "reference": {"member": truth}} for key, points, truth in
             [("a", "99 HN Points", False), ("b", "100 HN Points", True)]]
    assets.write_jsonl(dataset / "cases.jsonl", cases)
    assets.write_json(dataset / "manifest.json", {"schema_version": 2, "target": "news-admission",
        "benchmark": "aihot-prefilter", "version": "v1", "evaluation_mode": "pointwise",
        "case_count": 2, "files": {"cases.jsonl": assets.file_digest(dataset / "cases.jsonl")},
        "shared_evidence": ".", "evidence_files": {}})
    metrics = tmp_path / "evals/news-admission/aihot-prefilter/metrics.json"
    metrics.parent.mkdir(parents=True)
    metrics.write_bytes((assets.ROOT / "evals/news-admission/aihot-prefilter/metrics.json").read_bytes())
    model_root = tmp_path / "model"
    target_metrics = model_root / "evals/news-admission/aihot-prefilter/metrics.json"
    target_metrics.parent.mkdir(parents=True)
    target_metrics.write_bytes(metrics.read_bytes())
    result = prefilter_eval.evaluate(dataset, config={"models": {"prefilter": "deepseek-v4-flash"},
        "transport_identity": {}, "prefilter_policy": False}, split="dev", limit=None, seed="fixture", label="model",
        chat_factory=lambda attempts: lambda key: lambda **kwargs:
            {"json": {"reason": "fixture evidence", "is_ai_related": True, "confidence": 1}}, root=model_root)
    source = Path(result["run"])
    projected = policy.project(source, label="hybrid", root=tmp_path)
    assert projected["new_api_attempts"] == 0
    assert all(v["value"] == 1 for v in projected["metrics"].values())
    assert assets.read_jsonl(Path(projected["run"]) / "cases.jsonl") == assets.read_jsonl(source / "cases.jsonl")
    rows = assets.read_jsonl(source / "predictions.jsonl")
    mutated = copy.deepcopy(rows)
    mutated[0]["output"]["member"] = False
    # Corrupt only the upstream exported decision, not its archived model result.
    import json
    (source / "predictions.jsonl").write_text("\n".join(json.dumps(r) for r in mutated) + "\n")
    with pytest.raises(ValueError, match="differs from model"):
        policy.project(source, label="corrupted", root=tmp_path)
