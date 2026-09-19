"""Shared runtime env loader (ADR-003 dual dotenv): process env > project .env > ~/.claude/.env.

Used by the CLI entrypoint and by long-lived adapter entrypoints (e.g. the
continuous-performance adapter) that do not go through ``cli.main()`` and whose
launchd environment does not carry the owner's dotenv values.

Only keys this project declares are loaded into ``os.environ`` (2026-09-19
revision of ADR-003). ``~/.claude/.env`` is shared across every project on the
machine and holds credentials this one never uses; loading all of them made a
single RCE or subprocess env leak worth the whole keyring instead of the three
or four keys AI Radar actually needs.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values

from . import db

# Every key with the project prefix is ours by construction.
RUNTIME_ENV_PREFIXES = ("AI_RADAR_",)
# Non-prefixed keys the project reads. Keep in sync with .env.example (a test
# asserts every key declared there is listed here).
RUNTIME_ENV_KEYS = frozenset(
    {
        "AI_ASSISTANT_ROOT",
        "ARK_API_KEY",
        "ARK_BASE_URL",
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_BASE_URL",
        "DEBUG_QUOTA",
        "EDGEONE_SECRET_ID",
        "EDGEONE_SECRET_KEY",
        "EDGEONE_ZONE_ID",
        "FEISHU_GENERAL_ALERT_WEBHOOK",
        "FEISHU_GENERAL_NOTIFICATION_WEBHOOK",
        "GLM_API_KEY",
        "MP2RSS_FEED_URL",
        "OPENAI_API_KEY",
        "TIKHUB_API_KEY",
        "WECHAT2RSS_FEED_URL",
        "X_BEARER_TOKEN",
    }
)


def is_runtime_env_key(key: str) -> bool:
    return key.startswith(RUNTIME_ENV_PREFIXES) or key in RUNTIME_ENV_KEYS


def _dotenv_layers(
    *,
    project_env: Path | None = None,
    shared_env: Path | None = None,
    restrict: bool = True,
) -> dict[str, str]:
    project_env = project_env or db.PROJECT_ROOT / ".env"
    shared_env = shared_env or Path.home() / ".claude" / ".env"

    values: dict[str, str] = {}
    for env_path in (shared_env, project_env):
        if not env_path.exists():
            continue
        for key, value in dotenv_values(env_path).items():
            if value is None:
                continue
            if restrict and not is_runtime_env_key(key):
                continue
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
    pulling a single value keeps the parent's secret surface unchanged. The
    caller names the key explicitly, so the allowlist does not apply here.
    """
    from_env = os.environ.get(key)
    if from_env is not None:
        return from_env
    return _dotenv_layers(project_env=project_env, shared_env=shared_env, restrict=False).get(key, "")
