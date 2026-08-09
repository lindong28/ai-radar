"""ICP filing number must be reachable from the site once configured.

Mainland China requires a site served from a domestic host to display its ICP
record number linked to beian.miit.gov.cn. The number is configuration, not a
constant: this repo is open source, so a fork must not inherit somebody else's
filing, and an unconfigured deployment must render no footer at all rather than
an empty shell that looks like a broken one.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from airadar.web.app import create_app

BEIAN = "沪ICP备2026017013号"
BEIAN_URL = "https://beian.miit.gov.cn/"

# Every public page carries two copies: one in the sidebar (desktop) and one at
# the end of <main> (mobile, where the sidebar is collapsed). CSS shows exactly
# one. Both copies must exist in the markup on every page -- the regulation asks
# for the number to be visible on the site, and a phone visitor who never opens
# the sidebar would otherwise see none.
#
# NOTE: these assertions only prove the markup is present. Which copy is
# *visible* at a given viewport is a CSS question and is covered by the
# browser check in tests/playwright, not here.
COVERED_PATHS = ("/", "/all", "/hot", "/wechat", "/about", "/changelog", "/bookmarks", "/more")


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    monkeypatch.setenv("AI_RADAR_ICP_BEIAN", BEIAN)
    return TestClient(create_app(tmp_path / "radar.db"))


@pytest.fixture
def unconfigured_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    monkeypatch.delenv("AI_RADAR_ICP_BEIAN", raising=False)
    return TestClient(create_app(tmp_path / "radar.db"))


@pytest.mark.parametrize("path", COVERED_PATHS)
def test_configured_beian_is_displayed_and_linked(client: TestClient, path: str) -> None:
    body = client.get(path).text
    assert BEIAN in body, f"{path} does not show the ICP number"
    assert BEIAN_URL in body, f"{path} does not link to the filing registry"
    # Both copies, so neither viewport class is left without one.
    assert body.count(BEIAN) == 2, f"{path} should carry a sidebar and a mobile copy"


@pytest.mark.parametrize("path", COVERED_PATHS)
def test_beian_link_opens_in_a_new_tab_safely(client: TestClient, path: str) -> None:
    """Leaving the site must not hand the registry control of our tab."""
    body = client.get(path).text
    anchor_start = body.index(BEIAN_URL)
    anchor = body[max(0, anchor_start - 200) : anchor_start + 400]
    assert 'target="_blank"' in anchor, anchor
    assert "noopener" in anchor, anchor


@pytest.mark.parametrize("path", COVERED_PATHS)
def test_unconfigured_renders_no_footer_at_all(
    unconfigured_client: TestClient, path: str
) -> None:
    """No configuration means no block -- not an empty one.

    An open-source fork must neither inherit this deployment's filing number nor
    show a stray empty footer region where it would have been.
    """
    body = unconfigured_client.get(path).text
    assert BEIAN not in body
    assert "beian.miit.gov.cn" not in body
    assert "icp-footer" not in body
