"""Read historical locations without rewriting frozen provenance or hashes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def relocation_map() -> dict:
    return json.loads((ROOT / "evals/asset-relocations.json").read_text())


def resolve_asset_path(path: Path, *, root: Path = ROOT) -> Path:
    """Resolve exact path-component prefixes; never guess from basename/version.

    Existing paths win, allowing old pinned worktrees and pre-migration reads.
    Only readers use this helper: new writes must target canonical locations.
    """
    path, root = Path(path).expanduser(), Path(root).resolve()
    if not path.is_absolute():
        path = root / path
    if path.exists():
        return path.resolve()
    mapping = relocation_map()
    pairs = [(Path(a).expanduser(), Path(b).expanduser()) for a, b in mapping["external"].items()]
    for base in (root, *(Path(p).expanduser() for p in mapping["project_aliases"])):
        pairs.extend((base / a, root / b) for a, b in mapping["project"].items())
    for old, new in sorted(pairs, key=lambda pair: len(pair[0].parts), reverse=True):
        if path.is_relative_to(old):
            return (new / path.relative_to(old)).resolve()
    return path.resolve()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    resolved = resolve_asset_path(args.path, root=args.root)
    print(resolved)
    if not resolved.exists():
        parser.exit(1, "路径已解析，但文件尚未迁移或不可用。\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
