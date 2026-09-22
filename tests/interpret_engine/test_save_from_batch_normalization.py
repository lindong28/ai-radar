from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from airadar.interpret.engine import embedding, tag_normalizer


@pytest.fixture()
def kb_root(tmp_path, monkeypatch):
    monkeypatch.setattr(embedding, "KB_ROOT", tmp_path / "data/summary_agent")
    root = tmp_path / "data/summary_agent"
    (root / "articles").mkdir(parents=True)
    user_dir = root / "test_user"
    (user_dir / "article_summaries").mkdir(parents=True)
    (user_dir / "index.json").write_text("[]", encoding="utf-8")
    return root


@pytest.fixture()
def batch_dir(tmp_path):
    batch = tmp_path / "batch"
    batch.mkdir()
    (batch / "demo_article.md").write_text("# article demo\n", encoding="utf-8")
    (batch / "demo_summary.md").write_text("### 📋 文章概况\nA summary excerpt.\n", encoding="utf-8")
    return batch


def _meta_json() -> str:
    return json.dumps(
        {
            "title": "Title demo",
            "slug": "demo",
            "tags": ["Agent 架构"],
            "keywords": ["kw"],
            "url": "https://example.com/demo",
            "saved_at": "2026-01-01 00:00",
            "projects": [
                {
                    "name": "ProjA",
                    "url": "https://github.com/a/a",
                    "description": "demo",
                    "tags": ["AI乐器演奏"],
                }
            ],
        },
        ensure_ascii=False,
    )


def test_save_from_batch_persists_normalized_meta(kb_root, batch_dir, capsys):
    def normalize(meta, **_kwargs):
        meta["tags"] = ["Agent架构"]
        meta["projects"][0]["tags"] = ["AI音乐"]
        return tag_normalizer.NormalizationReport(
            decisions=[
                tag_normalizer.TagDecision("Agent 架构", "Agent架构", "merged", 0.99, None),
                tag_normalizer.TagDecision("AI乐器演奏", "AI音乐", "merged", 0.91, None),
            ],
            normalization_failed=False,
            failure_reason=None,
            tags_md_written=False,
        )

    with (
        patch.object(embedding.tag_normalizer, "normalize_meta_tags", side_effect=normalize) as mock_normalize,
        patch.object(embedding, "cmd_add_project") as mock_add_project,
    ):
        embedding.cmd_save_from_batch(
            slug="demo",
            user="test_user",
            batch_dir=str(batch_dir),
            meta_json=_meta_json(),
            skip_embedding=True,
        )

    mock_normalize.assert_called_once()
    index = json.loads((kb_root / "test_user" / "index.json").read_text(encoding="utf-8"))
    assert index[0]["metadata"]["tags"] == ["Agent架构"]
    added_project = json.loads(mock_add_project.call_args.args[0])
    assert added_project["tags"] == ["AI音乐"]

    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["tag_normalization"]["decisions"][0]["action"] == "merged"
    assert payload["tag_normalization"]["normalization_failed"] is False


def test_save_from_batch_normalization_failure_still_saves(kb_root, batch_dir, capsys):
    def normalize(meta, **_kwargs):
        return tag_normalizer.NormalizationReport(
            decisions=[],
            normalization_failed=True,
            failure_reason="embedding unavailable",
            tags_md_written=False,
        )

    with patch.object(embedding.tag_normalizer, "normalize_meta_tags", side_effect=normalize):
        embedding.cmd_save_from_batch(
            slug="demo",
            user="test_user",
            batch_dir=str(batch_dir),
            meta_json=_meta_json(),
            skip_embedding=True,
            skip_projects=True,
        )

    index = json.loads((kb_root / "test_user" / "index.json").read_text(encoding="utf-8"))
    assert index[0]["metadata"]["tags"] == ["Agent 架构"]
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["saved"] is True
    assert payload["tag_normalization"]["normalization_failed"] is True
    assert payload["tag_normalization"]["failure_reason"] == "embedding unavailable"
