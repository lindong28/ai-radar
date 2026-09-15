#!/usr/bin/env python3
"""Offline input-archive audit/freeze. Deletion requires explicit prune --apply."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from airadar.fetcher.raw_capture import coverage, freeze, prune, retention_candidates  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    audit = commands.add_parser("coverage")
    for key in ("start", "end", "enabled-at"):
        audit.add_argument("--" + key, required=True)
    audit.add_argument("--cadence-seconds", type=int, required=True)
    audit.add_argument("--source", action="append", required=True)
    audit.add_argument("--json", action="store_true")
    frozen = commands.add_parser("freeze")
    frozen.add_argument("--run", action="append", required=True)
    frozen.add_argument("--destination", type=Path, required=True)
    retention = commands.add_parser("retention-preview")
    retention.add_argument("--days", type=int, default=30)
    prune_command = commands.add_parser("prune", help="Delete validated old unreferenced runs, only with --apply.")
    prune_command.add_argument("--days", type=int, default=30)
    prune_command.add_argument("--apply", action="store_true", required=True)
    args = parser.parse_args()
    try:
        if args.command == "coverage":
            result = coverage(args.root, start=datetime.fromisoformat(args.start), end=datetime.fromisoformat(args.end),
                              enabled_at=datetime.fromisoformat(args.enabled_at), cadence_seconds=args.cadence_seconds,
                              source_ids=set(args.source))
            if args.json:
                print(json.dumps(result))
            else:
                print(f"Input archive {'complete' if result['complete'] else 'incomplete'} for the requested window and sources.")
                print(f"Observed runs: {result['observed_runs']}; gaps: {len(result['gaps'])}.")
                if result["gaps"]:
                    print("Do not freeze this window as complete. Use --json to locate gaps; missing historical inputs cannot be inferred.")
            return 0 if result["complete"] else 1
        if args.command == "freeze":
            freeze(args.root, args.destination, set(args.run))
            print(f"Copied runs and verified dependencies to {args.destination}; this does not certify window continuity.")
        elif args.command == "prune":
            removed = prune(args.root, now=datetime.now(UTC), days=args.days)
            print(f"Deleted {len(removed)} old unreferenced raw runs. Frozen copies are outside this cleanup; deleted live files require a backup to recover.")
        else:
            candidates = retention_candidates(args.root, now=datetime.now(UTC), days=args.days)
            print(f"Retention preview: {len(candidates)} old runs unreferenced by retained runs. Nothing deleted.")
            for run in candidates:
                print(run)
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Input archive operation failed ({type(error).__name__}); no completeness claim. Check archive paths and dependencies.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
