import pytest

from evals._shared import assets
from scripts.eval import category_evidence_study as study


@pytest.mark.parametrize("broken", ["body-missing", "body-drift", "dataset", "quote", "reviews", "model"])
def test_frozen_source_drift_rejected_before_transport(tmp_path, monkeypatch, broken):
    run, dataset, quote = tmp_path / "run", tmp_path / "dataset", tmp_path / "quote"
    assets.write_json(dataset / "manifest.json", {})
    assets.write_json(quote / "manifest.json", {})
    assets.write_jsonl(run / "body-context.jsonl", [{"text": "frozen"}])
    assets.write_json(run / "human-reviews.json", {})
    assets.write_json(run / "config.json", {"models": {"category": "model"}, "transport_identity": {}})
    inputs = {"dataset": assets.file_digest(dataset / "manifest.json"),
              "quote_sources": {str(quote / "manifest.json"): assets.file_digest(quote / "manifest.json")},
              "body_context": assets.file_digest(run / "body-context.jsonl"),
              "human_reviews": assets.file_digest(run / "human-reviews.json")}
    assets.write_json(run / "started.json", {"dataset": str(dataset), "object_identity": {
        "inputs": inputs, "behavior": {"conditional_review": False, "include_source_context": False,
        "request": {"model": "model"}, "transport": {}}}})
    if broken == "body-missing":
        (run / "body-context.jsonl").unlink()
    elif broken == "body-drift":
        (run / "body-context.jsonl").write_text('{"text":"changed"}\n')
    elif broken in {"dataset", "quote"}:
        ((dataset if broken == "dataset" else quote) / "manifest.json").write_text('{"changed":true}')
    elif broken == "reviews":
        (run / "human-reviews.json").write_text('{"changed":true}')
    else:
        (run / "config.json").write_text('{"models":{"category":"other"},"transport_identity":{}}')
    monkeypatch.setattr(study, "collect", lambda _: ({}, {}, {}, []))
    monkeypatch.setattr(study, "transport_factory", lambda *_: pytest.fail("transport reached before validation"))
    monkeypatch.setattr("sys.argv", ["study", "--source-run", str(run), "--arm", "documents",
                                    "--label", "fixture", "--env-file", str(tmp_path / "none")])
    with pytest.raises(ValueError):
        study.main()
