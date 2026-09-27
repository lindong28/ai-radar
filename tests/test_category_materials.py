import json

import pytest

from evals._shared.category_materials import render_materials


def documents(raw, supplement=None, quotes=None):
    return json.loads(render_materials(raw, supplement or {}, quotes or []).split("\n", 1)[1])


def test_exact_duplicates_keep_all_roles_but_different_versions_survive():
    rows = documents({"title": "same", "content_text": "same", "url": "x"},
        {"status": "available", "content_text": "same", "repair_materials": [
            {"kind": "current-post", "content_text": "same", "url": "x"}]},
        [{"status": "available", "input": {"title": "same", "content_text": "new", "url": "x"}}])
    assert [r["text"] for r in rows] == ["same", "new"]
    assert {r["role"] for r in rows[0]["origins"]} == {"current-item", "current-post", "quoted-post"}
    assert {r["part"] for r in rows[0]["origins"]} == {"title", "body"}


def test_supplement_residual_and_unknown_roles_are_not_dropped_or_augmented():
    rows = documents({"title": "headline", "reference": "SECRET"},
        {"status": "available", "content_text": "prefix BODY suffix", "repair_materials": [
            {"kind": "unrecognized", "content_text": "BODY"},
            {"kind": "linked-article", "content_text": "NOT PREVIOUSLY EXPOSED"}]})
    assert "".join(r["text"] for r in rows[1:]) == "prefix BODY suffix"
    assert rows[2]["origins"][0]["role"] == "unrecognized"
    assert "SECRET" not in str(rows) and "NOT PREVIOUSLY EXPOSED" not in str(rows)


def test_nested_fragments_preserve_long_text_and_missing_quotes_do_not_invent_content():
    rows = documents({}, {"status": "available", "content_text": "long BODY end", "repair_materials": [
        {"kind": "linked-article", "content_text": "BODY"},
        {"kind": "current-post", "content_text": "long BODY end"}]}, [{"status": "missing_as_of"}])
    assert len(rows) == 1 and rows[0]["text"] == "long BODY end"


def test_nontext_is_rejected():
    with pytest.raises(ValueError, match="string"):
        documents({"content_text": {"bad": "input"}})


def test_truncated_documents_layout_is_rejected_before_loading():
    from pathlib import Path
    from evals._shared.category_eval import evaluate
    with pytest.raises(ValueError, match="full frozen body"):
        evaluate(Path("missing"), config={}, split="dev", limit=None, seed="x",
                 label="x", chat_factory=None, material_layout="documents")
