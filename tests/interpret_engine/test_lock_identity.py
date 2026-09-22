from __future__ import annotations

import fcntl

import pytest

from airadar.interpret.engine import embedding, tag_normalizer


@pytest.mark.parametrize("legacy", [False, True])
def test_user_lock_uses_kb_identity_not_cwd(tmp_path, monkeypatch, legacy):
    root = tmp_path / "owner"
    kb = root / "data/summary_agent" if legacy else root / "knowledge"
    kb.mkdir(parents=True)
    monkeypatch.setattr(embedding, "KB_ROOT", kb)
    lock_dir = root / "tmp" if legacy else kb / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    expected = lock_dir / "summary_agent_user.lock"
    first, second = tmp_path / "cwd-a", tmp_path / "cwd-b"
    first.mkdir()
    second.mkdir()
    monkeypatch.chdir(first)
    with embedding._user_lock("user"):
        monkeypatch.chdir(second)
        with expected.open("a") as competing:
            with pytest.raises(BlockingIOError):
                fcntl.flock(competing, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with expected.open("a") as released:
        fcntl.flock(released, fcntl.LOCK_EX | fcntl.LOCK_NB)


@pytest.mark.parametrize("legacy", [False, True])
def test_tag_lock_uses_vocabulary_identity_not_caller_root(tmp_path, monkeypatch, legacy):
    owner = tmp_path / "owner"
    vocabulary = owner / "agents/summary-agent/docs/tags.md" if legacy else owner / "tags.md"
    vocabulary.parent.mkdir(parents=True)
    vocabulary.write_text("# tags\n")
    monkeypatch.setenv("AI_RADAR_INTERPRET_TAGS_PATH", str(vocabulary))
    expected = owner / "tmp/summary_agent_tags.lock" if legacy else owner / "tags.md.lock"
    expected.parent.mkdir(parents=True, exist_ok=True)
    with tag_normalizer._global_tags_lock(tmp_path / "radar-checkout"):
        with expected.open("a") as competing:
            with pytest.raises(BlockingIOError):
                fcntl.flock(competing, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with expected.open("a") as released:
        fcntl.flock(released, fcntl.LOCK_EX | fcntl.LOCK_NB)
