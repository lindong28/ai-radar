"""Shared admin-token constants for tests that call the operator surface.

Kept out of conftest.py on purpose: pytest's rootdir-based module naming means
`tests/conftest.py` and `tests/playwright/conftest.py` both import as
`conftest`, so `from conftest import ...` resolves to whichever was loaded
first. A plainly named module has no such ambiguity.
"""

from __future__ import annotations

TEST_ADMIN_TOKEN = "test-admin-token-0123456789abcdef"
TEST_ADMIN_HEADERS = {"X-Admin-Token": TEST_ADMIN_TOKEN}
