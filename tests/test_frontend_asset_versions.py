"""Guard the `?v=` cache-busting contract for `/app.js` and `/style.css`.

EdgeOne force-caches these two exact paths at the node for 7 days (ADR-039), so a
change that ships without bumping `?v=` is deployed but not live -- and the failure
is silent: origin serves the new file while some edge nodes keep serving the old one.

Two weaker designs were tried and rejected, both because a *partial* fix left the
suite green while the tree was still broken:

* "all HTML agree on one version" alone -- when nothing is bumped they all still
  agree, which is exactly the shape of the incident this guards against.
* "all HTML agree" + "asset content matches a recorded hash" -- a reviewer
  demonstrated the bypass by hand: change the asset, update only the hash, bump
  zero HTML files, suite green. That is the lowest-effort path, and the assertion
  failure message hands you the new digest to paste.

So the version is *derived from* the asset's content (`<label>-<sha8>`) by
`scripts/bump_frontend_assets.py`. A changed asset cannot keep its version string,
and the version cannot change in the pin without changing in every HTML reference.

Known limits of the reference scan (measured, not assumed): a reference written
without a leading slash (`href="style.css"`) is not recognised as a reference at
all and slips through; prose or comments merely mentioning `/style.css` are
reported as if they were references. Both were judged acceptable -- every one of
the 20 current pages uses absolute paths, and the false-positive direction is the
safe one.

`web/templates/wechat.html` deliberately has no `style.css?v=` reference -- it
inlines the stylesheet into the SSR HTML through a tracked symlink and refreshes
under the separate contract in ADR-039 (see docs/experiences/frontend.md).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUMP_SCRIPT = PROJECT_ROOT / "scripts" / "bump_frontend_assets.py"

sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
from bump_frontend_assets import (  # noqa: E402
    ASSETS,
    DIGEST_CHARS,
    PINS_PATH,
    digest,
    html_files,
    unversioned_references,
)

FIX_HINT = "运行 `uv run python scripts/bump_frontend_assets.py` 修复。背景见 docs/experiences/frontend.md。"


def _pins() -> dict[str, dict[str, str]]:
    return json.loads(PINS_PATH.read_text(encoding="utf-8"))


def test_tree_matches_what_the_bump_script_would_produce() -> None:
    """The load-bearing assertion: every other test here is a more legible subset of it."""
    result = subprocess.run(
        [sys.executable, str(BUMP_SCRIPT), "--check"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )
    assert result.returncode == 0, (
        f"前端资源与其 ?v= 版本串不一致：\n{result.stdout}{result.stderr}\n{FIX_HINT}"
    )


@pytest.mark.parametrize("asset", ASSETS)
def test_version_is_derived_from_asset_content(asset: str) -> None:
    """This is what closes the "update the hash only" bypass."""
    version = _pins()[asset]["version"]
    expected_suffix = digest(asset)[:DIGEST_CHARS]
    assert version.endswith(f"-{expected_suffix}"), (
        f"{asset} 的版本串 {version!r} 未以其当前内容摘要 {expected_suffix!r} 结尾——"
        f"说明资源改了而版本串没跟着变。{FIX_HINT}"
    )


@pytest.mark.parametrize("asset", ASSETS)
def test_all_html_references_agree_with_the_pin(asset: str) -> None:
    # findall, not search: a page can reference the asset twice (modulepreload in
    # <head> plus the import in <body>), and a bump that updates only the first is
    # exactly the partial fix this file exists to catch.
    referenced = {html: _versions_in(html, asset) for html in html_files()}
    referenced = {html: versions for html, versions in referenced.items() if versions}
    assert referenced, f"没有任何 HTML 引用 {asset}?v=——cache-busting 契约失去了消费者"
    pinned = _pins()[asset]["version"]
    stale = {
        path: sorted(set(versions) - {pinned})
        for path, versions in referenced.items()
        if set(versions) - {pinned}
    }
    assert not stale, (
        f"{asset} 的版本串在以下 HTML 里仍是旧值（pin 为 {pinned}）："
        + ", ".join(f"{p.relative_to(PROJECT_ROOT)}={v}" for p, v in sorted(stale.items()))
        + f"。{FIX_HINT}"
    )


@pytest.mark.parametrize("asset", ASSETS)
def test_no_page_references_the_asset_without_a_version(asset: str) -> None:
    """A bare `/style.css` is worse than a stale one, and rewriting cannot fix it.

    EdgeOne keys on the full query string (ADR-039), so an unversioned reference is
    its own cache entry that still gets the 7-day force-cache with no version string
    to bump -- recovery needs a console purge, which an agent cannot perform.
    """
    bare = unversioned_references(asset)
    assert not bare, (
        f"以下 HTML 引用了 /{asset} 却没有 ?v=："
        + ", ".join(str(p.relative_to(PROJECT_ROOT)) for p in bare)
        + "。脚本改写不了它（没有版本串可替换），请手工加上。"
    )


def test_pins_cover_exactly_the_force_cached_assets() -> None:
    assert set(_pins()) == set(ASSETS), (
        "asset-pins.json 必须恰好覆盖 EdgeOne 强制缓存的那几条路径（ADR-039）；"
        "新增第三条强制缓存路径意味着 CDN 规则与本 pin 文件要同时更新。"
    )


def _versions_in(html: Path, asset: str) -> list[str]:
    return re.findall(rf"{re.escape(asset)}\?v=([A-Za-z0-9._-]+)", html.read_text(encoding="utf-8"))
