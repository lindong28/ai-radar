#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any

from dotenv import dotenv_values

from airadar import db
from airadar.llm_usage import migrate_usage_db
from airadar.provider.base import ProviderItem
from airadar.provider.deepseek_v32 import DeepSeekV32Prefilter


@dataclass(frozen=True)
class ProbeResult:
    mode: str
    db_path: str
    usage_db_path: str
    calls: int
    latencies_ms: list[int]
    writer_commits: int
    writer_lock_errors: int

    def summary(self) -> dict[str, Any]:
        ordered = sorted(self.latencies_ms)
        p95_index = max(0, min(len(ordered) - 1, int((len(ordered) * 0.95 + 0.999999) - 1)))
        return {
            "mode": self.mode,
            "db_path": self.db_path,
            "usage_db_path": self.usage_db_path,
            "calls": self.calls,
            "p50_ms": int(median(ordered)) if ordered else None,
            "p95_ms": ordered[p95_index] if ordered else None,
            "max_ms": max(ordered) if ordered else None,
            "latencies_ms": self.latencies_ms,
            "writer_commits": self.writer_commits,
            "writer_lock_errors": self.writer_lock_errors,
        }


def _load_runtime_env() -> None:
    values: dict[str, str] = {}
    for env_path in (Path.home() / ".claude" / ".env", db.PROJECT_ROOT / ".env"):
        if not env_path.exists():
            continue
        for key, value in dotenv_values(env_path).items():
            if value is not None:
                values[key] = value
    for key, value in values.items():
        os.environ.setdefault(key, value)


def _ensure_probe_table(db_path: Path) -> None:
    with db.get_conn(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS _llm_usage_contention_probe (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              worker INTEGER NOT NULL,
              created_at REAL NOT NULL
            )
            """
        )
        conn.commit()


def _hammer_writer(
    *,
    db_path: Path,
    worker: int,
    stop: threading.Event,
    hold_seconds: float,
    stats: dict[str, int],
    lock: threading.Lock,
) -> None:
    conn = db.get_conn(db_path)
    try:
        while not stop.is_set():
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "INSERT INTO _llm_usage_contention_probe(worker, created_at) VALUES (?, ?)",
                    (worker, time.time()),
                )
                time.sleep(hold_seconds)
                conn.commit()
                with lock:
                    stats["commits"] += 1
            except sqlite3.OperationalError:
                conn.rollback()
                with lock:
                    stats["lock_errors"] += 1
                time.sleep(0.02)
    finally:
        conn.close()


def _start_hammer(db_path: Path, *, writers: int, hold_ms: int) -> tuple[threading.Event, list[threading.Thread], dict[str, int]]:
    _ensure_probe_table(db_path)
    stop = threading.Event()
    stats = {"commits": 0, "lock_errors": 0}
    lock = threading.Lock()
    threads = [
        threading.Thread(
            target=_hammer_writer,
            kwargs={
                "db_path": db_path,
                "worker": worker,
                "stop": stop,
                "hold_seconds": hold_ms / 1000,
                "stats": stats,
                "lock": lock,
            },
            daemon=True,
        )
        for worker in range(writers)
    ]
    for thread in threads:
        thread.start()
    time.sleep(0.3)
    return stop, threads, stats


def _stop_hammer(stop: threading.Event, threads: list[threading.Thread]) -> None:
    stop.set()
    for thread in threads:
        thread.join(timeout=10)


def _probe_item(index: int) -> ProviderItem:
    return ProviderItem(
        id=f"contention-probe-{int(time.time())}-{index}",
        title="New LLM inference benchmark and deployment notes",
        url=f"https://example.com/ai-radar/contention-probe-{index}",
        source_id="contention-probe",
        tier="T1",
        author="AI Radar Probe",
        published_at="2026-06-24T00:00:00Z",
        content_text=(
            "A practical report about a new large language model inference stack, "
            "including API behavior, latency, deployment tradeoffs, and engineering notes."
        ),
    )


def _run_prefilter_calls(calls: int) -> list[int]:
    provider = DeepSeekV32Prefilter()
    latencies: list[int] = []
    for index in range(calls):
        started = time.perf_counter()
        result = provider.is_ai_related(_probe_item(index))
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        latencies.append(elapsed_ms)
        print(
            f"call {index + 1}/{calls} latency_ms={elapsed_ms} "
            f"is_ai_related={result.is_ai_related} confidence={result.confidence:.2f}",
            flush=True,
        )
    return latencies


def _run_mode(
    *,
    mode: str,
    db_path: Path,
    usage_db_path: Path,
    calls: int,
    writers: int,
    hold_ms: int,
) -> ProbeResult:
    os.environ["AI_RADAR_DB"] = str(db_path)
    os.environ["AI_RADAR_LLM_USAGE_DB"] = str(usage_db_path)
    migrate_usage_db(usage_db_path=usage_db_path, main_db_path=db_path)
    stop, threads, stats = _start_hammer(db_path, writers=writers, hold_ms=hold_ms)
    try:
        latencies = _run_prefilter_calls(calls)
    finally:
        _stop_hammer(stop, threads)
    return ProbeResult(
        mode=mode,
        db_path=str(db_path),
        usage_db_path=str(usage_db_path),
        calls=calls,
        latencies_ms=latencies,
        writer_commits=stats["commits"],
        writer_lock_errors=stats["lock_errors"],
    )


def _legacy_copy(source_db: Path) -> Path:
    tmp_dir = Path(tempfile.mkdtemp(prefix="ai-radar-legacy-contention-"))
    target = tmp_dir / "radar.db"
    shutil.copy2(source_db, target)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Prove llm_usage writes no longer contend with the main DB.")
    parser.add_argument("--db", default=str(db.DEFAULT_DB_PATH), help="Main radar.db path to hammer.")
    parser.add_argument("--usage-db", default=None, help="Dedicated llm_usage DB path for split mode.")
    parser.add_argument("--calls", type=int, default=5)
    parser.add_argument("--writers", type=int, default=4)
    parser.add_argument("--hold-ms", type=int, default=250)
    parser.add_argument(
        "--include-legacy-copy",
        action="store_true",
        help="Also run a legacy comparison against a temporary copy where usage writes target the main DB.",
    )
    args = parser.parse_args()

    _load_runtime_env()
    if not (os.environ.get("ARK_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")):
        raise SystemExit("ARK_API_KEY or DEEPSEEK_API_KEY is required for real prefilter calls")

    main_db = db.resolve_db_path(args.db)
    usage_db = Path(args.usage_db) if args.usage_db else db.PROJECT_ROOT / "data" / "llm_usage.db"
    if not usage_db.is_absolute():
        usage_db = db.PROJECT_ROOT / usage_db

    results = [
        _run_mode(
            mode="split",
            db_path=main_db,
            usage_db_path=usage_db,
            calls=args.calls,
            writers=args.writers,
            hold_ms=args.hold_ms,
        )
    ]
    if args.include_legacy_copy:
        legacy_db = _legacy_copy(main_db)
        results.append(
            _run_mode(
                mode="legacy-main-usage-copy",
                db_path=legacy_db,
                usage_db_path=legacy_db,
                calls=args.calls,
                writers=args.writers,
                hold_ms=args.hold_ms,
            )
        )

    payload = {"results": [result.summary() for result in results]}
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
