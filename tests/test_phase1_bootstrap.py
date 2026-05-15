from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path
from typing import Protocol

from airadar.db import migrate
from airadar.provider.codex_gpt_mini import CodexGptMiniScorer
from airadar.provider.deepseek_v4_pro import DeepSeekV4ProScorer
from airadar.provider.deepseek_v32 import DeepSeekV32Prefilter
from airadar.provider.glm import GLMPrefilter

ROOT = Path(__file__).resolve().parents[1]


class SmokeProvider(Protocol):
    model_id: str

    def smoke_test(self) -> str: ...


def test_migrate_creates_expected_tables_and_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.db"

    migrate(db_path)
    migrate(db_path)

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()

    table_names = {row[0] for row in rows}
    assert {
        "curated_items",
        "curation_runs",
        "feedback",
        "item_evaluations",
        "items_fts",
        "items",
        "sources",
    }.issubset(table_names)


def test_provider_smoke_contract_returns_status_strings() -> None:
    providers: list[SmokeProvider] = [
        GLMPrefilter(),
        CodexGptMiniScorer(),
        DeepSeekV32Prefilter(),
        DeepSeekV4ProScorer(),
    ]

    for provider in providers:
        assert provider.model_id
        assert isinstance(provider.smoke_test(), str)


def test_cli_help_exposes_planned_subcommands() -> None:
    result = subprocess.run(
        ["./run.sh", "--help"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    for command in ["fetch", "prefilter", "score", "curate", "serve", "admin"]:
        assert command in result.stdout


def test_cli_unimplemented_rerun_eval_exits_zero() -> None:
    result = subprocess.run(
        ["./run.sh", "admin", "rerun-eval"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )

    assert "not implemented" in result.stdout.lower()
