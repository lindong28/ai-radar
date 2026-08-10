#!/usr/bin/env python3
"""Create a consistent SQLite snapshot without opening the source read-only.

WAL readers may need to create or update ``-shm`` even when they never change
database content, so the source is opened with SQLite ``mode=rw``.  The first
SQL statement on that connection enables ``PRAGMA query_only`` and the second
verifies it before the destination is opened or backup begins.  Source-path
alias and inode safety remain the caller's responsibility.
"""

from __future__ import annotations

import argparse
import sqlite3
from collections.abc import Callable, Sequence
from pathlib import Path

BackupProgress = Callable[[int, int, int], None]


def backup_database(
    source: Path,
    destination: Path,
    *,
    pages: int = -1,
    progress: BackupProgress | None = None,
) -> None:
    """Back up ``source`` consistently while enforcing a query-only source."""
    source_uri = f"{source.resolve().as_uri()}?mode=rw"
    source_connection = sqlite3.connect(source_uri, uri=True)
    try:
        source_connection.execute("PRAGMA query_only=ON")
        query_only = source_connection.execute("PRAGMA query_only").fetchone()
        if query_only != (1,):
            raise RuntimeError("SQLite did not enable query_only on the source")

        destination_connection = sqlite3.connect(destination)
        try:
            source_connection.backup(
                destination_connection,
                pages=pages,
                progress=progress,
            )
        finally:
            destination_connection.close()
    finally:
        source_connection.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create a consistent SQLite backup from a query-only WAL reader."
    )
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args(argv)
    backup_database(args.source, args.destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
