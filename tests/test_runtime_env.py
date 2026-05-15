from __future__ import annotations

from pathlib import Path

from airadar.cli import _load_runtime_env


def test_load_runtime_env_uses_shared_claude_env_when_project_env_missing(monkeypatch, tmp_path: Path) -> None:
    project_env = tmp_path / "project.env"
    shared_env = tmp_path / ".claude" / ".env"
    shared_env.parent.mkdir()
    shared_env.write_text("DEEPSEEK_API_KEY=shared-key\n", encoding="utf-8")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    _load_runtime_env(project_env=project_env, shared_env=shared_env)

    assert __import__("os").environ["DEEPSEEK_API_KEY"] == "shared-key"


def test_load_runtime_env_prefers_project_env_over_shared_env(monkeypatch, tmp_path: Path) -> None:
    project_env = tmp_path / ".env"
    shared_env = tmp_path / ".claude" / ".env"
    shared_env.parent.mkdir()
    shared_env.write_text("DEEPSEEK_API_KEY=shared-key\nOPENAI_API_KEY=shared-openai\n", encoding="utf-8")
    project_env.write_text("DEEPSEEK_API_KEY=project-key\n", encoding="utf-8")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    _load_runtime_env(project_env=project_env, shared_env=shared_env)

    import os

    assert os.environ["DEEPSEEK_API_KEY"] == "project-key"
    assert os.environ["OPENAI_API_KEY"] == "shared-openai"


def test_load_runtime_env_preserves_existing_process_env(monkeypatch, tmp_path: Path) -> None:
    project_env = tmp_path / ".env"
    shared_env = tmp_path / ".claude" / ".env"
    shared_env.parent.mkdir()
    shared_env.write_text("DEEPSEEK_API_KEY=shared-key\n", encoding="utf-8")
    project_env.write_text("DEEPSEEK_API_KEY=project-key\n", encoding="utf-8")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "process-key")

    _load_runtime_env(project_env=project_env, shared_env=shared_env)

    import os

    assert os.environ["DEEPSEEK_API_KEY"] == "process-key"
