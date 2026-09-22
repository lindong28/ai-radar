"""Tests for project tracking integrated into cmd_save_from_batch.

The orchestrating agent should not have to call --add-project N times. Instead
cmd_save_from_batch reads meta.projects and registers each entry.
"""

from __future__ import annotations

import json
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
    (batch / "demo_article.md").write_text("# article demo\n", encoding="utf-8")
    (batch / "demo_summary.md").write_text("# summary demo\n", encoding="utf-8")
    return batch


def _meta_with_projects(slug: str, projects: list[dict]) -> str:
    return json.dumps(
        {
            "title": f"Title {slug}",
            "slug": slug,
            "tags": ["test"],
            "keywords": ["kw"],
            "url": f"https://example.com/{slug}",
            "saved_at": "2026-01-01 00:00",
            "projects": projects,
        }
    )


def test_save_from_batch_registers_each_project_in_meta(kb_root, batch_dir):
    projects = [
        {"name": "ProjA", "url": "https://github.com/a/a", "description": "demo"},
        {"name": "ProjB", "url": "https://github.com/b/b", "description": "demo"},
    ]

    with patch.object(embedding, "cmd_add_project") as mock_add_project:
        embedding.cmd_save_from_batch(
            slug="demo",
            user="test_user",
            batch_dir=str(batch_dir),
            meta_json=_meta_with_projects("demo", projects),
            skip_embedding=True,
        )

    assert mock_add_project.call_count == 2
    called_jsons = [call.args[0] for call in mock_add_project.call_args_list]
    parsed = [json.loads(j) for j in called_jsons]
    assert {p["name"] for p in parsed} == {"ProjA", "ProjB"}


def test_save_from_batch_skips_projects_without_reusable_urls(kb_root, batch_dir, capsys):
    projects = [
        {"name": "EmptyURL", "url": "", "description": "demo"},
        {"name": "ProductPage", "url": "https://www.seeles.ai", "description": "demo"},
        {"name": "Reusable", "url": "https://github.com/a/a", "description": "demo"},
    ]

    with patch.object(embedding, "cmd_add_project") as mock_add_project:
        embedding.cmd_save_from_batch(
            slug="demo",
            user="test_user",
            batch_dir=str(batch_dir),
            meta_json=_meta_with_projects("demo", projects),
            skip_embedding=True,
        )

    assert mock_add_project.call_count == 1
    called_project = json.loads(mock_add_project.call_args.args[0])
    assert called_project["name"] == "Reusable"
    stderr = capsys.readouterr().err
    assert "Skipping project 'EmptyURL'" in stderr
    assert "Skipping project 'ProductPage'" in stderr


def test_save_from_batch_skip_projects_does_not_register(kb_root, batch_dir):
    projects = [{"name": "ProjA", "url": "https://github.com/a/a", "description": "demo"}]

    with patch.object(embedding, "cmd_add_project") as mock_add_project:
        embedding.cmd_save_from_batch(
            slug="demo",
            user="test_user",
            batch_dir=str(batch_dir),
            meta_json=_meta_with_projects("demo", projects),
            skip_embedding=True,
            skip_projects=True,
        )

    mock_add_project.assert_not_called()


def test_save_from_batch_no_projects_no_op(kb_root, batch_dir):
    with patch.object(embedding, "cmd_add_project") as mock_add_project:
        embedding.cmd_save_from_batch(
            slug="demo",
            user="test_user",
            batch_dir=str(batch_dir),
            meta_json=_meta_with_projects("demo", []),
            skip_embedding=True,
        )

    mock_add_project.assert_not_called()
