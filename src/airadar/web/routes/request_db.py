from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import Request


@contextmanager
def conn_from_request(request: Request) -> Iterator[sqlite3.Connection]:
    # Yields a per-request connection and always closes it on exit. A bare
    # `sqlite3.connect()` returned to a `with` block is NOT closed by that block
    # (sqlite3's context manager only manages the transaction), which leaks the
    # connection each request. Leaked read connections pin WAL read-marks so
    # PASSIVE autocheckpoint can never reset the WAL — it grows unbounded and
    # eventually surfaces as intermittent SQLITE_CANTOPEN 500s on read routes.
    conn = sqlite3.connect(request.app.state.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
    finally:
        conn.close()
