from __future__ import annotations

import os
from pathlib import Path

from airadar.cli import _load_runtime_env


def test_load_runtime_env_uses_shared_claude_env_when_project_env_missing(monkeypatch, tmp_path: Path) -> None:
    project_env = tmp_path / "project.env"
    shared_env = tmp_path / ".claude" / ".env"
    shared_env.parent.mkdir()
    shared_env.write_text("AI_RADAR_LLM_GATEWAY_PROJECT=shared-project\n", encoding="utf-8")
    monkeypatch.delenv("AI_RADAR_LLM_GATEWAY_PROJECT", raising=False)

    _load_runtime_env(project_env=project_env, shared_env=shared_env)

    assert os.environ["AI_RADAR_LLM_GATEWAY_PROJECT"] == "shared-project"


def test_load_runtime_env_prefers_project_env_over_shared_env(monkeypatch, tmp_path: Path) -> None:
    project_env = tmp_path / ".env"
    shared_env = tmp_path / ".claude" / ".env"
    shared_env.parent.mkdir()
    shared_env.write_text("AI_RADAR_LLM_GATEWAY_PROJECT=shared-project\nAI_RADAR_KB_ROOT=/shared/kb\n", encoding="utf-8")
    project_env.write_text("AI_RADAR_LLM_GATEWAY_PROJECT=project-id\n", encoding="utf-8")
    monkeypatch.delenv("AI_RADAR_LLM_GATEWAY_PROJECT", raising=False)
    monkeypatch.delenv("AI_RADAR_KB_ROOT", raising=False)

    _load_runtime_env(project_env=project_env, shared_env=shared_env)

    import os

    assert os.environ["AI_RADAR_LLM_GATEWAY_PROJECT"] == "project-id"
    assert os.environ["AI_RADAR_KB_ROOT"] == "/shared/kb"


def test_load_runtime_env_preserves_existing_process_env(monkeypatch, tmp_path: Path) -> None:
    project_env = tmp_path / ".env"
    shared_env = tmp_path / ".claude" / ".env"
    shared_env.parent.mkdir()
    shared_env.write_text("AI_RADAR_LLM_GATEWAY_PROJECT=shared-project\n", encoding="utf-8")
    project_env.write_text("AI_RADAR_LLM_GATEWAY_PROJECT=project-id\n", encoding="utf-8")
    monkeypatch.setenv("AI_RADAR_LLM_GATEWAY_PROJECT", "process-project")

    _load_runtime_env(project_env=project_env, shared_env=shared_env)

    import os

    assert os.environ["AI_RADAR_LLM_GATEWAY_PROJECT"] == "process-project"


def test_load_runtime_env_does_not_import_migrated_provider_credentials(monkeypatch, tmp_path):
    keys = ("ARK_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY")
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    shared_env = tmp_path / "shared.env"
    shared_env.write_text("".join(f"{key}=synthetic-canary\n" for key in keys))
    _load_runtime_env(project_env=tmp_path / "missing.env", shared_env=shared_env)
    assert not set(keys).intersection(os.environ)


def test_read_value_resolves_single_key_without_mutating_environ(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    from airadar.runtime_env import read_value

    shared_env = tmp_path / "claude.env"
    shared_env.write_text("AI_RADAR_PUBLIC_URL=https://shared.invalid\nAI_RADAR_SECRET=leak\n", encoding="utf-8")
    project_env = tmp_path / "missing.env"
    monkeypatch.delenv("AI_RADAR_PUBLIC_URL", raising=False)
    monkeypatch.delenv("AI_RADAR_SECRET", raising=False)

    value = read_value("AI_RADAR_PUBLIC_URL", project_env=project_env, shared_env=shared_env)

    assert value == "https://shared.invalid"
    assert "AI_RADAR_PUBLIC_URL" not in os.environ
    assert "AI_RADAR_SECRET" not in os.environ

    monkeypatch.setenv("AI_RADAR_PUBLIC_URL", "https://process.invalid")
    assert read_value("AI_RADAR_PUBLIC_URL", project_env=project_env, shared_env=shared_env) == "https://process.invalid"
