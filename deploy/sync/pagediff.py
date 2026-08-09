#!/usr/bin/env python3
"""Report how many bytes actually differ between two SQLite snapshots.

Transfer cost for the replica is decided by changed *pages*, not by how many
rows were added: SQLite is a paged B-tree, so a handful of new items dirties
interior nodes and index pages scattered across the file.

This exists because that gap was large enough to invalidate a plan. Two clean
measurements of one 15-minute pipeline round showed ~314 MiB changing while the
real content delta was under 5 MiB -- the rest was migration 003 rebuilding the
whole FTS index every run. Without a page-level number that cause stays
invisible and "syncing is expensive" looks like a property of the database.

Usage:
    pagediff.py OLD.db NEW.db [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def page_size(path: Path) -> int:
    """Read the page size from the SQLite header (offset 16, big-endian u16).

    Taken from the file rather than assumed: a mismatch between the two
    snapshots makes a page-by-page comparison meaningless, and defaulting to
    4096 would silently produce a plausible-looking wrong answer.
    """
    with path.open("rb") as handle:
        header = handle.read(100)
    if len(header) < 100 or header[:16] != b"SQLite format 3\x00":
        raise ValueError(f"{path} is not a SQLite database")
    raw = int.from_bytes(header[16:18], "big")
    # The header stores 65536 as 1.
    return 65536 if raw == 1 else raw


def compare(old: Path, new: Path) -> dict[str, int | float]:
    size = page_size(new)
    if page_size(old) != size:
        raise ValueError("snapshots use different page sizes; not comparable")

    same = changed = added = 0
    with old.open("rb") as a, new.open("rb") as b:
        while True:
            pa = a.read(size)
            pb = b.read(size)
            if not pb:
                break
            if not pa:
                added += 1
            elif pa == pb:
                same += 1
            else:
                changed += 1

    total = same + changed + added
    transfer_pages = changed + added
    return {
        "page_size": size,
        "total_pages": total,
        "same_pages": same,
        "changed_pages": changed,
        "added_pages": added,
        "transfer_bytes": transfer_pages * size,
        "transfer_mib": round(transfer_pages * size / 2**20, 1),
        "total_mib": round(total * size / 2**20, 1),
        "changed_pct": round(transfer_pages / total * 100, 2) if total else 0.0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old", type=Path)
    parser.add_argument("new", type=Path)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    for path in (args.old, args.new):
        if not path.is_file():
            print(f"pagediff: no such file: {path}", file=sys.stderr)
            return 2

    try:
        result = compare(args.old, args.new)
    except ValueError as exc:
        print(f"pagediff: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result))
    else:
        print(f"page size     {result['page_size']} B")
        print(f"total         {result['total_pages']} pages ({result['total_mib']} MiB)")
        print(f"same          {result['same_pages']}")
        print(f"changed       {result['changed_pages']}")
        print(f"added         {result['added_pages']}")
        print(
            f"would transfer {result['transfer_mib']} MiB "
            f"({result['changed_pct']}% of the file)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
