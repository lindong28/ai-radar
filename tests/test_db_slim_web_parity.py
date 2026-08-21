from __future__ import annotations

import hashlib
import json
import math
import os
import socket
import sqlite3
import subprocess
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

import pytest

from airadar import db
from airadar.performance.budgets import evaluate_samples
from airadar.performance.config import load_local_engineering_config
from scripts.web_contract_golden import HttpSpec, capture, logical_db_invariant

PRODUCTION_DB = Path("/Users/lindong/research/ai-radar/data/radar.db")


def _query_digest(conn: sqlite3.Connection, query: str) -> str:
    digest = hashlib.sha256()
    for row in conn.execute(query):
        for value in row:
            if value is None:
                tag, payload = b"n", b""
            elif isinstance(value, bytes):
                tag, payload = b"b", value
            elif isinstance(value, str):
                tag, payload = b"s", value.encode()
            elif isinstance(value, int):
                tag, payload = b"i", str(value).encode()
            elif isinstance(value, float):
                tag, payload = b"f", value.hex().encode()
            else:  # pragma: no cover - SQLite only returns the types above
                raise TypeError(type(value))
            digest.update(tag)
            digest.update(len(payload).to_bytes(8, "big"))
            digest.update(payload)
    return digest.hexdigest()


def _pick_representatives(values: Sequence[str], count: int = 3) -> tuple[str, ...]:
    assert values
    indexes = (0, len(values) // 2, len(values) - 1)
    return tuple(dict.fromkeys(values[index] for index in indexes))[:count]


def _normalized_db_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).replace(microsecond=0)


def _expected_cleared_keys(
    conn: sqlite3.Connection,
    *,
    keep_days: int,
) -> tuple[set[tuple[str, str]], int]:
    run_times = {
        str(run_id): _normalized_db_timestamp(str(created_at))
        for run_id, created_at in conn.execute("SELECT id, created_at FROM curation_runs")
    }
    assert run_times
    latest_timestamp = max(run_times.values())
    cutoff = datetime.now(UTC).replace(microsecond=0) - timedelta(days=keep_days)
    eligible_runs = {
        run_id
        for run_id, created_at in run_times.items()
        if created_at < cutoff and created_at != latest_timestamp
    }
    expected: set[tuple[str, str]] = set()
    logical_bytes = 0
    for run_id, item_id, summary_bytes in conn.execute(
        """
        SELECT run_id, item_id, LENGTH(CAST(summary_json AS BLOB))
        FROM curated_items WHERE summary_json IS NOT NULL
        """
    ):
        key = str(run_id), str(item_id)
        if key[0] in eligible_runs:
            expected.add(key)
            logical_bytes += int(summary_bytes)
    return expected, logical_bytes


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("0.0.0.0", 0))
        port = int(listener.getsockname()[1])
    assert port != 8000
    return port


@contextmanager
def _serve(db_path: Path, port: int) -> Iterator[str]:
    environment = os.environ.copy()
    environment["AI_RADAR_DB"] = str(db_path)
    process = subprocess.Popen(
        [
            str(db.PROJECT_ROOT / "run.sh"),
            "serve",
            "--host",
            "0.0.0.0",
            "--port",
            str(port),
            "--pre-migrated-db",
        ],
        cwd=db.PROJECT_ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 180
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                output = process.stdout.read() if process.stdout is not None else ""
                raise AssertionError(f"serve exited early: {output}")
            try:
                with urlopen(f"{base_url}/api/v1/healthz", timeout=1) as response:  # noqa: S310
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.1)
        else:
            raise AssertionError("serve did not become ready within 180 seconds")
        yield base_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def _manifest(directory: Path) -> dict[str, dict[str, object]]:
    payload = json.loads((directory / "request-manifest.json").read_text())
    return {entry["artifact"]: entry for entry in payload["artifacts"]}


def _artifact_data(directory: Path, artifact: str) -> dict[str, object]:
    payload = json.loads((directory / artifact).read_text())
    assert payload["success"] is True
    assert payload["error"] is None
    return payload["data"]


