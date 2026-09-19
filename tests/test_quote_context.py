"""As-of raw context and real runner wiring; no external model calls."""
import copy
import json
import shutil
from pathlib import Path

import pytest

from evals._shared import assets, prefilter_eval
from evals._shared.quote_context import QuoteContext, render_quotes
from tests.test_prefilter_eval import config, fixture_dataset


def variant(post="123", body="A new model", at="2026-09-16T10:00:00Z"):
    return {"observed_at": at, "raw_run": "raw-run", "raw": {
        "url": f"https://x.com/a/status/{post}", "title": "Model", "content_text": body,
        "author": "@a", "extra": {"x_post_id": post}, "reference": "DO_NOT_LEAK"}}


def index(tmp_path, variants):
    path = tmp_path / "raw-inputs.jsonl"
    assets.write_jsonl(path, [{"variants": {str(i): v for i, v in enumerate(variants)}}])
    return QuoteContext(path)


def raw():
    return {"source_kind": "x", "extra": {"referenced_tweets": [{"type": "quoted", "id": "123"}]}}


@pytest.mark.parametrize("mode,expected", [
    ("prior", "available"), ("future", "missing_as_of"), ("conflict", "ambiguous"),
    ("metadata", "available"), ("empty", "empty_body"), ("mismatched_id", "missing_as_of")])
def test_as_of_identity_conflict_and_missing(tmp_path, mode, expected):
    original = variant()
    variants = [original]
    if mode == "future": original["observed_at"] = "2026-09-17T00:00:00Z"
    if mode == "conflict": variants.append(variant(body="Materially changed"))
    if mode == "metadata":
        changed = copy.deepcopy(original)
        changed["raw"].update(published_at="other", content_html="new", title="Model!")
        variants.append(changed)
    if mode == "empty": original["raw"]["content_text"] = ""
    if mode == "mismatched_id": original["raw"]["extra"]["x_post_id"] = "456"
    rows = index(tmp_path, variants).resolve(raw(), "2026-09-16T12:00:00Z")
    assert rows[0]["status"] == expected
    rendered = render_quotes(rows)
    assert bool(rendered) == (expected == "available")
    assert "DO_NOT_LEAK" not in rendered


def test_non_quote_future_conflict_and_truncation(tmp_path):
    old = variant(body="x" * 5000)
    future = variant(body="changed", at="2026-09-17T00:00:00Z")
    quotes = index(tmp_path, [old, future])
    rows = quotes.resolve(raw(), "2026-09-16T12:00:00Z")
    assert rows[0]["status"] == "available"
    assert "x" * 4000 in render_quotes(rows) and "x" * 4001 not in render_quotes(rows)
    reply = raw(); reply["extra"]["referenced_tweets"][0]["type"] = "replied_to"
    assert quotes.resolve(reply, "2026-09-16T12:00:00Z") == []
    assert quotes.resolve({**raw(), "source_kind": "web"}, "2026-09-16T12:00:00Z") == []


def test_runner_keeps_cases_and_archives_actual_context(tmp_path):
    leaf = fixture_dataset(tmp_path)
    cases = assets.read_jsonl(leaf / "cases.jsonl")
    for case in cases:
        case["input"].update(raw())
        case["provenance"] = {"observed_at": "2026-09-16T12:00:00Z"}
    (leaf / "cases.jsonl").write_text("".join(json.dumps(c) + "\n" for c in cases))
    assets.write_jsonl(leaf / "raw-inputs.jsonl", [{"variants": {"v": variant()}}])
    manifest = assets.read_json(leaf / "manifest.json")
    manifest["files"]["cases.jsonl"] = assets.file_digest(leaf / "cases.jsonl")
    manifest["evidence_files"] = {"raw-inputs.jsonl": assets.file_digest(leaf / "raw-inputs.jsonl")}
    (leaf / "manifest.json").write_text(json.dumps(manifest))
    captured = []
    def factory(attempts):
        def for_case(key):
            def chat(**kwargs):
                captured.append(kwargs["prompt"])
                return {"json": {"is_ai_related": True, "confidence": 1}}
            return chat
        return for_case
    prompt = {"system": "candidate", "user_template": "{{ item.title }}"}
    kwargs = dict(config=config(), split="dev", limit=2, seed="s", chat_factory=factory,
                  label="quotes", root=tmp_path, prompt=prompt)
    result = prefilter_eval.evaluate(leaf, **kwargs, quote_context=True)
    run = Path(result["run"])
    assert all("A new model" in p["user"] and "DO_NOT_LEAK" not in str(p) for p in captured)
    assert assets.read_jsonl(run / "cases.jsonl") == prefilter_eval.select_cases(cases, "dev", 2, "s")
    sidecar = assets.read_jsonl(run / "quote-context.jsonl")
    assert assets.read_json(run / "started.json")["object_identity"]["quote_context"]["resolved_sha256"] == assets.digest(sidecar)
    with pytest.raises(ValueError, match="identity mismatch"):
        prefilter_eval.evaluate(leaf, **kwargs, reuse=run)
    captured.clear()
    shutil.copytree(tmp_path / "evals", tmp_path / "retry" / "evals")
    kwargs["root"] = tmp_path / "retry"
    prefilter_eval.evaluate(leaf, **kwargs, quote_context=True, reuse=run)
    assert not captured
