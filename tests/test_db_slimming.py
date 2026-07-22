from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from airadar import cli
from airadar.curator.precompute import retain_curated_summaries
from airadar.db import migrate
from airadar.web.routes import curated_digest


def _seed_retention_db(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "retention.db"
    migrate(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO sources (
          id, name, url, tier, enabled, kind, homepage_url, icon_url, meta_json, synced_at
        ) VALUES (
          'source-1', 'Source', 'https://example.com/feed.xml', 'T1', 1, 'feed',
          'https://example.com/', NULL, '{}', '2026-07-01T00:00:00Z'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO items (
          id, source_id, url, title, author, published_at, fetched_at,
          content_text, content_html, content_hash, extra_json
        ) VALUES (
          'item-1', 'source-1', 'https://example.com/item-1', 'Item 1', 'Ada',
          '2026-07-01T00:00:00Z', '2026-07-01T00:00:00Z', 'content', NULL,
          'hash-1', '{}'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO item_evaluations (
          item_id, stage, ruleset_version, model_id, input_json, output_json,
          numeric_json, latency_ms, cost_usd, evaluated_at, error
        ) VALUES (
          'item-1', 'scoring', 'test.r1', 'fake', '{"input":1}', '{"output":1}',
          '{"score":9}', 12, 0.01, '2026-07-01T00:00:00Z', NULL
        )
        """
    )
    conn.commit()
    return conn


def _insert_run(
    conn: sqlite3.Connection,
    run_id: str,
    created_at: str,
    *,
    summary: str | None = '{"item_summary":"摘要"}',
) -> None:
    conn.execute(
        """
        INSERT INTO curation_runs (
          id, ruleset_version, weights_json, threshold, input_eval_ids,
          output_curated_ids, created_at
        ) VALUES (?, 'test.r1', '{"relevance":1}', 6.5, '[1]', '["item-1"]', ?)
        """,
        (run_id, created_at),
    )
    conn.execute(
        """
        INSERT INTO curated_items (
          run_id, item_id, weighted_score, rank, reason_json, summary_json
        ) VALUES (?, 'item-1', 8.5, 1, '{"scores":{"relevance":9}}', ?)
        """,
        (run_id, summary),
    )
    conn.commit()


def _as_tz(value: str) -> str:
    return value.replace(" ", "T") + "Z"


def _table_digest(conn: sqlite3.Connection, table: str) -> str:
    columns = [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]
    rows = conn.execute(
        f'SELECT {", ".join(f"\"{column}\"" for column in columns)} '
        f'FROM "{table}" ORDER BY rowid'
    ).fetchall()
    payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _summary_by_run(conn: sqlite3.Connection) -> dict[str, str | None]:
    return dict(conn.execute("SELECT run_id, summary_json FROM curated_items ORDER BY run_id"))


def _path_fingerprint(path: Path) -> tuple[bool, int | None, int | None, str | None]:
    if not path.exists():
        return False, None, None, None
    stat = path.stat()
    return True, stat.st_mtime_ns, stat.st_size, hashlib.sha256(path.read_bytes()).hexdigest()


def _db_files(db_path: Path) -> tuple[Path, Path, Path]:
    return db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")


def _file_fingerprint(
    db_path: Path,
) -> tuple[tuple[bool, int | None, int | None, str | None], ...]:
    return tuple(_path_fingerprint(path) for path in _db_files(db_path))


def _sidecar_extents(db_path: Path) -> dict[Path, tuple[int, int]]:
    return {
        sidecar: (sidecar.stat().st_ino, sidecar.stat().st_size)
        for sidecar in _db_files(db_path)[1:]
        if sidecar.exists()
    }


def _assert_sidecars_not_deleted_or_truncated(
    before: dict[Path, tuple[int, int]],
) -> None:
    for sidecar, (inode, size) in before.items():
        stat = sidecar.stat()
        assert stat.st_ino == inode
        assert stat.st_size >= size


def test_retain_curated_summaries_normalizes_tz_boundary_and_keep_days_zero(
    tmp_path: Path,
) -> None:
    conn = _seed_retention_db(tmp_path)
    cutoff_minus = conn.execute("SELECT datetime('now', '-7 days', '-1 second')").fetchone()[0]
    cutoff_exact = conn.execute("SELECT datetime('now', '-7 days')").fetchone()[0]
    cutoff_plus = conn.execute("SELECT datetime('now', '-7 days', '+1 second')").fetchone()[0]
    latest = conn.execute("SELECT datetime('now')").fetchone()[0]
    _insert_run(conn, "cutoff-minus", _as_tz(cutoff_minus))
    _insert_run(conn, "cutoff-exact", _as_tz(cutoff_exact))
    _insert_run(conn, "cutoff-plus", _as_tz(cutoff_plus))
    _insert_run(conn, "latest", _as_tz(latest))

    assert retain_curated_summaries(conn, keep_days=7) == 1
    assert _summary_by_run(conn) == {
        "cutoff-exact": '{"item_summary":"摘要"}',
        "cutoff-minus": None,
        "cutoff-plus": '{"item_summary":"摘要"}',
        "latest": '{"item_summary":"摘要"}',
    }

    assert retain_curated_summaries(conn, keep_days=0) == 2
    assert _summary_by_run(conn) == {
        "cutoff-exact": None,
        "cutoff-minus": None,
        "cutoff-plus": None,
        "latest": '{"item_summary":"摘要"}',
    }
    conn.close()


def test_retain_curated_summaries_hard_protects_stale_latest_run(tmp_path: Path) -> None:
    conn = _seed_retention_db(tmp_path)
    old = conn.execute("SELECT datetime('now', '-40 days')").fetchone()[0]
    stale_latest = conn.execute("SELECT datetime('now', '-30 days')").fetchone()[0]
    _insert_run(conn, "older", _as_tz(old))
    _insert_run(conn, "stale-latest", _as_tz(stale_latest))

    assert retain_curated_summaries(conn, keep_days=7) == 1
    assert _summary_by_run(conn) == {
        "older": None,
        "stale-latest": '{"item_summary":"摘要"}',
    }
    conn.close()


def test_retain_curated_summaries_protects_every_run_tied_for_latest_timestamp(
    tmp_path: Path,
) -> None:
    conn = _seed_retention_db(tmp_path)
    older = conn.execute("SELECT datetime('now', '-40 days')").fetchone()[0]
    tied_latest = conn.execute("SELECT datetime('now', '-30 days')").fetchone()[0]
    _insert_run(conn, "older", _as_tz(older))
    _insert_run(conn, "same-second-first", _as_tz(tied_latest))
    _insert_run(conn, "same-second-later", _as_tz(tied_latest))

    assert retain_curated_summaries(conn, keep_days=7) == 1
    assert _summary_by_run(conn) == {
        "older": None,
        "same-second-first": '{"item_summary":"摘要"}',
        "same-second-later": '{"item_summary":"摘要"}',
    }
    conn.close()


def test_retain_curated_summaries_is_idempotent_trigger_free_and_non_trespassing(
    tmp_path: Path,
) -> None:
    conn = _seed_retention_db(tmp_path)
    old = conn.execute("SELECT datetime('now', '-40 days')").fetchone()[0]
    latest = conn.execute("SELECT datetime('now')").fetchone()[0]
    _insert_run(conn, "old", _as_tz(old))
    _insert_run(conn, "latest", _as_tz(latest))
    before_generations = conn.execute(
        "SELECT archive_generation, category_generation FROM archive_cache_generations WHERE id=1"
    ).fetchone()
    before_runs = _table_digest(conn, "curation_runs")
    before_evaluations = _table_digest(conn, "item_evaluations")

    assert retain_curated_summaries(conn, keep_days=7) == 1
    assert retain_curated_summaries(conn, keep_days=7) == 0

    assert conn.execute(
        "SELECT archive_generation, category_generation FROM archive_cache_generations WHERE id=1"
    ).fetchone() == before_generations
    assert _table_digest(conn, "curation_runs") == before_runs
    assert _table_digest(conn, "item_evaluations") == before_evaluations
    conn.close()


def test_curated_digest_fallback_branch_calls_live_compute(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _seed_retention_db(tmp_path)
    _insert_run(conn, "run-without-summary", "2026-07-01T00:00:00Z", summary=None)
    conn.row_factory = sqlite3.Row
    run = conn.execute(
        "SELECT * FROM curation_runs WHERE id='run-without-summary'"
    ).fetchone()
    calls: list[str] = []

    def no_precompute(*_args: object, **_kwargs: object) -> None:
        calls.append("load")
        return None

    def live_compute(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        calls.append("compute")
        return [{"id": "item-1"}]

    monkeypatch.setattr(curated_digest, "_load_precomputed", no_precompute)
    monkeypatch.setattr(curated_digest, "_compute_items", live_compute)

    assert curated_digest.compute_digest_items(
        conn,
        run,
        selected_date=None,
        normalized_category=None,
        q=None,
    ) == [{"id": "item-1"}]
    assert calls == ["load", "compute"]
    conn.close()


def test_db_retain_and_slim_dry_run_report_utf8_bytes_without_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    conn = _seed_retention_db(tmp_path)
    old = conn.execute("SELECT datetime('now', '-40 days')").fetchone()[0]
    latest = conn.execute("SELECT datetime('now')").fetchone()[0]
    multibyte_summary = '{"item_summary":"中文摘要"}'
    _insert_run(conn, "old", _as_tz(old), summary=multibyte_summary)
    _insert_run(conn, "latest", _as_tz(latest))
    db_path = Path(conn.execute("PRAGMA database_list").fetchone()[2])
    character_count = conn.execute(
        "SELECT LENGTH(summary_json) FROM curated_items WHERE run_id='old'"
    ).fetchone()[0]
    byte_count = conn.execute(
        "SELECT LENGTH(CAST(summary_json AS BLOB)) FROM curated_items WHERE run_id='old'"
    ).fetchone()[0]
    conn.close()
    with sqlite3.connect(db_path) as checkpoint:
        checkpoint.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    assert byte_count == len(multibyte_summary.encode())
    assert byte_count > character_count
    main_before = _path_fingerprint(db_path)
    sidecars_before = _sidecar_extents(db_path)
    monkeypatch.setenv("AI_RADAR_DB", str(db_path))

    for command in ("retain", "slim"):
        args = cli.build_parser().parse_args(["admin", "db", command, "--dry-run"])
        assert cli._admin(args) == 0
        captured = capsys.readouterr()
        assert captured.out.strip() == f"eligible_rows=1 logical_summary_bytes={byte_count}"
        assert captured.err == ""
        assert _path_fingerprint(db_path) == main_before
        _assert_sidecars_not_deleted_or_truncated(sidecars_before)


@pytest.mark.parametrize("command", ["retain", "slim"])
def test_db_dry_run_keeps_main_db_unchanged_when_no_sidecars_preexist(
    command: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    conn = _seed_retention_db(tmp_path)
    old = conn.execute("SELECT datetime('now', '-40 days')").fetchone()[0]
    latest = conn.execute("SELECT datetime('now')").fetchone()[0]
    _insert_run(conn, "old", _as_tz(old))
    _insert_run(conn, "latest", _as_tz(latest))
    db_path = Path(conn.execute("PRAGMA database_list").fetchone()[2])
    conn.close()
    checkpoint = sqlite3.connect(db_path)
    try:
        checkpoint.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        checkpoint.close()
    for sidecar in _db_files(db_path)[1:]:
        sidecar.unlink(missing_ok=True)
    main_before = _path_fingerprint(db_path)
    assert _sidecar_extents(db_path) == {}
    monkeypatch.setenv("AI_RADAR_DB", str(db_path))

    args = cli.build_parser().parse_args(["admin", "db", command, "--dry-run"])
    assert cli._admin(args) == 0
    assert capsys.readouterr().err == ""
    assert _path_fingerprint(db_path) == main_before


def test_db_dry_run_explicitly_closes_read_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _seed_retention_db(tmp_path)
    db_path = Path(conn.execute("PRAGMA database_list").fetchone()[2])
    conn.close()
    monkeypatch.setenv("AI_RADAR_DB", str(db_path))
    real_connect = cli._connect_existing_db
    opened: list[sqlite3.Connection] = []

    def capture_connection(path: Path, *, readonly: bool) -> sqlite3.Connection:
        opened.append(real_connect(path, readonly=readonly))
        return opened[-1]

    monkeypatch.setattr(cli, "_connect_existing_db", capture_connection)
    args = cli.build_parser().parse_args(["admin", "db", "retain", "--dry-run"])

    assert cli._admin(args) == 0
    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        opened[0].execute("SELECT 1")


def test_db_dry_run_never_deletes_preexisting_live_wal_sidecars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    conn = _seed_retention_db(tmp_path)
    old = conn.execute("SELECT datetime('now', '-40 days')").fetchone()[0]
    latest = conn.execute("SELECT datetime('now')").fetchone()[0]
    _insert_run(conn, "old", _as_tz(old))
    _insert_run(conn, "latest", _as_tz(latest))
    db_path = Path(conn.execute("PRAGMA database_list").fetchone()[2])
    conn.close()
    writer = sqlite3.connect(db_path)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("BEGIN IMMEDIATE")
    writer.execute("UPDATE items SET title='active-writer' WHERE id='item-1'")
    main_before = _path_fingerprint(db_path)
    sidecars_before = _sidecar_extents(db_path)
    assert len(sidecars_before) == 2
    monkeypatch.setenv("AI_RADAR_DB", str(db_path))
    try:
        args = cli.build_parser().parse_args(
            ["admin", "db", "retain", "--dry-run"]
        )
        assert cli._admin(args) == 0
        assert capsys.readouterr().err == ""
        assert _path_fingerprint(db_path) == main_before
        _assert_sidecars_not_deleted_or_truncated(sidecars_before)
    finally:
        writer.rollback()
        writer.close()


def test_db_dry_run_never_deletes_writer_sidecars_created_after_initial_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    conn = _seed_retention_db(tmp_path)
    db_path = Path(conn.execute("PRAGMA database_list").fetchone()[2])
    conn.close()
    checkpoint = sqlite3.connect(db_path)
    try:
        checkpoint.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        checkpoint.close()
    for sidecar in _db_files(db_path)[1:]:
        sidecar.unlink(missing_ok=True)
    assert _sidecar_extents(db_path) == {}
    main_before = _path_fingerprint(db_path)
    monkeypatch.setenv("AI_RADAR_DB", str(db_path))
    real_connect = cli._connect_existing_db
    readers: list[sqlite3.Connection] = []
    writers: list[sqlite3.Connection] = []
    writer_sidecars: dict[Path, tuple[int, int]] = {}

    def connect_then_start_writer(
        path: Path,
        *,
        readonly: bool,
    ) -> sqlite3.Connection:
        reader = real_connect(path, readonly=readonly)
        readers.append(reader)
        writer = sqlite3.connect(path)
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE items SET title='racing-writer' WHERE id='item-1'")
        writers.append(writer)
        writer_sidecars.update(_sidecar_extents(path))
        assert writer_sidecars
        return reader

    monkeypatch.setattr(cli, "_connect_existing_db", connect_then_start_writer)
    args = cli.build_parser().parse_args(["admin", "db", "retain", "--dry-run"])
    try:
        assert cli._admin(args) == 0
        assert capsys.readouterr().err == ""
        assert _path_fingerprint(db_path) == main_before
        _assert_sidecars_not_deleted_or_truncated(writer_sidecars)
    finally:
        for writer in writers:
            writer.rollback()
            writer.close()
        for reader in readers:
            reader.close()


def test_db_slim_defaults_and_curate_hook_share_default_keep_days(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retain_args = cli.build_parser().parse_args(["admin", "db", "retain"])
    slim_args = cli.build_parser().parse_args(["admin", "db", "slim"])
    assert retain_args.keep_days == 7
    assert slim_args.keep_days == 7

    conn = _seed_retention_db(tmp_path)
    db_path = Path(conn.execute("PRAGMA database_list").fetchone()[2])
    conn.close()
    monkeypatch.setenv("AI_RADAR_DB", str(db_path))
    monkeypatch.setattr(
        cli,
        "curate",
        lambda *_args, **_kwargs: SimpleNamespace(
            id="new-run", output_curated_ids=[], threshold=6.5
        ),
    )
    monkeypatch.setattr(cli, "precompute_curated_summaries", lambda _conn, _run_id: 0)
    keep_days_seen: list[int] = []
    monkeypatch.setattr(
        cli,
        "retain_curated_summaries",
        lambda _conn, keep_days: keep_days_seen.append(keep_days) or 0,
    )

    assert cli._curate(cli.build_parser().parse_args(["curate"])) == 0
    assert keep_days_seen == [7]


@pytest.mark.parametrize("command", ["retain", "slim"])
@pytest.mark.parametrize("keep_days", ["-1", "not-a-number"])
def test_db_slim_rejects_invalid_keep_days_before_opening_db(
    command: str,
    keep_days: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = tmp_path / "must-not-exist.db"
    monkeypatch.setenv("AI_RADAR_DB", str(missing))

    with pytest.raises(SystemExit) as exc_info:
        cli.build_parser().parse_args(
            ["admin", "db", command, "--keep-days", keep_days]
        )

    assert exc_info.value.code == 2
    assert not missing.exists()


@pytest.mark.parametrize("command", ["retain", "slim"])
def test_db_slim_bad_path_returns_clear_error_without_creating_db(
    command: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = tmp_path / "missing" / "radar.db"
    monkeypatch.setenv("AI_RADAR_DB", str(missing))
    args = cli.build_parser().parse_args(["admin", "db", command])

    assert cli._admin(args) == 2
    captured = capsys.readouterr()
    assert "error=" in captured.err
    assert str(missing) in captured.err
    assert not missing.exists()


@pytest.mark.parametrize("command", ["retain", "slim"])
@pytest.mark.parametrize("explicit_path", ["", "   "])
def test_db_slim_rejects_explicit_blank_path_without_touching_env_or_default_db(
    command: str,
    explicit_path: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    env_dir = tmp_path / "env"
    default_dir = tmp_path / "default"
    env_dir.mkdir()
    default_dir.mkdir()
    env_conn = _seed_retention_db(env_dir)
    default_conn = _seed_retention_db(default_dir)
    for conn in (env_conn, default_conn):
        old = conn.execute("SELECT datetime('now', '-40 days')").fetchone()[0]
        latest = conn.execute("SELECT datetime('now')").fetchone()[0]
        _insert_run(conn, "old", _as_tz(old))
        _insert_run(conn, "latest", _as_tz(latest))
    env_path = Path(env_conn.execute("PRAGMA database_list").fetchone()[2])
    default_path = Path(default_conn.execute("PRAGMA database_list").fetchone()[2])
    env_conn.close()
    default_conn.close()
    for path in (env_path, default_path):
        checkpoint = sqlite3.connect(path)
        try:
            checkpoint.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            checkpoint.close()
        for sidecar in _db_files(path)[1:]:
            sidecar.unlink(missing_ok=True)
    env_before = _file_fingerprint(env_path)
    default_before = _file_fingerprint(default_path)
    monkeypatch.setenv("AI_RADAR_DB", str(env_path))
    monkeypatch.setattr(cli.db, "DEFAULT_DB_PATH", default_path)

    args = cli.build_parser().parse_args(
        ["admin", "db", command, "--db-path", explicit_path]
    )
    assert cli._admin(args) == 2
    captured = capsys.readouterr()
    assert "error=" in captured.err
    assert "path" in captured.err.lower()
    assert captured.out == ""
    assert _file_fingerprint(env_path) == env_before
    assert _file_fingerprint(default_path) == default_before


@pytest.mark.parametrize("command", ["retain", "slim"])
def test_db_slim_unwritable_db_returns_error_without_writing(
    command: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    conn = _seed_retention_db(tmp_path)
    old = conn.execute("SELECT datetime('now', '-40 days')").fetchone()[0]
    latest = conn.execute("SELECT datetime('now')").fetchone()[0]
    _insert_run(conn, "old", _as_tz(old))
    _insert_run(conn, "latest", _as_tz(latest))
    db_path = Path(conn.execute("PRAGMA database_list").fetchone()[2])
    conn.close()
    with sqlite3.connect(db_path) as checkpoint:
        checkpoint.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    monkeypatch.setenv("AI_RADAR_DB", str(db_path))
    db_path.chmod(0o444)
    before = _file_fingerprint(db_path)
    try:
        args = cli.build_parser().parse_args(["admin", "db", command])
        assert cli._admin(args) == 2
        captured = capsys.readouterr()
        assert "error=" in captured.err
        assert "not writable" in captured.err
        assert _file_fingerprint(db_path) == before
    finally:
        db_path.chmod(0o644)


def test_db_slim_vacuum_failure_reports_retryable_two_phase_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    conn = _seed_retention_db(tmp_path)
    old = conn.execute("SELECT datetime('now', '-40 days')").fetchone()[0]
    latest = conn.execute("SELECT datetime('now')").fetchone()[0]
    _insert_run(conn, "old", _as_tz(old))
    _insert_run(conn, "latest", _as_tz(latest))
    db_path = Path(conn.execute("PRAGMA database_list").fetchone()[2])
    before_runs = _table_digest(conn, "curation_runs")
    before_evaluations = _table_digest(conn, "item_evaluations")
    conn.close()
    monkeypatch.setenv("AI_RADAR_DB", str(db_path))
    real_vacuum = cli._vacuum_database

    def fail_vacuum(_conn: sqlite3.Connection) -> None:
        raise sqlite3.OperationalError("injected VACUUM failure")

    monkeypatch.setattr(cli, "_vacuum_database", fail_vacuum)
    args = cli.build_parser().parse_args(["admin", "db", "slim"])
    assert cli._admin(args) == 1
    captured = capsys.readouterr()
    assert "retained=true" in captured.out
    assert "compacted=false" in captured.out
    assert "injected VACUUM failure" in captured.out
    with sqlite3.connect(db_path) as check:
        assert _summary_by_run(check) == {
            "latest": '{"item_summary":"摘要"}',
            "old": None,
        }
        assert _table_digest(check, "curation_runs") == before_runs
        assert _table_digest(check, "item_evaluations") == before_evaluations

    monkeypatch.setattr(cli, "_vacuum_database", real_vacuum)
    assert cli._admin(args) == 0
    captured = capsys.readouterr()
    assert "retained=true" in captured.out
    assert "compacted=true" in captured.out


def test_db_slim_disk_preflight_failure_keeps_retained_state_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    conn = _seed_retention_db(tmp_path)
    old = conn.execute("SELECT datetime('now', '-40 days')").fetchone()[0]
    latest = conn.execute("SELECT datetime('now')").fetchone()[0]
    _insert_run(conn, "old", _as_tz(old))
    _insert_run(conn, "latest", _as_tz(latest))
    db_path = Path(conn.execute("PRAGMA database_list").fetchone()[2])
    conn.close()
    monkeypatch.setenv("AI_RADAR_DB", str(db_path))
    monkeypatch.setattr(cli, "_has_vacuum_space", lambda _path: False)

    args = cli.build_parser().parse_args(["admin", "db", "slim"])
    assert cli._admin(args) == 1
    output = capsys.readouterr().out
    assert "retained=true" in output
    assert "compacted=false" in output
    assert "insufficient disk space" in output
    with sqlite3.connect(db_path) as check:
        assert _summary_by_run(check)["old"] is None


def test_vacuum_space_preflight_uses_resolved_database_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_dir = tmp_path / "target-volume"
    link_dir = tmp_path / "link-volume"
    target_dir.mkdir()
    link_dir.mkdir()
    target = target_dir / "radar.db"
    target.write_bytes(b"sqlite-placeholder")
    link = link_dir / "radar.db"
    link.symlink_to(target)
    checked_paths: list[Path] = []

    def disk_usage(path: Path) -> SimpleNamespace:
        checked_paths.append(Path(path))
        return SimpleNamespace(free=target.stat().st_size * 2)

    monkeypatch.setattr(cli.shutil, "disk_usage", disk_usage)

    assert cli._has_vacuum_space(link) is True
    assert checked_paths == [target.resolve().parent]


def test_db_slim_post_commit_preflight_error_reports_two_phase_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    conn = _seed_retention_db(tmp_path)
    old = conn.execute("SELECT datetime('now', '-40 days')").fetchone()[0]
    latest = conn.execute("SELECT datetime('now')").fetchone()[0]
    _insert_run(conn, "old", _as_tz(old))
    _insert_run(conn, "latest", _as_tz(latest))
    db_path = Path(conn.execute("PRAGMA database_list").fetchone()[2])
    conn.close()
    monkeypatch.setenv("AI_RADAR_DB", str(db_path))

    def fail_preflight(_path: Path) -> bool:
        raise OSError("injected post-commit preflight failure")

    monkeypatch.setattr(cli, "_has_vacuum_space", fail_preflight)
    args = cli.build_parser().parse_args(["admin", "db", "slim"])

    assert cli._admin(args) == 1
    captured = capsys.readouterr()
    assert "retained=true" in captured.out
    assert "compacted=false" in captured.out
    assert "injected post-commit preflight failure" in captured.out
    assert captured.err == ""
    with sqlite3.connect(db_path) as check:
        assert _summary_by_run(check)["old"] is None
