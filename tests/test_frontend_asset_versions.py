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

What counts as a reference: a `src=`/`href=` attribute, or an ES module specifier,
whose path component is exactly the asset and which is not sitting inside a comment.
That is what the browser actually fetches, so it is what needs a `?v=`. Every test
here reads references through the script's own helpers rather than a local regex --
a private pattern would drift from the criterion the script rewrites by, and the
suite would then be green about a tree the script disagrees with.
Both directions of the old bare-substring scan were wrong and are now pinned by
`test_prose_mentioning_the_asset_is_not_a_reference` and its positive twin: a
comment naming `web/static/app.js` used to be reported as an unversioned
reference (it fired on a real Jinja partial and got "fixed" by rewording the
comment), while a relative `href="style.css"` used to slip through unseen.

`web/templates/wechat.html` deliberately has no `style.css?v=` reference -- it
inlines the stylesheet into the SSR HTML through a tracked symlink and refreshes
under the separate contract in ADR-039 (see docs/experiences/frontend.md).
"""

from __future__ import annotations

import json
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
    has_unversioned_reference,
    html_files,
    rewrite_text,
    unversioned_references,
    versions_in,
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
    referenced = {html: versions_in(html.read_text(encoding="utf-8"), asset) for html in html_files()}
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


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param(
            "{# SSR 对偶：这里的 DOM 必须与 web/static/app.js 的 renderHotTopics() 逐节点同形。 #}",
            id="jinja-comment",
        ),
        pytest.param("<!-- bump app.js then rerun the script -->", id="html-comment"),
        pytest.param("<p>编辑 web/static/app.js 之后要 bump 版本串</p>", id="body-prose"),
    ],
)
def test_prose_mentioning_the_asset_is_not_a_reference(markup: str) -> None:
    """The negative half of the contrast: naming the file is not fetching it.

    The bare-substring scan this replaced flagged a real Jinja partial, and the
    false positive was absorbed by rewording the comment -- which leaves the next
    comment to trip over it again.
    """
    assert not has_unversioned_reference(markup, "app.js")


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param('<script src="/app.js"></script>', id="absolute-src"),
        pytest.param('<script src="app.js"></script>', id="relative-src"),
        pytest.param("<script src=/app.js></script>", id="unquoted-src"),
        pytest.param('<link href="/app.js" rel="modulepreload">', id="href"),
        pytest.param('<script SRC="/app.js"></script>', id="uppercase-attr"),
        pytest.param('<script src="/app.js?v="></script>', id="empty-version"),
        # The form app.js is actually reached by on every page -- narrowing the
        # criterion to attributes alone would have made all 14 of them invisible.
        pytest.param('import { initAbout } from "/app.js";', id="module-import"),
        pytest.param('import "/app.js";', id="side-effect-import"),
        pytest.param('const m = await import("/app.js");', id="dynamic-import"),
    ],
)
def test_a_real_reference_without_a_version_is_reported(markup: str) -> None:
    """The positive half: narrowing the criterion must not blind it to the real thing."""
    assert has_unversioned_reference(markup, "app.js")


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param('<script src="/app.js?v=20260817-aihot-1bf076b8"></script>', id="attr"),
        pytest.param('import { initAbout } from "/app.js?v=20260817-aihot-1bf076b8";', id="import"),
    ],
)
def test_a_versioned_reference_is_not_reported(markup: str) -> None:
    assert not has_unversioned_reference(markup, "app.js")


def test_both_reference_forms_are_rewritten() -> None:
    """app.js is fetched by `import ... from`, style.css by `href=`; a bump needs both."""
    text = '<link rel="modulepreload" href="/app.js?v=old">\nimport { init } from "/app.js?v=old";'
    assert rewrite_text(text, "app.js", "new") == (
        '<link rel="modulepreload" href="/app.js?v=new">\nimport { init } from "/app.js?v=new";'
    )


def test_rewrite_updates_attributes_and_leaves_prose_alone() -> None:
    """Rewriting is scoped to the same criterion, so a comment cannot be edited by it."""
    text = '{# 见 web/static/app.js?v=old #}\n<script src="/app.js?v=old"></script>'
    assert rewrite_text(text, "app.js", "new") == '{# 见 web/static/app.js?v=old #}\n<script src="/app.js?v=new"></script>'


def test_pins_cover_exactly_the_force_cached_assets() -> None:
    assert set(_pins()) == set(ASSETS), (
        "asset-pins.json 必须恰好覆盖 EdgeOne 强制缓存的那几条路径（ADR-039）；"
        "新增第三条强制缓存路径意味着 CDN 规则与本 pin 文件要同时更新。"
    )


@pytest.mark.parametrize(
    ("markup", "is_reference"),
    [
        # Commented-out markup: the browser fetches nothing, so there is nothing to bump.
        # The positive twin below is the same tag with the comment delimiters removed.
        pytest.param('<!-- <script src="/app.js"></script> -->', False, id="html-comment-full-tag"),
        pytest.param('{# <script src="/app.js"></script> #}', False, id="jinja-comment-full-tag"),
        pytest.param('<script src="/app.js"></script>', True, id="uncommented-tag"),
        # data-src is a payload some script may or may not act on, not a fetch the
        # browser performs; `\b` would have matched inside it, a lookbehind does not.
        pytest.param('<div data-src="/app.js"></div>', False, id="data-src"),
        pytest.param('<div data-thing="x" src="/app.js"></div>', True, id="plain-src-after-dash-attr"),
        # A fragment is not part of the path, so the reference is real and unversioned.
        pytest.param('<script src="/app.js#main"></script>', True, id="fragment-unversioned"),
        # Same-named file elsewhere: a different resource under a different cache rule.
        pytest.param('<script src="/vendor/app.js"></script>', False, id="vendor-subdir"),
        pytest.param('import { x } from "/static/vendor/app.js";', False, id="vendor-subdir-import"),
    ],
)
def test_reference_criterion_edges(markup: str, is_reference: bool) -> None:
    """Each mutation paired with its twin: what separates them is only the thing under test."""
    assert has_unversioned_reference(markup, "app.js") is is_reference


def test_a_versioned_reference_with_a_fragment_is_not_reported() -> None:
    """`?v=` before `#frag`: stripping only the query would leave `.js#main` unmatched."""
    assert not has_unversioned_reference('<script src="/app.js?v=20260817-aihot-1bf076b8#main"></script>', "app.js")


@pytest.mark.parametrize(
    "markup",
    [
        pytest.param('<script src="./app.js"></script>', id="dot-relative"),
        pytest.param('<script src="../app.js"></script>', id="parent-relative"),
        pytest.param('import "./app.js";', id="dot-relative-import"),
        pytest.param('<script src="././app.js"></script>', id="repeated-dot-segment"),
        pytest.param('<script src=".././app.js"></script>', id="mixed-dot-segments"),
    ],
)
def test_a_dot_relative_reference_stops_the_run(markup: str) -> None:
    """What `./app.js` fetches depends on the page's own URL -- under /wechat/<slug> it is
    /wechat/app.js, not the force-cached /app.js, while ../app.js is the asset itself. The
    script cannot tell from the markup alone, so it refuses rather than guessing either way."""
    with pytest.raises(SystemExit, match="dot-relative"):
        has_unversioned_reference(markup, "app.js")


def test_rewrite_leaves_commented_out_markup_and_other_directories_alone() -> None:
    """Rewriting shares the detection criterion, so what is not a reference is not edited."""
    text = (
        '<!-- <script src="/app.js?v=old"></script> -->\n'
        '<script src="/vendor/app.js?v=old"></script>\n'
        '<script src="/app.js?v=old"></script>'
    )
    assert rewrite_text(text, "app.js", "new") == (
        '<!-- <script src="/app.js?v=old"></script> -->\n'
        '<script src="/vendor/app.js?v=old"></script>\n'
        '<script src="/app.js?v=new"></script>'
    )


def test_versions_in_reads_only_real_references() -> None:
    """The helper the staleness test uses -- same criterion, so it cannot disagree with it."""
    text = (
        '<!-- <script src="/app.js?v=commented"></script> -->\n'
        '<div data-src="/app.js?v=payload"></div>\n'
        '<script src="/vendor/app.js?v=vendor"></script>\n'
        '<link rel="modulepreload" href="/app.js?v=real-a">\n'
        'import { init } from "/app.js?v=real-b";'
    )
    assert versions_in(text, "app.js") == ["real-a", "real-b"]
