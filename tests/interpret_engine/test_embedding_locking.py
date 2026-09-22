"""Tests for file locking in embedding.py.

Verifies that concurrent write operations don't lose data.
"""

from __future__ import annotations

import fcntl
import json
import threading
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from airadar.interpret.engine import embedding

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def kb_root(tmp_path, monkeypatch):
    """Set up a temporary knowledge base root with an empty index."""
    monkeypatch.setattr(embedding, "KB_ROOT", tmp_path)
    user_dir = tmp_path / "test_user"
    user_dir.mkdir()
    (user_dir / "index.json").write_text("[]", encoding="utf-8")
    return tmp_path


def _make_entry_json(slug: str, title: str | None = None, url: str | None = None) -> str:
    """Build a JSON string for cmd_append_entry."""
    return json.dumps(
        {
            "title": title or f"Title for {slug}",
            "slug": slug,
            "tags": ["test"],
            "keywords": ["kw"],
            "source": "test",
            "url": url or f"https://example.com/{slug}",
            "saved_at": "2026-01-01 00:00",
        }
    )


def _make_project_json(name: str, url: str) -> str:
    """Build a JSON string for cmd_add_project."""
    return json.dumps(
        {
            "name": name,
            "url": url,
            "description": f"Description for {name}",
            "tags": ["test"],
            "keywords": ["kw"],
        }
    )


