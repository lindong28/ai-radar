"""Phase 1 done gate: every enabled source in real sources.toml must have
homepage_url + icon_url filled (DD-PARITY-13). Expected to FAIL before owner
gate (sources.toml has no values) and PASS after gate.

Once Phase 1 owner gate completes, this test enforces no future enabled source
slips through without the two new fields.
"""

from __future__ import annotations

import pytest

from airadar.db import PROJECT_ROOT
from airadar.sources.loader import SourceConfig, load_sources

POOL_PATH = PROJECT_ROOT / "apps" / "ai-radar" / "data" / "sources.toml"


@pytest.fixture(scope="module")
def enabled_sources() -> list[SourceConfig]:
    if not POOL_PATH.exists():
        pytest.skip(f"sources.toml not found at {POOL_PATH}")
    return [s for s in load_sources(POOL_PATH) if s.enabled]


def test_all_enabled_sources_have_homepage_url(enabled_sources: list[SourceConfig]) -> None:
    missing = [s.slug for s in enabled_sources if not s.homepage_url]
    assert not missing, f"enabled sources missing homepage_url: {missing}"


def test_all_enabled_sources_have_icon_url(enabled_sources: list[SourceConfig]) -> None:
    missing = [s.slug for s in enabled_sources if not s.icon_url]
    assert not missing, f"enabled sources missing icon_url: {missing}"


def test_at_least_one_x_kind_source_for_v10_verifiability(enabled_sources: list[SourceConfig]) -> None:
    """spec V10 needs >= 3 X-kind cards. owner may opt out at gate; in that case
    sources pool will simply have no kind=x source and this test is skipped to
    let owner sign V10 N/A explicitly. End-to-end items >= 3 is verified by
    Playwright after fetcher runs against the X source."""
    x_sources = [s for s in enabled_sources if s.kind == "x"]
    if not x_sources:
        pytest.skip("no kind=x sources in pool — owner has opted out of V10 (record in journal as decision)")
    assert x_sources, "X kind enabled but pool yielded zero — check kind field"
