#!/usr/bin/env python3
"""Move the registered local evaluation archives without rewriting payloads."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals._shared.assets import ROOT, rebuild_index, utc_now, write_json
from evals._shared.relocations import relocation_map


def tree_identity(directory: Path) -> dict[str, str]:
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError(f"expected a real source directory: {directory}")
    paths = sorted(p for p in directory.rglob("*") if p.is_file() or p.is_symlink())

    def identify(path):
        value = ("symlink:" + str(path.readlink()) if path.is_symlink() else
                 "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest())
        return str(path.relative_to(directory)), value

    # Independent reads only; bounded to avoid competing with live collectors.
    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(pool.map(identify, paths))


def migration_entries(mapping: dict) -> list[tuple[str, str]]:
    # The submodule is moved by Git, external datasets were already archived.
    return [(a, b) for a, b in mapping["project"].items()
            if a.startswith(("runs/", "experiments/"))]


def migrate(root: Path, *, apply: bool = False, mapping: dict | None = None) -> dict:
    root = root.resolve()
    entries = migration_entries(mapping or relocation_map())
    receipt_path = root / "data/evaluation-archive/layout-migration.json"
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text())
        if [(r["from"], r["to"]) for r in receipt["moves"]] != entries:
            raise ValueError("migration map differs from saved receipt")
    else:
        moves = []
        for old, new in entries:
            src, dst = root / old, root / new
            if dst.exists():
                raise FileExistsError(f"unregistered destination already exists: {dst}")
            moves.append({"from": old, "to": new, "files": tree_identity(src)})
        receipt = {"kind": "asset-migration", "recorded_at": utc_now(), "moves": moves,
                   "deleted_files": 0, "model_calls": 0}

    # Check every entry before any rename, including resumed migrations.
    for entry in receipt["moves"]:
        src, dst = root / entry["from"], root / entry["to"]
        if src.exists() == dst.exists():
            raise ValueError(f"need exactly one source/destination: {src} -> {dst}")
        if tree_identity(src if src.exists() else dst) != entry["files"]:
            raise ValueError(f"payload differs from migration receipt: {src}")

    if not apply:
        return {"mode": "dry-run", "moves": len(entries),
                "files": sum(len(e["files"]) for e in receipt["moves"]), "deleted_files": 0}
    if not receipt_path.exists():
        write_json(receipt_path, receipt)
    for entry in receipt["moves"]:
        src, dst = root / entry["from"], root / entry["to"]
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.rename(dst)
        if tree_identity(dst) != entry["files"]:
            raise ValueError(f"post-move payload mismatch: {dst}")
        parent = src.parent
        while parent not in {root / "runs", root / "experiments", root}:
            if not parent.exists() or any(parent.iterdir()):
                break
            parent.rmdir()
            parent = parent.parent

    containers = {}
    for entry in receipt["moves"]:
        if "/support/" in entry["to"]:
            container = entry["to"].split("/support/", 1)[0]
            containers.setdefault(container, []).append(entry["from"])
    for container, origins in containers.items():
        _, target, benchmark, version, day, clock = Path(container).parts
        metadata = root / "experiments" / Path(container).relative_to("runs") / "metadata.json"
        expected = {"kind": "asset-migration", "target": target, "benchmark": benchmark,
                    "version": version, "source_paths": origins,
                    "directory_timestamp_utc": f"{day}/{clock}",
                    "directory_time_source": "migration_plan_created",
                    "recorded_at": receipt["recorded_at"], "original_run_time": None,
                    "migration_receipt": str(receipt_path.relative_to(root)), "new_api_attempts": 0}
        if metadata.exists():
            if json.loads(metadata.read_text()) != expected:
                raise ValueError(f"support container metadata collision: {metadata}")
        else:
            write_json(metadata, expected)
    rows = rebuild_index(root)
    return {"mode": "applied", "moves": len(entries),
            "files": sum(len(e["files"]) for e in receipt["moves"]),
            "metric_rows": len(rows), "deleted_files": 0, "receipt": str(receipt_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--apply", action="store_true", help="默认只核对；显式执行原位迁移")
    args = parser.parse_args()
    try:
        result = migrate(args.root, apply=args.apply)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"迁移未完成：{exc}\n已有收据和已迁文件保留；修复后同命令续跑。\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
