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

Known limits of the textual reference criterion (accepted, not oversights):

* An attribute value produced by a Jinja expression (`src="{{ url_for('app.js') }}"`)
  is not recognised, because the path only exists after rendering. No page uses that
  form today; one that starts to would need this script taught about it.
* A `//`-comment inside an inline `<script>` that contains an `import ... from "/app.js"`
  is still treated as a reference. Erring this way is the safe direction: it bumps a
  version string that no browser reads, rather than missing one that it does.
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


# A reference is something that makes the browser *fetch* the asset. Two forms do that
# here, and both must be covered -- app.js is reached almost entirely through the second:
#
#   <link rel="modulepreload" href="/app.js?v=...">     attribute
#   import { initAbout } from "/app.js?v=...";          ES module specifier
#
# Prose that merely names the file (a Jinja comment saying the SSR markup must match
# `web/static/app.js`) is not a fetch. The earlier bare-substring scan could not tell
# the two apart: it reported that comment as an unversioned reference, and the false
# positive was absorbed by rewording the comment rather than by fixing this criterion.
#
# The left boundary is a lookbehind rather than `\b`: `\b` matches inside `data-src=`
# (`-` is a non-word character), so it would read a `data-src` payload -- which the
# browser never fetches on its own -- as a reference needing a version string.
ATTR_REFERENCE = re.compile(
    r"""(?<![\w-])(?:src|href)\s*=\s*(?:(?P<q>["'])(?P<url>[^"']*)(?P=q)|(?P<bare>[^\s"'<>`]+))""",
    re.IGNORECASE,
)
# Covers `from "x"`, bare `import "x"`, and dynamic `import("x")`.
MODULE_SPECIFIER = re.compile(r"""(?:\bfrom|\bimport)\s*\(?\s*(?P<q>["'])(?P<url>[^"']*)(?P=q)""")
REFERENCE_PATTERNS = (ATTR_REFERENCE, MODULE_SPECIFIER)
VERSION_QUERY = re.compile(r"(\?v=)[A-Za-z0-9._-]+")
# HTML and Jinja comments are not markup the browser acts on, so a `<script src=...>`
# parked inside one is not a fetch. Masking them (below) keeps detection and rewriting
# on the same criterion: a commented-out tag is neither reported nor edited.
COMMENT = re.compile(r"<!--.*?-->|\{#.*?#\}", re.DOTALL)


def _mask_comments(text: str) -> str:
    """Blank out comment bodies, preserving length so match offsets stay valid in `text`."""
    return COMMENT.sub(lambda m: " " * len(m.group(0)), text)


def _reference_matches(text: str) -> list[re.Match[str]]:
    """Every attribute/module-specifier match outside a comment, in document order."""
    masked = _mask_comments(text)
    matches = [match for pattern in REFERENCE_PATTERNS for match in pattern.finditer(masked)]
    return sorted(matches, key=lambda m: m.start())


def _attr_url(match: re.Match[str]) -> str:
    url = match.group("url")
    return url if url is not None else match.groupdict()["bare"]


def _points_at(url: str, asset: str) -> bool:
    """True when this URL fetches `asset`, ignoring any query string or fragment.

    The match on the path is exact: EdgeOne force-caches `/app.js` and `/style.css`
    as those two paths, so a same-named file under another directory (`/vendor/app.js`)
    is a different resource under a different cache rule and must not be rewritten to
    carry this asset's digest.
    """
    path = re.split(r"[?#]", url, maxsplit=1)[0]
    if re.fullmatch(rf"\.\.?(/\.\.?)*/{re.escape(asset)}", path):
        raise SystemExit(
            f"{path!r} names {asset} through a dot-relative path, and what it actually "
            f"fetches depends on the page's own URL: under /wechat/<slug> it resolves to "
            f"/wechat/{asset}, which EdgeOne does not force-cache, while ../{asset} resolves "
            f"to the asset itself. Write the reference as /{asset} so the target is the same "
            f"on every page, or handle this asset outside this script."
        )
    return path in (asset, f"/{asset}")


def digest(asset: str) -> str:
    return hashlib.sha256((STATIC_DIR / asset).read_bytes()).hexdigest()


def expected_version(asset: str, label: str) -> str:
    """`<label>-<sha8>` -- the digest suffix is what makes a stale version impossible."""
    return f"{label}-{digest(asset)[:DIGEST_CHARS]}"


def current_label(asset: str, pins: dict[str, dict[str, str]]) -> str:
    """Reuse the human-readable part of the existing version, minus the digest."""
    recorded = pins.get(asset, {}).get("version", "")
    return recorded.rsplit("-", 1)[0] if "-" in recorded else recorded


def references(text: str, asset: str) -> list[str]:
    """Every URL in `text` that fetches `asset`, by either reference form."""
    return [url for match in _reference_matches(text) if _points_at(url := _attr_url(match), asset)]


def versions_in(text: str, asset: str) -> list[str]:
    """The `?v=` value carried by each real reference to `asset`, in document order.

    Exported so the test suite reads staleness through the same criterion this script
    rewrites by; a private regex over the raw file would disagree with it on exactly
    the cases this module goes out of its way to classify (comments, `data-src`,
    `/vendor/app.js`), and a green suite would then mean nothing.
    """
    return [
        url[found.end(1) : found.end()]
        for url in references(text, asset)
        if (found := VERSION_QUERY.search(url))
    ]


def has_unversioned_reference(text: str, asset: str) -> bool:
    """True when `text` fetches `asset` through a reference carrying no `?v=`."""
    # Requires `?v=` *plus at least one character*: a bare `?v=` looks versioned to a
    # naive check while being just as unbumpable, and it survives rewrite_text() too
    # (the `+` quantifier there needs something to replace).
    return any(not VERSION_QUERY.search(url) for url in references(text, asset))


def unversioned_references(asset: str) -> list[Path]:
    """Pages that reference the asset with no `?v=` at all.

    This cannot be auto-fixed by rewriting, and it is the worse failure: EdgeOne
    keys on the full query string (ADR-039), so a bare `/style.css` is its own
    cache entry that still gets the 7-day force-cache and has no version string to
    bump -- recovering it needs a console purge, the one branch an agent cannot do.
    """
    return [
        html for html in html_files() if has_unversioned_reference(html.read_text(encoding="utf-8"), asset)
    ]


def rewrite_text(text: str, asset: str, version: str) -> str:
    """Rewrite the `?v=` of every real reference to `asset`, leaving all else byte-identical.

    Edits are applied by span (back to front) rather than through `pattern.sub`, because
    the matches come from the comment-masked copy of `text` and their offsets are what
    ties them back to the original.
    """
    edits: list[tuple[int, int, str]] = []
    for match in _reference_matches(text):
        url = _attr_url(match)
        if not _points_at(url, asset):
            continue
        updated = VERSION_QUERY.sub(rf"\g<1>{version}", url)
        if updated == url:
            continue
        start = match.start() + match.group(0).index(url)
        edits.append((start, start + len(url), updated))

    for start, end, updated in sorted(edits, reverse=True):
        text = text[:start] + updated + text[end:]
    return text


def rewrite(asset: str, version: str, *, apply: bool) -> list[Path]:
    changed = []
    for html in html_files():
        text = html.read_text(encoding="utf-8")
        updated = rewrite_text(text, asset, version)
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
