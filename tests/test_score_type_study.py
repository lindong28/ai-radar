import json

import pytest

from evals._shared import assets
from evals._shared import score_type_study as study


@pytest.mark.parametrize("kind", study.TYPES)
def test_reason_first_type(kind):
    assert study.validate_type({"reason": "material evidence", "news_type": kind}) == kind


@pytest.mark.parametrize("payload", [
    {"news_type": "release", "reason": "late"},
    {"reason": "", "news_type": "release"},
    {"reason": "valid", "news_type": "company"},
    {"reason": "valid", "news_type": "release", "score": 60},
])
def test_reject_invalid_type(payload):
    with pytest.raises(ValueError):
        study.validate_type(payload)


def test_classifier_input_is_original_a11_projection():
    raw = {"id": "input", "title": "visible", "content_text": "body", "source_id": "source",
           "tier": "T1", "url": "https://example.com", "author": "author",
           "published_at": "2026-09-20", "reference": {"score": 987654}}
    prompt = study.classifier_prompt(raw, "{{ item.title }}:{{ item.content_text }}")
    assert prompt["user"] == "visible:body"
    assert "987654" not in str(prompt)


def test_load_validates_raw_order_and_identity(tmp_path):
    cases = [{"case_id": "a", "input": {"title": "text"}}]
    hashes = {"cases.jsonl": "abc"}
    assets.write_json(tmp_path/"started.json", {"source_sha256": hashes})
    assets.write_json(tmp_path/"result.json", {"complete": True, "identity_unchanged": True})
    row = {"case_id": "a", "input_sha256": assets.digest(cases[0]["input"]),
           "status": "ok", "news_type": "release", "response": {"raw": {"choices": [
               {"message": {"content": '{"reason":"new release","news_type":"release"}'}}]}}}
    assets.write_jsonl(tmp_path/"classifications.jsonl", [row])
    assert study.load_types(tmp_path, cases, hashes) == {"a": "release"}
    with pytest.raises(ValueError, match="source changed"):
        study.load_types(tmp_path, cases, {"cases.jsonl": "changed"})
    with pytest.raises(ValueError, match="input mismatch"):
        study.load_types(tmp_path, [{"case_id": "a", "input": {"title": "changed"}}], hashes)
    row["response"]["raw"]["choices"][0]["message"]["content"] = json.dumps({"news_type": "release", "reason": "late"})
    invalid = tmp_path/"invalid"
    assets.write_json(invalid/"started.json", {"source_sha256": hashes})
    assets.write_json(invalid/"result.json", {"complete": True, "identity_unchanged": True})
    assets.write_jsonl(invalid/"classifications.jsonl", [row])
    with pytest.raises(ValueError):
        study.load_types(invalid, cases, hashes)


@pytest.mark.parametrize("complete,unchanged", [(False, True), (True, False), (False, False)])
def test_failed_terminal_rejected(tmp_path, complete, unchanged):
    assets.write_json(tmp_path/"result.json", {"complete": complete, "identity_unchanged": unchanged})
    with pytest.raises(ValueError, match="incomplete or identity drift"):
        study.load_types(tmp_path, [], {})


@pytest.mark.parametrize("wrapper", ["{}", "```json\n{}\n```", "[{}]"])
def test_load_uses_transport_parser(tmp_path, wrapper):
    from airadar.provider.deepseek_chat import _parse_json_object
    content = wrapper.format('{"reason":"new model","news_type":"release"}')
    parsed = _parse_json_object(content)
    assert study.validate_type(parsed) == "release"
    cases = [{"case_id": "a", "input": {}}]
    assets.write_json(tmp_path/"started.json", {"source_sha256": {}})
    assets.write_json(tmp_path/"result.json", {"complete": True, "identity_unchanged": True})
    assets.write_jsonl(tmp_path/"classifications.jsonl", [{"case_id": "a", "input_sha256": assets.digest({}),
        "status": "ok", "news_type": "release", "response": {"raw": {"choices": [{"message": {"content": content}}]}}}])
    assert study.load_types(tmp_path, cases, {}) == {"a": "release"}


def test_small_and_unknown_types_fall_back_to_global(monkeypatch):
    cases = [{"case_id": str(i), "reference": {"score": i}} for i in range(32)]
    types = {str(i): "release" if i < 15 else "unknown" if i < 31 else "industry" for i in range(32)}
    calls = []
    def fake_solve(dims, y):
        calls.append(y)
        return {"weights_percent": dict.fromkeys(("impact", "novelty", "substance", "authority", "relevance"), 20)}
    monkeypatch.setattr(study, "solve", fake_solve)
    result = study.fit_branches(cases, [{}]*32, types)
    assert set(result["branches"]) == {"release"}
    assert calls == [list(range(32)), list(range(15))]
    dims = [dict(reason="ok", impact=5, novelty=5, substance=5, authority=5, relevance=5)]*32
    assert study.predictions(cases, dims, types, result, True) == study.predictions(cases, dims, types, result, False)


def test_fit_uses_only_training_labels_for_internal_mapping(tmp_path, monkeypatch):
    cases = [{"case_id": str(i), "split": "dev", "reference": {"score": i}} for i in range(20)]
    monkeypatch.setattr(study, "source_rows", lambda *a, **kw: (None, {"split": "dev", "object_identity": {}}, cases, [{}]*20, {}))
    monkeypatch.setattr(study, "load_types", lambda *a: {str(i): "release" for i in range(20)})
    monkeypatch.setattr(study, "train_case", lambda c: int(c["case_id"]) < 15)
    fitted = []
    def fit(cs, ds, types):
        fitted.append([c["reference"]["score"] for c in cs])
        return {}
    monkeypatch.setattr(study, "fit_branches", fit)
    monkeypatch.setattr(study, "measure", lambda *a: {k: {"metrics": {"mae": {"value": 10}, "spearman": {"value": .5}}} for k in ("global", "conditional")})
    assets.write_jsonl(tmp_path/"classifications.jsonl", [])
    result = study.fit(tmp_path, tmp_path, tmp_path/"fit.json", tmp_path)
    assert fitted == [list(range(15))]
    assert result["full_dev_mapping"] is None
    assert not result["advance_to_seen_regression"]
