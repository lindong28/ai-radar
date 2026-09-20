import copy
from pathlib import Path

import pytest
from test_prefilter_eval import fixture_dataset, config

from evals._shared import assets, prefilter_eval
from evals._shared.admission_rescore import rescore


def test_rescore_preserves_object_and_failure_with_exact_input_only(tmp_path):
    old_leaf = fixture_dataset(tmp_path)
    def factory(attempts):
        def case_chat(key):
            def chat(**kwargs):
                if key == "1":
                    raise ValueError("failed response")
                return {"json": {"reason": "fixture evidence", "is_ai_related": True, "confidence": 1}}
            return chat
        return case_chat
    result = prefilter_eval.evaluate(old_leaf, config=config(), split="dev", limit=None, seed="s",
                                    chat_factory=factory, label="test", root=tmp_path)
    source = Path(result["run"])
    manifest, rows = assets.load_dataset(old_leaf)
    rows = copy.deepcopy(rows)
    rows[0]["reference"]["member"] = False
    rows[2]["input"]["title"] = "actually different input"
    rows = rows[:5]  # A former main case became unknown; not silently a negative.
    new_leaf = tmp_path / "data/news-admission/aihot-observed-membership/v1"
    assets.write_jsonl(new_leaf / "cases.jsonl", rows)
    assets.write_json(new_leaf / "manifest.json", {**manifest, "benchmark": assets.OBSERVED_ADMISSION,
        "case_count": len(rows), "files": {"cases.jsonl": assets.file_digest(new_leaf / "cases.jsonl")}})
    metrics = tmp_path / "evals/news-admission/aihot-observed-membership/metrics.json"
    metrics.parent.mkdir(parents=True)
    metrics.write_bytes((assets.ROOT / "evals/news-admission/aihot-observed-membership/metrics.json").read_bytes())
    output = rescore(source, new_leaf, label="migrated", root=tmp_path)
    assert output["case_count"] == 4 and output["omitted"] == 2 and not output["complete"]
    run = Path(output["run"])
    before = assets.read_json(source / "started.json")
    after = assets.read_json(run / "started.json")
    assert after["object_identity"] == before["object_identity"]
    assert after["new_api_attempts"] == 0
    assert not (run / "attempts").exists()
    pred = assets.read_jsonl(run / "predictions.jsonl")
    assert next(r for r in pred if r["case_id"] == "1")["status"] != "ok"
    assert {r["reason"] for r in assets.read_jsonl(run / "omitted.jsonl")} == {"not_in_new_main", "input_changed_no_reuse"}
    (source / "cases.jsonl").write_text("{}\n")
    with pytest.raises(ValueError, match="identity"):
        rescore(source, new_leaf, label="corrupted", root=tmp_path)
