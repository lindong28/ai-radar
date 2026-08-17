#!/usr/bin/env python3
"""Bump the `?v=` cache-busting version of `/app.js` and `/style.css`.

EdgeOne force-caches these two exact paths at the node for 7 days (ADR-039), so a
change that ships without a new `?v=` is deployed but not live. This script makes
that mechanical: it derives each version from the asset's own content hash, so a
changed asset cannot keep its old version string, and rewrites every HTML reference
in one pass.

    uv run python scripts/bump_frontend_assets.py            # rewrite in place
    uv run python scripts/bump_frontend_assets.py --check    # report drift, change nothing

The `--check` mode is what `tests/test_frontend_asset_versions.py` asserts, so a
green test run means the tree already matches what this script would produce.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "web" / "static"
PINS_PATH = ROOT / "web" / "asset-pins.json"
ASSETS = ("app.js", "style.css")
DIGEST_CHARS = 8


def html_files() -> list[Path]:
    """Every page that may reference a versioned asset.

    Jinja partials (`_`-prefixed) are included by a parent template and carry no
    asset references of their own; they are harmless to scan and cheap to keep in
    so a future partial that does gain one is not silently missed.
    """
    return sorted((ROOT / "web" / "templates").glob("*.html")) + sorted(STATIC_DIR.glob("*.html"))


def digest(asset: str) -> str:
    return hashlib.sha256((STATIC_DIR / asset).read_bytes()).hexdigest()


def expected_version(asset: str, label: str) -> str:
    """`<label>-<sha8>` -- the digest suffix is what makes a stale version impossible."""
    return f"{label}-{digest(asset)[:DIGEST_CHARS]}"


def current_label(asset: str, pins: dict[str, dict[str, str]]) -> str:
    """Reuse the human-readable part of the existing version, minus the digest."""
    recorded = pins.get(asset, {}).get("version", "")
    return recorded.rsplit("-", 1)[0] if "-" in recorded else recorded


def unversioned_references(asset: str) -> list[Path]:
    """Pages that reference the asset with no `?v=` at all.

    This cannot be auto-fixed by rewriting, and it is the worse failure: EdgeOne
    keys on the full query string (ADR-039), so a bare `/style.css` is its own
    cache entry that still gets the 7-day force-cache and has no version string to
    bump -- recovering it needs a console purge, the one branch an agent cannot do.
    """
    # Requires `?v=` *plus at least one character*: a bare `?v=` looks versioned to a
    # naive lookahead while being just as unbumpable, and it survives rewrite() too
    # (the `+` quantifier there needs something to replace).
    pattern = re.compile(rf"/{re.escape(asset)}(?!\?v=[A-Za-z0-9._-])")
    return [html for html in html_files() if pattern.search(html.read_text(encoding="utf-8"))]


def rewrite(asset: str, version: str, *, apply: bool) -> list[Path]:
    pattern = re.compile(rf"({re.escape(asset)}\?v=)[A-Za-z0-9._-]+")
    changed = []
    for html in html_files():
        text = html.read_text(encoding="utf-8")
        updated = pattern.sub(rf"\g<1>{version}", text)
        if updated != text:
            changed.append(html)
            if apply:
                html.write_text(updated, encoding="utf-8")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="report drift without writing")
    parser.add_argument(
        "--label",
        help="human-readable part of the new version, e.g. 20260817-aihot (default: reuse the current one)",
    )
    args = parser.parse_args()

    pins = json.loads(PINS_PATH.read_text(encoding="utf-8")) if PINS_PATH.exists() else {}
    drifted = False
    unfixable = False

    for asset in ASSETS:
        label = args.label or current_label(asset, pins)
        if not label:
            print(f"error: {asset} has no recorded label; pass --label YYYYMMDD-<tag>", file=sys.stderr)
            return 2

        bare = unversioned_references(asset)
        if bare:
            unfixable = True
            sys.stdout.flush()
            print(
                f"{asset}: 以下 HTML 引用了它却没有 ?v=，无法自动修复，请手工加上版本串："
                + ", ".join(str(p.relative_to(ROOT)) for p in bare),
                file=sys.stderr,
            )

        version = expected_version(asset, label)
        changed = rewrite(asset, version, apply=not args.check)
        pin_stale = pins.get(asset, {}) != {"sha256": digest(asset), "version": version}
        if changed or pin_stale:
            drifted = True
            verb = "would update" if args.check else "updated"
            print(f"{asset} -> {version} ({verb} {len(changed)} HTML file(s), pin {'stale' if pin_stale else 'ok'})")
        else:
            print(f"{asset} -> {version} (already current)")
        pins[asset] = {"sha256": digest(asset), "version": version}

    if not args.check:
        PINS_PATH.write_text(json.dumps(pins, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if args.check and drifted and not unfixable:
        sys.stdout.flush()
        print(
            "\nfrontend assets changed without a matching ?v= bump. "
            "Run: uv run python scripts/bump_frontend_assets.py",
            file=sys.stderr,
        )
        return 1
    if unfixable:
        # Fails in both modes: rewriting cannot add a `?v=` that was never there.
        # Everything fixable has still been fixed above, so the tree is self-consistent
        # apart from the references named on stderr.
        sys.stdout.flush()
        print("\n失败：存在无法自动修复的引用（见上），补上 ?v= 后重跑。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
