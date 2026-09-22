"""Code-owned templates and operator-owned KB data are separate roots."""

from __future__ import annotations

import os
from pathlib import Path

from airadar import db

ASSETS = Path(__file__).parent / "assets"


def kb_root(legacy_root: Path | None = None) -> Path:
    configured = os.environ.get("AI_RADAR_KB_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    legacy = legacy_root or os.environ.get("AI_ASSISTANT_ROOT")
    if legacy:
        return (Path(legacy).expanduser() / "data/summary_agent").resolve()
    return db.PROJECT_ROOT / "data/summary_agent"


def tags_path(legacy_root: Path | None = None) -> Path:
    configured = os.environ.get("AI_RADAR_INTERPRET_TAGS_PATH")
    if configured:
        return Path(configured).expanduser().resolve()
    legacy = legacy_root or os.environ.get("AI_ASSISTANT_ROOT")
    if legacy:
        old = Path(legacy).expanduser() / "agents/summary-agent/docs/tags.md"
        if old.is_file():
            return old.resolve()
    # Never silently replace an existing operator vocabulary with the seed.
    return kb_root(legacy_root) / "tags.md"


def stored_path(value: str, root: Path | None = None) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path.resolve()
    try:
        path = path.relative_to("data/summary_agent")
    except ValueError:
        pass
    return (root or kb_root()).joinpath(path).resolve()
