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


def load_runtime_env(
    *,
    project_env: Path | None = None,
    shared_env: Path | None = None,
) -> None:
    project_env = project_env or db.PROJECT_ROOT / ".env"
    shared_env = shared_env or Path.home() / ".claude" / ".env"

    values: dict[str, str] = {}
    for env_path in (shared_env, project_env):
        if not env_path.exists():
            continue
        for key, value in dotenv_values(env_path).items():
            if value is not None:
                values[key] = value

    for key, value in values.items():
        os.environ.setdefault(key, value)
