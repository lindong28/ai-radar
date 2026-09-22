"""Tests for cmd_save_from_batch in embedding.py.

Verifies the orchestration that copies scratch article + summary into the
knowledge base, appends the index entry, and triggers the embedding update.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from airadar.interpret.engine import embedding


@pytest.fixture(autouse=True)
def disable_tag_normalization(monkeypatch):
    monkeypatch.setattr(
        embedding.tag_normalizer,
        "normalize_meta_tags",
        lambda meta, **_kwargs: embedding.tag_normalizer.NormalizationReport([], False, None, False),
    )


@pytest.fixture()
def kb_root(tmp_path, monkeypatch):
    monkeypatch.setattr(embedding, "KB_ROOT", tmp_path)
    (tmp_path / "articles").mkdir()
    user_dir = tmp_path / "test_user"
    (user_dir / "article_summaries").mkdir(parents=True)
    (user_dir / "index.json").write_text("[]", encoding="utf-8")
    return tmp_path


@pytest.fixture()
def batch_dir(tmp_path):
    batch = tmp_path / "batch"
    batch.mkdir()
    return batch


def _meta_json(slug: str = "demo") -> str:
    return json.dumps(
        {
            "title": f"Title for {slug}",
            "slug": slug,
            "tags": ["test"],
            "keywords": ["kw"],
            "source": "test",
            "url": f"https://example.com/{slug}",
            "saved_at": "2026-01-01 00:00",
            "model_name": "test-model",
        }
    )


def _meta_json_with_url(slug: str, url: str) -> str:
    meta = json.loads(_meta_json(slug))
    meta["url"] = url
    return json.dumps(meta)


def _seed_scratch(batch: Path, slug: str) -> None:
    (batch / f"{slug}_article.md").write_text(f"# article {slug}\n", encoding="utf-8")
    (batch / f"{slug}_summary.md").write_text(f"# summary {slug}\n", encoding="utf-8")


def test_save_from_batch_copies_files_and_appends_index(kb_root, batch_dir):
    _seed_scratch(batch_dir, "demo")

    embedding.cmd_save_from_batch(
        slug="demo",
        user="test_user",
        batch_dir=str(batch_dir),
        meta_json=_meta_json("demo"),
        skip_embedding=True,
    )

    assert (kb_root / "articles" / "demo.md").read_text(encoding="utf-8") == "# article demo\n"
    summary_dest = kb_root / "test_user" / "article_summaries" / "demo_output.md"
    assert summary_dest.read_text(encoding="utf-8") == "# summary demo\n"

    index = json.loads((kb_root / "test_user" / "index.json").read_text(encoding="utf-8"))
    assert len(index) == 1
    assert index[0]["title"] == "Title for demo"
    assert index[0]["input"]["article_file_path"] == "data/summary_agent/articles/demo.md"


def test_save_from_batch_missing_article_fails(kb_root, batch_dir):
    (batch_dir / "demo_summary.md").write_text("# summary\n", encoding="utf-8")

    with pytest.raises(SystemExit):
        embedding.cmd_save_from_batch(
            slug="demo",
            user="test_user",
            batch_dir=str(batch_dir),
            meta_json=_meta_json("demo"),
            skip_embedding=True,
        )


def test_save_from_batch_invalid_meta_json_fails(kb_root, batch_dir):
    _seed_scratch(batch_dir, "demo")

    with pytest.raises(SystemExit):
        embedding.cmd_save_from_batch(
            slug="demo",
            user="test_user",
            batch_dir=str(batch_dir),
            meta_json='{"slug": "demo"}',
            skip_embedding=True,
        )


def test_save_from_batch_calls_add_embedding(kb_root, batch_dir):
    _seed_scratch(batch_dir, "demo")

    with patch.object(embedding, "cmd_add") as mock_add:
        embedding.cmd_save_from_batch(
            slug="demo",
            user="test_user",
            batch_dir=str(batch_dir),
            meta_json=_meta_json("demo"),
            skip_embedding=False,
        )

    mock_add.assert_called_once_with("demo", "test_user")


def test_save_from_batch_stdout_includes_canonical_paths(kb_root, batch_dir, capsys):
    """Final JSON line on stdout must expose the absolute on-disk locations.

    Agents need these to report the real save path back to the user instead of
    fabricating one from stale README / memory.
    """
    _seed_scratch(batch_dir, "demo")

    embedding.cmd_save_from_batch(
        slug="demo",
        user="test_user",
        batch_dir=str(batch_dir),
        meta_json=_meta_json("demo"),
        skip_embedding=True,
        skip_projects=True,
    )

    stdout = capsys.readouterr().out
    final_line = stdout.strip().splitlines()[-1]
    payload = json.loads(final_line)

    assert payload["saved"] is True
    assert payload["slug"] == "demo"
    expected_summary = str(kb_root / "test_user" / "article_summaries" / "demo_output.md")
    expected_article = str(kb_root / "articles" / "demo.md")
    assert payload["summary_file_path"] == expected_summary
    assert payload["article_file_path"] == expected_article


def test_save_from_batch_duplicate_canonical_url_skips_before_copy(kb_root, batch_dir, capsys):
    existing_index = [
        {
            "title": "Existing",
            "input": {"article_file_path": "data/summary_agent/articles/existing.md"},
            "output": {"summary_file_path": "data/summary_agent/test_user/article_summaries/existing_output.md"},
            "metadata": {"url": "https://mp.weixin.qq.com/s/demo", "saved_at": "2026-01-01 00:00"},
        }
    ]
    (kb_root / "test_user" / "index.json").write_text(
        json.dumps(existing_index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _seed_scratch(batch_dir, "duplicate")

    embedding.cmd_save_from_batch(
        slug="duplicate",
        user="test_user",
        batch_dir=str(batch_dir),
        meta_json=_meta_json_with_url("duplicate", "https://mp.weixin.qq.com/s/demo?scene=334"),
        skip_embedding=True,
        skip_projects=True,
    )

    assert not (kb_root / "articles" / "duplicate.md").exists()
    assert not (kb_root / "test_user" / "article_summaries" / "duplicate_output.md").exists()
    index = json.loads((kb_root / "test_user" / "index.json").read_text(encoding="utf-8"))
    assert index == existing_index

    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["saved"] is False
    assert payload["reason"] == "url_exists"
    assert payload["existing"]["slug"] == "existing"
