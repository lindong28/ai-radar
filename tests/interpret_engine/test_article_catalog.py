from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

from airadar.interpret.engine import embedding


def _write_catalog_fixture(
    root: Path,
    *,
    vectors: np.ndarray,
    manifest_slugs: list[str],
    missing_summary: bool = False,
) -> None:
    user_dir = root / "dong_lin"
    summaries_dir = user_dir / "article_summaries"
    articles_dir = root / "articles"
    embeddings_dir = user_dir / "embeddings"
    summaries_dir.mkdir(parents=True)
    articles_dir.mkdir(parents=True)
    embeddings_dir.mkdir(parents=True)

    rows = []
    for index, slug in enumerate(("first", "second"), start=1):
        article_path = articles_dir / f"{slug}.md"
        summary_path = summaries_dir / f"{slug}_output.md"
        article_path.write_text(f"# Article {index}\n", encoding="utf-8")
        if not (missing_summary and slug == "second"):
            summary_path.write_text(f"# Summary {index}\n", encoding="utf-8")
        rows.append(
            {
                "title": f"Article {index}",
                "input": {"article_file_path": str(article_path)},
                "output": {"summary_file_path": str(summary_path)},
                "metadata": {
                    "url": f"https://mp.weixin.qq.com/s/article-{index}?scene=1",
                    "source": "测试公众号",
                    "saved_at": f"2026-08-0{index} 10:00",
                    "tags": ["Seedance"],
                    "keywords": [f"keyword-{index}"],
                },
            }
        )

    (user_dir / "index.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    np.save(embeddings_dir / "vectors.npy", vectors)
    (embeddings_dir / "vectors_manifest.json").write_text(
        json.dumps({"slugs": manifest_slugs}),
        encoding="utf-8",
    )


def _records(stdout: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in stdout.splitlines() if line.strip()]


def test_list_article_records_emits_versioned_snapshot_and_stable_article_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(embedding, "KB_ROOT", tmp_path)
    _write_catalog_fixture(
        tmp_path,
        vectors=np.ones((2, embedding.EMBEDDING_DIM), dtype=np.float32),
        manifest_slugs=["first", "second"],
    )

    embedding.cmd_list_article_records("dong_lin")

    header, first, second = _records(capsys.readouterr().out)
    assert header == {
        "record_type": "catalog",
        "schema_version": 1,
        "user": "dong_lin",
        "index_rows": 2,
        "manifest_rows": 2,
        "vector_rows": 2,
        "vector_ndim": 2,
        "vector_dim": embedding.EMBEDDING_DIM,
        "expected_vector_dim": embedding.EMBEDDING_DIM,
        "alignment_status": "exact",
    }
    assert [first["kb_slug"], second["kb_slug"]] == ["first", "second"]
    assert first["canonical_url"] == "https://mp.weixin.qq.com/s/article-1"
    assert first["entry_status"] == "ok"
    assert first["file_status"] == "ok"
    assert first["vector_status"] == "ok"
    assert first["tags"] == ["Seedance"]
    assert first["keywords"] == ["keyword-1"]


def test_list_article_records_reports_missing_file_and_bad_vector_without_aborting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(embedding, "KB_ROOT", tmp_path)
    vectors = np.ones((2, embedding.EMBEDDING_DIM), dtype=np.float32)
    vectors[1] = 0
    _write_catalog_fixture(
        tmp_path,
        vectors=vectors,
        manifest_slugs=["first", "second"],
        missing_summary=True,
    )

    embedding.cmd_list_article_records("dong_lin")

    _header, first, second = _records(capsys.readouterr().out)
    assert first["file_status"] == "ok"
    assert first["vector_status"] == "ok"
    assert second["file_status"] == "summary_missing"
    assert second["vector_status"] == "zero_or_nonfinite"


def test_list_article_records_resolves_repo_relative_paths_against_an_alternate_kb_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    alternate_root = tmp_path / "copied-summary-agent"
    monkeypatch.setattr(embedding, "KB_ROOT", alternate_root)
    _write_catalog_fixture(
        alternate_root,
        vectors=np.ones((2, embedding.EMBEDDING_DIM), dtype=np.float32),
        manifest_slugs=["first", "second"],
    )
    index_path = alternate_root / "dong_lin/index.json"
    rows = json.loads(index_path.read_text(encoding="utf-8"))
    for slug, row in zip(("first", "second"), rows, strict=True):
        row["input"]["article_file_path"] = f"data/summary_agent/articles/{slug}.md"
        row["output"]["summary_file_path"] = f"data/summary_agent/dong_lin/article_summaries/{slug}_output.md"
    index_path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")

    embedding.cmd_list_article_records("dong_lin")

    _header, first, second = _records(capsys.readouterr().out)
    assert first["article_file_path"] == str((alternate_root / "articles/first.md").resolve())
    assert second["summary_file_path"] == str(
        (alternate_root / "dong_lin/article_summaries/second_output.md").resolve()
    )
    assert first["file_status"] == "ok"
    assert second["file_status"] == "ok"


def test_list_article_records_marks_every_vector_unusable_when_alignment_mismatches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(embedding, "KB_ROOT", tmp_path)
    _write_catalog_fixture(
        tmp_path,
        vectors=np.ones((2, embedding.EMBEDDING_DIM), dtype=np.float32),
        manifest_slugs=["second", "first"],
    )

    embedding.cmd_list_article_records("dong_lin")

    header, first, second = _records(capsys.readouterr().out)
    assert header["alignment_status"] == "mismatch"
    assert first["vector_status"] == "alignment_mismatch"
    assert second["vector_status"] == "alignment_mismatch"


def test_main_routes_list_article_records_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(embedding, "KB_ROOT", tmp_path)
    monkeypatch.setattr(embedding, "_load_project_env", lambda: None)
    _write_catalog_fixture(
        tmp_path,
        vectors=np.ones((2, embedding.EMBEDDING_DIM), dtype=np.float32),
        manifest_slugs=["first", "second"],
    )
    monkeypatch.setattr(sys, "argv", ["airadar.interpret.engine.embedding.py", "--list-article-records", "--user", "dong_lin"])

    embedding.main()

    assert _records(capsys.readouterr().out)[0]["record_type"] == "catalog"


def test_list_article_records_emits_invalid_index_entries_instead_of_aborting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(embedding, "KB_ROOT", tmp_path)
    user_dir = tmp_path / "dong_lin"
    embeddings_dir = user_dir / "embeddings"
    embeddings_dir.mkdir(parents=True)
    (user_dir / "index.json").write_text('["malformed"]', encoding="utf-8")
    np.save(embeddings_dir / "vectors.npy", np.ones((1, embedding.EMBEDDING_DIM), dtype=np.float32))
    (embeddings_dir / "vectors_manifest.json").write_text('{"slugs":[""]}', encoding="utf-8")

    embedding.cmd_list_article_records("dong_lin")

    _header, article = _records(capsys.readouterr().out)
    assert article["entry_status"] == "invalid"
    assert article["file_status"] == "both_missing"


def test_list_article_records_marks_bad_metadata_types_invalid_and_preserves_schema_shape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(embedding, "KB_ROOT", tmp_path)
    _write_catalog_fixture(
        tmp_path,
        vectors=np.ones((2, embedding.EMBEDDING_DIM), dtype=np.float32),
        manifest_slugs=["first", "second"],
    )
    index_path = tmp_path / "dong_lin/index.json"
    rows = json.loads(index_path.read_text(encoding="utf-8"))
    rows[0]["metadata"]["tags"] = ["Seedance", 1, {"bad": True}]
    rows[0]["metadata"]["keywords"] = "not-a-list"
    index_path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")

    embedding.cmd_list_article_records("dong_lin")

    _header, first, second = _records(capsys.readouterr().out)
    assert first["entry_status"] == "invalid"
    assert first["tags"] == ["Seedance"]
    assert first["keywords"] == []
    assert second["entry_status"] == "ok"
