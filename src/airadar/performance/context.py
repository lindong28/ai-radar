from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path

from .stage_ledger import LedgerSnapshot, classify_interval


def collect_probe_context(
    *,
    ledger_before: LedgerSnapshot,
    ledger_after: LedgerSnapshot,
    db_path: Path,
    host_cpu_percent: float | None = None,
) -> dict[str, object]:
    classification = classify_interval(ledger_before, ledger_after)
    try:
        dirty = bool(
            subprocess.run(
                ["/usr/bin/git", "status", "--porcelain"], capture_output=True, check=True, text=True
            ).stdout
        )
    except (OSError, subprocess.CalledProcessError):
        dirty = True
    cpu_started = time.monotonic_ns()
    cpu_value: float | str
    if host_cpu_percent is None:
        try:
            output = subprocess.run(
                ["/bin/ps", "-A", "-o", "%cpu="], check=True, capture_output=True, text=True, timeout=2
            ).stdout
            cpu_value = min(100.0, sum(float(value) for value in output.split()) / max(1, os.cpu_count() or 1))
        except (OSError, ValueError, subprocess.SubprocessError):
            cpu_value = "unknown"
    else:
        cpu_value = host_cpu_percent
    cpu_interval_ms = (time.monotonic_ns() - cpu_started) / 1_000_000
    try:
        load_1m: float | str = os.getloadavg()[0]
    except OSError:
        load_1m = "unknown"
    try:
        memory_output = subprocess.run(
            ["/usr/sbin/sysctl", "-n", "hw.memsize"], check=True, capture_output=True, text=True, timeout=2
        ).stdout.strip()
        total_memory = int(memory_output)
        vm_output = subprocess.run(
            ["/usr/bin/vm_stat"], check=True, capture_output=True, text=True, timeout=2
        ).stdout
        page_size = int(vm_output.split("page size of ", 1)[1].split(" bytes", 1)[0])
        counts = {
            line.split(":", 1)[0]: int(line.split(":", 1)[1].strip().rstrip("."))
            for line in vm_output.splitlines()[1:]
            if ":" in line
        }
        available = (counts.get("Pages free", 0) + counts.get("Pages speculative", 0)) * page_size
        host_memory_bytes: dict[str, int] | str = {
            "used": max(0, total_memory - available),
            "total": total_memory,
        }
    except (OSError, ValueError, subprocess.SubprocessError):
        host_memory_bytes = "unknown"
    row_counts: dict[str, int] | str = "unknown"
    if db_path.exists():
        try:
            connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1)
            try:
                row_counts = {
                    table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                    for table in ("items", "curated_items", "wechat_interpretations")
                }
            finally:
                connection.close()
        except (sqlite3.Error, TypeError):
            row_counts = "unknown"
    sampled_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    db_bytes: int | str = db_path.stat().st_size if db_path.exists() else "unknown"
    wal_path = db_path.with_name(db_path.name + "-wal")
    wal_bytes: int | str = wal_path.stat().st_size if wal_path.exists() else "unknown"
    diagnostic_fields = {
        "host_cpu_percent": {"unit": "percent", "sampled_at": sampled_at},
        "host_cpu_interval_ms": {"unit": "ms", "sampled_at": sampled_at},
        "host_load_1m": {"unit": "load_1m", "sampled_at": sampled_at},
        "host_memory_bytes": {"unit": "bytes", "sampled_at": sampled_at},
        "db_bytes": {"unit": "bytes", "sampled_at": sampled_at},
        "wal_bytes": {"unit": "bytes", "sampled_at": sampled_at},
    }
    return {
        "git_dirty": dirty,
        "pipeline": {"status": classification.load_class, "reason": classification.reason},
        "host_cpu_percent": cpu_value,
        "host_cpu_interval_ms": cpu_interval_ms,
        "host_load_1m": load_1m,
        "host_memory_bytes": host_memory_bytes,
        "db_bytes": db_bytes,
        "wal_bytes": wal_bytes,
        "sampled_at": sampled_at,
        "log_summary": json.dumps(
            {
                "diagnostic_fields": diagnostic_fields,
                "row_counts": row_counts,
                "service": os.environ.get("AI_RADAR_SERVICE_STATE", "unknown"),
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
    }