def _http_specs(conn: sqlite3.Connection) -> tuple[tuple[HttpSpec, ...], tuple[HttpSpec, ...], str]:
    dates = [
        row[0]
        for row in conn.execute(
            """
            SELECT DISTINCT date(datetime(i.published_at, '+08:00')) AS published_date
            FROM items i
            JOIN curated_items c ON c.item_id=i.id
            WHERE published_date IS NOT NULL
            ORDER BY published_date
            """
        )
    ]
    sampled_dates = _pick_representatives(dates)
    wechat = conn.execute(
        """
        SELECT wi.slug, i.author
        FROM wechat_interpretations wi
        JOIN items i ON i.id=wi.item_id
        WHERE wi.save_decision=1 AND COALESCE(i.author, '') <> ''
        ORDER BY i.published_at DESC, i.id DESC
        LIMIT 1
        """
    ).fetchone()
    assert wechat is not None
    wechat_slug, wechat_query = str(wechat[0]), str(wechat[1])
    stable: list[HttpSpec] = [
        HttpSpec("curated-latest.json", "/api/v1/curated", "api-json"),
        HttpSpec("timeline.json", "/api/v1/timeline", "api-json"),
        HttpSpec("wechat.json", "/api/v1/wechat", "api-json"),
        HttpSpec(
            "wechat-slug-list.json",
            f"/api/v1/wechat?{urlencode({'q': wechat_query, 'limit': 50})}",
            "api-json",
        ),
        HttpSpec("search-openai.json", "/api/v1/curated?q=OpenAI", "api-json"),
        HttpSpec("search-claude.json", "/api/v1/curated?q=Claude", "api-json"),
    ]
    stable.extend(
        HttpSpec(
            f"curated-date-{index}.json",
            f"/api/v1/curated?{urlencode({'date': selected_date})}",
            "api-json",
        )
        for index, selected_date in enumerate(sampled_dates)
    )
    eligible_runs = [
        row[0]
        for row in conn.execute(
            """
            SELECT cr.id
            FROM curation_runs cr
            WHERE datetime(cr.created_at) <> (
              SELECT MAX(datetime(created_at)) FROM curation_runs
            )
              AND datetime(cr.created_at) < datetime('now', '-7 days')
              AND EXISTS (
                SELECT 1 FROM curated_items ci
                WHERE ci.run_id=cr.id AND ci.summary_json IS NOT NULL
              )
            ORDER BY cr.created_at, cr.id
            """
        )
    ]
    historical = tuple(
        HttpSpec(
            f"historical-run-{index}.json",
            f"/api/v1/curated?{urlencode({'run_id': run_id})}",
            "api-json",
        )
        for index, run_id in enumerate(_pick_representatives(eligible_runs))
    )
    return tuple(stable), historical, wechat_slug


def _run_slim(db_path: Path) -> subprocess.CompletedProcess[str]:
    return _run_cli(db_path, "admin", "db", "slim", timeout=300)