def _load_index(kb_root: Path) -> list[dict]:
    return json.loads((kb_root / "test_user" / "index.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Tests: cmd_append_entry
# ---------------------------------------------------------------------------


class TestConcurrentAppendEntry:
    """Verify no data loss when multiple threads append to index.json."""

    def test_concurrent_append_no_data_loss(self, kb_root):
        """N threads each append a unique slug — all N entries must be present."""
        n_threads = 10
        errors: list[BaseException] = []

        def append_one(i: int):
            try:
                embedding.cmd_append_entry(_make_entry_json(f"slug_{i}"), "test_user")
            except (SystemExit, Exception) as exc:
                errors.append(exc)

        threads = [threading.Thread(target=append_one, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Unexpected errors: {errors}"
        index = _load_index(kb_root)
        slugs = {embedding._slug_from_path(e["output"]["summary_file_path"]) for e in index}
        assert slugs == {f"slug_{i}" for i in range(n_threads)}

    def test_concurrent_append_duplicate_slug(self, kb_root):
        """Two threads try to append the same slug — exactly one succeeds."""
        results: list[str] = []  # "ok" or "dup"

        def append_same():
            try:
                embedding.cmd_append_entry(_make_entry_json("dup_slug"), "test_user")
                results.append("ok")
            except SystemExit:
                results.append("dup")

        threads = [threading.Thread(target=append_same) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert results.count("ok") == 1
        assert results.count("dup") == 1
        index = _load_index(kb_root)
        assert len(index) == 1

    def test_append_duplicate_canonical_url_rejected(self, kb_root):
        """A tracking-param variant of an existing URL must not create a second index entry."""
        embedding.cmd_append_entry(
            _make_entry_json("wechat_original", url="https://mp.weixin.qq.com/s/demo"),
            "test_user",
        )

        with pytest.raises(SystemExit):
            embedding.cmd_append_entry(
                _make_entry_json("wechat_copy", url="https://mp.weixin.qq.com/s/demo?scene=334"),
                "test_user",
            )

        index = _load_index(kb_root)
        assert len(index) == 1
        assert embedding._slug_from_path(index[0]["output"]["summary_file_path"]) == "wechat_original"


# ---------------------------------------------------------------------------
# Tests: cmd_add_project
# ---------------------------------------------------------------------------


class TestConcurrentAddProject:
    """Verify no data loss when multiple threads add projects."""

    def test_add_project_rejects_empty_url(self, kb_root):
        with pytest.raises(SystemExit):
            embedding.cmd_add_project(_make_project_json("empty_url", "  "), "test_user")

        assert not (kb_root / "test_user" / "open_source_projects.json").exists()

    def test_add_project_rejects_non_allowlisted_url(self, kb_root):
        with pytest.raises(SystemExit):
            embedding.cmd_add_project(
                _make_project_json("product_page", "https://www.seeles.ai"),
                "test_user",
            )

        assert not (kb_root / "test_user" / "open_source_projects.json").exists()

    def test_concurrent_add_no_data_loss(self, kb_root):
        """N threads each add a unique project — all N must be present."""
        n_threads = 10
        errors: list[BaseException] = []

        def add_one(i: int):
            try:
                embedding.cmd_add_project(
                    _make_project_json(f"project_{i}", f"https://github.com/test/{i}"),
                    "test_user",
                )
            except (SystemExit, Exception) as exc:
                errors.append(exc)

        threads = [threading.Thread(target=add_one, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Unexpected errors: {errors}"
        projects = json.loads((kb_root / "test_user" / "open_source_projects.json").read_text(encoding="utf-8"))
        urls = {p["url"] for p in projects}
        assert urls == {f"https://github.com/test/{i}" for i in range(n_threads)}

    def test_concurrent_add_duplicate_url(self, kb_root):
        """Two threads try to add the same URL — exactly one gets added."""
        results: list[str] = []

        def add_same():
            embedding.cmd_add_project(
                _make_project_json("dup_project", "https://github.com/dup/project"),
                "test_user",
            )
            results.append("ok")

        threads = [threading.Thread(target=add_same) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Both threads return normally (no SystemExit for duplicates)
        assert len(results) == 2
        projects = json.loads((kb_root / "test_user" / "open_source_projects.json").read_text(encoding="utf-8"))
        dup_entries = [p for p in projects if p["url"] == "https://github.com/dup/project"]
        assert len(dup_entries) == 1


# ---------------------------------------------------------------------------
# Tests: cmd_add two-phase correctness
# ---------------------------------------------------------------------------


class TestCmdAddTwoPhase:
    """Verify cmd_add's two-phase locking handles concurrent index growth."""

    def _setup_index_with_entry(self, kb_root: Path, slug: str):
        """Add one entry to index and create a dummy summary file."""
        summary_path = str(kb_root / "test_user" / "article_summaries" / f"{slug}_output.md")
        entry = {
            "title": f"Title for {slug}",
            "input": {"article_file_path": f"data/summary_agent/articles/{slug}.md"},
            "output": {"summary_file_path": summary_path},
            "metadata": {
                "source": "test",
                "url": f"https://example.com/{slug}",
                "saved_at": "2026-01-01 00:00",
                "tags": ["test"],
                "keywords": ["kw"],
            },
        }
        index_path = kb_root / "test_user" / "index.json"
        index = json.loads(index_path.read_text(encoding="utf-8"))
        index.append(entry)
        index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        # Create summary file so _extract_overview can read it
        summary_dir = kb_root / "test_user" / "article_summaries"
        summary_dir.mkdir(parents=True, exist_ok=True)
        (summary_dir / f"{slug}_output.md").write_text(
            "### 📋 文章概况\nTest overview\n### 💡 独特亮点\n", encoding="utf-8"
        )

    @patch("airadar.interpret.engine.embedding.get_embeddings")
    @patch("airadar.interpret.engine.embedding._get_openai_client")
    def test_index_grows_between_phases(self, mock_client, mock_embed, kb_root):
        """An append between Phase 1 and Phase 2 should not corrupt vectors.

        Phase 2 re-reads index, so the vectors array should include the new entry.
        """
        self._setup_index_with_entry(kb_root, "article_a")

        fake_vec = np.ones(embedding.EMBEDDING_DIM, dtype=np.float32)

        def embed_with_side_effect(client, texts):
            """After Phase 1 reads index, inject a new entry before Phase 2."""
            self._setup_index_with_entry(kb_root, "article_b")
            return np.array([fake_vec])

        mock_embed.side_effect = embed_with_side_effect

        embedding.cmd_add("article_a", "test_user")

        # Verify vectors were saved with correct size (2 entries, not 1)
        emb_dir = kb_root / "test_user" / "embeddings"
        vectors = np.load(emb_dir / "vectors.npy")
        manifest = json.loads((emb_dir / "vectors_manifest.json").read_text(encoding="utf-8"))

        assert vectors.shape[0] == 2, f"Expected 2 rows, got {vectors.shape[0]}"
        assert len(manifest["slugs"]) == 2
        assert manifest["slugs"] == ["article_a", "article_b"]
        # article_a should have the embedding, article_b should be zero (not yet embedded)
        assert np.all(vectors[0] == fake_vec)
        assert np.all(vectors[1] == 0)


# ---------------------------------------------------------------------------
# Tests: cmd_build locking
# ---------------------------------------------------------------------------


class TestCmdBuildLocking:
    """Verify cmd_build acquires the lock."""

    @patch("airadar.interpret.engine.embedding._incremental_rebuild")
    def test_build_acquires_lock(self, mock_rebuild, kb_root):
        """cmd_build should hold the user lock while rebuilding."""
        lock_held = []

        def check_lock(user):
            # Try to acquire the lock non-blocking; if it fails, the lock is held
            lock_path = embedding._user_lock_path(user)
            lock_path.touch(exist_ok=True)
            with open(lock_path) as f:
                try:
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    lock_held.append(False)
                    fcntl.flock(f, fcntl.LOCK_UN)
                except BlockingIOError:
                    lock_held.append(True)

        mock_rebuild.side_effect = check_lock
        embedding.cmd_build("test_user")

        assert lock_held == [True], "Lock should be held during _incremental_rebuild"
