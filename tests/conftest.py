from __future__ import annotations

import pytest
from admin_auth import TEST_ADMIN_TOKEN

# The operator surface is unlocked by a shared secret (AI_RADAR_ADMIN_TOKEN).
# Every test that talks to /admin* sends admin_auth.TEST_ADMIN_HEADERS; this
# fixture makes the app expect that value so the suite never depends on the
# developer's own .env layers. Tests that need the "unconfigured" state patch
# `airadar.web.routes.admin.read_value` instead of unsetting the variable,
# because read_value falls back to ~/.claude/.env.


@pytest.fixture(autouse=True)
def _configured_admin_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_RADAR_ADMIN_TOKEN", TEST_ADMIN_TOKEN)