def _run_cli(
    db_path: Path,
    *arguments: str,
    timeout: int = 300,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["AI_RADAR_DB"] = str(db_path)
    return subprocess.run(
        [str(db.PROJECT_ROOT / "run.sh"), *arguments],
        cwd=db.PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def test_db_slim_bounded_retain_and_real_curate_hook() -> None:
    if os.environ.get("AI_RADAR_DB_SLIM_INTEGRATION") != "1":
        pytest.skip("set AI_RADAR_DB_SLIM_INTEGRATION=1 on a disposable copy")
    db_path = db.resolve_db_path()
    assert db_path.resolve() != PRODUCTION_DB.resolve()
    db_path.resolve().relative_to((db.PROJECT_ROOT / "data").resolve())
    with sqlite3.connect(db_path) as conn:
        item_id = str(conn.execute("SELECT id FROM items ORDER BY id LIMIT 1").fetchone()[0])
        latest_run = str(
            conn.execute(
                "SELECT id FROM curation_runs ORDER BY created_at DESC, id DESC LIMIT 1"
            ).fetchone()[0]
        )
        evaluations_digest = _query_digest(
            conn,
            "SELECT * FROM item_evaluations ORDER BY id",
        )
        conn.execute(
            """
            INSERT INTO curation_runs (
              id, ruleset_version, weights_json, threshold, input_eval_ids,
              output_curated_ids, created_at
            ) VALUES (
              'l2-bounded-old', 'l2', '{}', 6.5, '[101]', json_array(?),
              strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-40 days')
            )
            """,
            (item_id,),
        )
        conn.execute(
            """
            INSERT INTO curation_runs (
              id, ruleset_version, weights_json, threshold, input_eval_ids,
              output_curated_ids, created_at
            ) VALUES (
              'l2-bounded-within', 'l2', '{}', 6.5, '[202]', json_array(?),
              strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-3 days')
            )
            """,
            (item_id,),
        )
        conn.executemany(
            """
            INSERT INTO curated_items (
              run_id, item_id, weighted_score, rank, reason_json, summary_json
            ) VALUES (?, ?, 8.0, 1, '{}', '{"item_summary":"l2"}')
            """,
            (("l2-bounded-old", item_id), ("l2-bounded-within", item_id)),
        )
        conn.commit()

    retained = _run_cli(db_path, "admin", "db", "retain")
    assert retained.returncode == 0, retained
    assert "retained=true" in retained.stdout
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT summary_json FROM curated_items WHERE run_id='l2-bounded-old'"
        ).fetchone()[0] is None
        assert conn.execute(
            "SELECT summary_json FROM curated_items WHERE run_id='l2-bounded-within'"
        ).fetchone()[0] is not None
        latest_counts = conn.execute(
            "SELECT COUNT(summary_json), COUNT(*) FROM curated_items WHERE run_id=?",
            (latest_run,),
        ).fetchone()
        assert latest_counts[0] == latest_counts[1] > 0
        assert conn.execute(
            "SELECT input_eval_ids FROM curation_runs WHERE id='l2-bounded-old'"
        ).fetchone()[0] == "[101]"
        assert conn.execute(
            "SELECT input_eval_ids FROM curation_runs WHERE id='l2-bounded-within'"
        ).fetchone()[0] == "[202]"
        assert _query_digest(conn, "SELECT * FROM item_evaluations ORDER BY id") == evaluations_digest
        conn.execute(
            """
            INSERT INTO curation_runs (
              id, ruleset_version, weights_json, threshold, input_eval_ids,
              output_curated_ids, created_at
            ) VALUES (
              'l2-curate-hook-old', 'l2', '{}', 6.5, '[303]', json_array(?),
              strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-40 days')
            )
            """,
            (item_id,),
        )
        conn.execute(
            """
            INSERT INTO curated_items (
              run_id, item_id, weighted_score, rank, reason_json, summary_json
            ) VALUES (
              'l2-curate-hook-old', ?, 8.0, 1, '{}', '{"item_summary":"hook"}'
            )
            """,
            (item_id,),
        )
        conn.commit()

    curated = _run_cli(db_path, "curate")
    assert curated.returncode == 0, f"stdout={curated.stdout}\nstderr={curated.stderr}"
    assert "curate run_id=" in curated.stdout
    with sqlite3.connect(db_path) as conn:
        new_run = str(
            conn.execute(
                "SELECT id FROM curation_runs ORDER BY created_at DESC, id DESC LIMIT 1"
            ).fetchone()[0]
        )
        assert new_run not in {latest_run, "l2-bounded-within"}
        new_counts = conn.execute(
            "SELECT COUNT(summary_json), COUNT(*) FROM curated_items WHERE run_id=?",
            (new_run,),
        ).fetchone()
        assert new_counts[0] == new_counts[1] > 0
        assert conn.execute(
            "SELECT summary_json FROM curated_items WHERE run_id='l2-curate-hook-old'"
        ).fetchone()[0] is None
        assert conn.execute(
            "SELECT summary_json FROM curated_items WHERE run_id='l2-bounded-within'"
        ).fetchone()[0] is not None
        assert conn.execute(
            """
            SELECT run_id FROM curated_items GROUP BY run_id
            HAVING COUNT(summary_json) NOT IN (0, COUNT(*))
            """
        ).fetchall() == []
        assert _query_digest(conn, "SELECT * FROM item_evaluations ORDER BY id") == evaluations_digest
    print(
        "L2 BOUNDED+CURATE PASS "
        f"retain={retained.stdout.strip()} curate={curated.stdout.strip()} new_run={new_run}"
    )


def test_db_slim_web_and_storage_parity_on_real_copy(tmp_path: Path) -> None:
    if os.environ.get("AI_RADAR_DB_SLIM_L2") != "1":
        pytest.skip("set AI_RADAR_DB_SLIM_L2=1 to run the destructive real-copy L2 adapter")
    db_path = db.resolve_db_path()
    assert db_path.is_absolute() and db_path.is_file()
    assert db_path.resolve() != PRODUCTION_DB.resolve()
    db_path.resolve().relative_to((db.PROJECT_ROOT / "data").resolve())

    with sqlite3.connect(db_path) as checkpoint:
        checkpoint.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    before_size = db_path.stat().st_size
    before_invariant = logical_db_invariant(db_path)
    before_tables = before_invariant["tables"]
    assert isinstance(before_tables, dict)
    with sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        row_count = int(conn.execute("SELECT COUNT(*) FROM curated_items").fetchone()[0])
        key_digest = _query_digest(
            conn,
            "SELECT run_id, item_id FROM curated_items ORDER BY run_id, item_id",
        )
        non_summary_digest = _query_digest(
            conn,
            """
            SELECT run_id, item_id, weighted_score, rank, reason_json
            FROM curated_items ORDER BY run_id, item_id
            """,
        )
        before_nonnull = {
            (str(row[0]), str(row[1]))
            for row in conn.execute(
                "SELECT run_id, item_id FROM curated_items WHERE summary_json IS NOT NULL"
            )
        }
        generations = tuple(
            conn.execute(
                "SELECT archive_generation, category_generation FROM archive_cache_generations WHERE id=1"
            ).fetchone()
        )
        stable_specs, historical_specs, wechat_slug = _http_specs(conn)
        fts_term = next(
            (
                term
                for term in ("OpenAI", "Claude", "AI")
                if conn.execute(
                    "SELECT COUNT(*) FROM items_fts WHERE items_fts MATCH ?", (term,)
                ).fetchone()[0]
            ),
            None,
        )
        assert fts_term is not None
        fts_hits_before = int(
            conn.execute(
                "SELECT COUNT(*) FROM items_fts WHERE items_fts MATCH ?", (fts_term,)
            ).fetchone()[0]
        )

    before_http = tmp_path / "before-http"
    port = _free_port()
    with _serve(db_path, port) as base_url:
        capture(
            base_url,
            before_http,
            concurrency=4,
            specs=(*stable_specs, *historical_specs),
        )
    before_manifest = _manifest(before_http)
    for artifact in ("search-openai.json", "search-claude.json"):
        assert int(_artifact_data(before_http, artifact)["count"]) > 0
    wechat_items = _artifact_data(before_http, "wechat-slug-list.json")["items"]
    assert wechat_slug in {item["slug"] for item in wechat_items}

    with sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True) as conn:
        expected_cleared, logical_cleared_bytes = _expected_cleared_keys(
            conn, keep_days=7
        )
    assert expected_cleared <= before_nonnull

    slim = _run_slim(db_path)
    assert slim.returncode == 0, f"stdout={slim.stdout}\nstderr={slim.stderr}"
    assert "retained=true" in slim.stdout
    assert "compacted=true" in slim.stdout
    assert f"cleared_rows={len(expected_cleared)}" in slim.stdout
    print(f"SLIM CLI {slim.stdout.strip()}")

    after_http = tmp_path / "after-http"
    with _serve(db_path, port) as base_url:
        capture(
            base_url,
            after_http,
            concurrency=4,
            specs=(*stable_specs, *historical_specs),
        )
        samples: list[float] = []
        historical_url = f"{base_url}{historical_specs[0].path}"
        for _ in range(20):
            started = time.perf_counter()
            with urlopen(historical_url, timeout=30) as response:  # noqa: S310
                assert response.status == 200
                response.read()
            samples.append((time.perf_counter() - started) * 1000)
    after_manifest = _manifest(after_http)
    for spec in stable_specs:
        assert before_manifest[spec.artifact]["sha256"] == after_manifest[spec.artifact]["sha256"]
    for spec in historical_specs:
        # Historical summaries intentionally expire; TTL parity is item identity/count only.
        before_data = _artifact_data(before_http, spec.artifact)
        after_data = _artifact_data(after_http, spec.artifact)
        before_ids = {item["id"] for item in before_data["items"]}
        after_ids = {item["id"] for item in after_data["items"]}
        assert int(before_data["count"]) == len(before_ids)
        assert int(after_data["count"]) == len(after_ids)
        assert before_ids == after_ids
    performance = evaluate_samples(
        samples,
        load_local_engineering_config().budgets["curated_api"],
    )
    with sqlite3.connect(f"{db_path.as_uri()}?mode=rw", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        assert int(conn.execute("SELECT COUNT(*) FROM curated_items").fetchone()[0]) == row_count
        assert _query_digest(
            conn,
            "SELECT run_id, item_id FROM curated_items ORDER BY run_id, item_id",
        ) == key_digest
        assert _query_digest(
            conn,
            """
            SELECT run_id, item_id, weighted_score, rank, reason_json
            FROM curated_items ORDER BY run_id, item_id
            """,
        ) == non_summary_digest
        after_nonnull = {
            (str(row[0]), str(row[1]))
            for row in conn.execute(
                "SELECT run_id, item_id FROM curated_items WHERE summary_json IS NOT NULL"
            )
        }
        assert before_nonnull - after_nonnull == expected_cleared
        assert after_nonnull <= before_nonnull
        assert conn.execute(
            """
            SELECT run_id FROM curated_items GROUP BY run_id
            HAVING COUNT(summary_json) NOT IN (0, COUNT(*))
            """
        ).fetchall() == []
        assert tuple(
            conn.execute(
                "SELECT archive_generation, category_generation FROM archive_cache_generations WHERE id=1"
            ).fetchone()
        ) == generations
        conn.execute("INSERT INTO items_fts(items_fts) VALUES('integrity-check')")
        item_ids = {row[0] for row in conn.execute("SELECT id FROM items")}
        fts_ids = {row[0] for row in conn.execute("SELECT item_id FROM items_fts")}
        assert item_ids == fts_ids
        fts_hits_after = int(
            conn.execute(
                "SELECT COUNT(*) FROM items_fts WHERE items_fts MATCH ?", (fts_term,)
            ).fetchone()[0]
        )
        assert fts_hits_after == fts_hits_before > 0

    with sqlite3.connect(db_path) as checkpoint:
        checkpoint.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    after_invariant = logical_db_invariant(db_path)
    after_tables = after_invariant["tables"]
    assert isinstance(after_tables, dict)
    assert after_invariant["schema_sha256"] == before_invariant["schema_sha256"]
    for table in ("curation_runs", "item_evaluations", "items", "feedback"):
        assert after_tables[table] == before_tables[table]

    after_size = db_path.stat().st_size
    reclaimed = before_size - after_size
    print(
        "L2 REAL COPY "
        f"before_size={before_size} after_size={after_size} reclaimed={reclaimed} "
        f"ratio={after_size / before_size:.6f} cleared_rows={len(expected_cleared)} "
        f"fts_term={fts_term} fts_hits={fts_hits_after} port={port} "
        f"fallback_p95_ms={performance.display_p95_ms}"
    )
    failures: list[str] = []
    minimum_reclaim = math.floor(logical_cleared_bytes * 0.9)
    if after_size >= before_size:
        failures.append(f"growth failed: before_size={before_size} after_size={after_size}")
    if reclaimed < minimum_reclaim:
        failures.append(
            f"reclaim failed: reclaimed={reclaimed} minimum={minimum_reclaim}"
        )
    if after_size > 1_800_000_000:
        failures.append(f"gross size failed: after_size={after_size} limit=1800000000")
    assert not failures, "; ".join(failures)
