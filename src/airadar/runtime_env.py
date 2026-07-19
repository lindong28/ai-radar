"""Shared runtime env loader (ADR-003 dual dotenv): process env > project .env > ~/.claude/.env.

Used by the CLI entrypoint and by long-lived adapter entrypoints (e.g. the
continuous-performance adapter) that do not go through ``cli.main()`` and whose
launchd environment does not carry the owner's dotenv values.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values

from . import db


def _dotenv_layers(
    *,
    project_env: Path | None = None,
    shared_env: Path | None = None,
) -> dict[str, str]:
    project_env = project_env or db.PROJECT_ROOT / ".env"
    shared_env = shared_env or Path.home() / ".claude" / ".env"

    values: dict[str, str] = {}
    for env_path in (shared_env, project_env):
        if not env_path.exists():
            continue
        for key, value in dotenv_values(env_path).items():
            if value is not None:
                values[key] = value
    return values


def load_runtime_env(
    *,
    project_env: Path | None = None,
    shared_env: Path | None = None,
) -> None:
    for key, value in _dotenv_layers(project_env=project_env, shared_env=shared_env).items():
        os.environ.setdefault(key, value)


def read_value(
    key: str,
    *,
    project_env: Path | None = None,
    shared_env: Path | None = None,
) -> str:
    """Resolve one key (process env > project .env > ~/.claude/.env) without mutating os.environ.

    For entrypoints whose child processes must not inherit the full dotenv
    contents (e.g. the performance adapter, which spawns browser drivers):
    pulling a single value keeps the parent's secret surface unchanged.
    """
    from_env = os.environ.get(key)
    if from_env is not None:
        return from_env
    return _dotenv_layers(project_env=project_env, shared_env=shared_env).get(key, "")
