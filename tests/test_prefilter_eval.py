"""Independent O1 consumer tests, including production-call parity and failure controls."""
import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from evals._shared import assets, prefilter_eval
from evals._shared.inference import _item, predict_one


def cases():
    return [{"case_id": str(i), "split": "dev" if i < 6 else "regression",
             "input": {"case_id": str(i), "title": f"Title {i}", "source_id": "example",
                       "url": f"https://example.org/{i}", "tier": "T2", "published_at": "2026-09-16T12:00:00Z",
                       "content_text": f"content {i}"}, "reference": {"member": i % 2 == 0}}
            for i in range(10)]


def config():
    return {"models": {"prefilter": "deepseek-v4-flash"},
            "transport_identity": {"provider": "ark", "base_url": "https://example.org/v1"}}


def fixture_dataset(tmp_path):
    leaf = tmp_path / "data/news-admission/aihot-prefilter/v1"
    assets.write_jsonl(leaf / "cases.jsonl", cases())
    assets.write_json(leaf / "manifest.json", {
        "schema_version": 2, "target": "news-admission", "benchmark": "aihot-prefilter",
        "version": "v1", "evaluation_mode": "pointwise", "case_count": 10,
        "files": {"cases.jsonl": assets.file_digest(leaf / "cases.jsonl")},
        "shared_evidence": ".", "evidence_files": {},
    })
    metrics = tmp_path / "evals/news-admission/aihot-prefilter/metrics.json"
    metrics.parent.mkdir(parents=True)
    metrics.write_bytes((assets.ROOT / "evals/news-admission/aihot-prefilter/metrics.json").read_bytes())
    return leaf


def test_sampling_ignores_labels_and_keeps_splits_disjoint():
    original = cases()
    changed = copy.deepcopy(original)
    for c in changed:
        c["reference"]["member"] = not c["reference"]["member"]
    ids = lambda rows: {c["case_id"] for c in rows}
    dev = prefilter_eval.select_cases(original, "dev", 3, "seed")
    assert ids(dev) == ids(prefilter_eval.select_cases(changed[::-1], "dev", 3, "seed"))
    assert not ids(dev) & ids(prefilter_eval.select_cases(original, "regression", 3, "seed"))
    with pytest.raises(ValueError):
        prefilter_eval.select_cases(original, "dev", 0, "seed")


@pytest.mark.parametrize("answer", [True, False])
def test_production_prompt_request_and_conversion_parity(monkeypatch, answer):
    from airadar.provider import deepseek_v32
    monkeypatch.setenv("ARK_API_KEY", "fixture-only")
    monkeypatch.delenv("AI_RADAR_FORCE_HEURISTIC", raising=False)
    monkeypatch.setenv("AI_RADAR_PREFILTER", "deepseek_v32")
    calls = []
    payload = {"is_ai_related": answer, "confidence": 0.7}
    def production_call(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(json=payload, model="deepseek-v4-flash", provider="ark")
    monkeypatch.setattr(deepseek_v32, "chat_json", production_call)
    raw = {**cases()[0]["input"], "reference": "SECRET_REFERENCE_MUST_NOT_BE_RENDERED"}
    expected = deepseek_v32.DeepSeekV32Prefilter().is_ai_related(_item(raw))
    def chat(*, stage, prompt, request):
        assert stage == "prefilter"
        assert prompt == {k: calls[0][k] for k in ("system", "user")}
        assert "SECRET_REFERENCE" not in prompt["user"]
        assert request == {"model": calls[0]["default_model"], "temperature": calls[0]["temperature"],
                           "max_tokens": calls[0]["max_tokens"]}
        return {"json": payload, "model": "deepseek-v4-flash"}
    actual = predict_one("prefilter", raw, {**config(), "chat": chat})
    assert set(actual["stage_results"]) == {"prefilter"}
    assert actual["output"]["is_ai_related"] is expected.is_ai_related


@pytest.mark.parametrize("mode", ["correct", "flipped", "failed"])
def test_real_runner_archives_and_scores_both_classes(tmp_path, mode):
    leaf = fixture_dataset(tmp_path)
    calls = []
    def factory(attempts):
        def for_case(key):
            def chat(**kwargs):
                calls.append(kwargs)
                assert kwargs["stage"] == "prefilter"
                if mode == "failed":
                    raise TimeoutError("fixture")
                value = int(key) % 2 == 0
                return {"json": {"is_ai_related": value if mode == "correct" else not value, "confidence": 1}}
            return chat
        return for_case
    result = prefilter_eval.evaluate(leaf, config=config(), split="dev", limit=None, seed="s",
                                    chat_factory=factory, label=mode, root=tmp_path, workers=2)
    assert len(calls) == 6
    assert result["complete"] is (mode != "failed")
    value = {"correct": 1, "flipped": 0, "failed": None}[mode]
    assert result["metrics"]["precision"]["value"] == value
    assert result["metrics"]["recall"]["value"] == value
    run = Path(result["run"])
    assert len(list((run / "items").glob("*.json"))) == 6
    assert (run / "started.json").is_file()
    assert assets.rebuild_index(tmp_path)[0]["metric_value"] == value
