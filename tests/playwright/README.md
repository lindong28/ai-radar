# ai-radar Playwright User Verify

This is the repository's single Playwright harness for user-facing checks, including the AIHOT parity journeys in `plans/20260803-aihot-visual-parity/plan.md` §L2-3.

## Run modes

Run either mode from the repository root. Both modes expose the same `base_url`, `page`, and data fixtures to test modules; tests do not branch on service ownership.

### Self-managed service

```bash
./tests/run_user_verify.sh
```

When `AI_RADAR_PLAYWRIGHT_BASE_URL` is absent, the fixture creates and migrates a fresh session-scoped SQLite database without reading the configured `AI_RADAR_DB`. It loads the current source configuration, adds a test-only WeChat source with a safe public URL, and seeds deterministic feed/X/WeChat items plus curation, daily, hot, search, media, and pagination prerequisites. It then starts `./run.sh serve` on a free local port with that database explicitly selected as pre-migrated and stops the child service after the suite. The fixture does not call X, Mp2RSS, or any other content source.

### External service

```bash
AI_RADAR_PLAYWRIGHT_BASE_URL=http://127.0.0.1:8011 ./tests/run_user_verify.sh
```

When `AI_RADAR_PLAYWRIGHT_BASE_URL` is set, its non-empty value is the service origin used by every test. A trailing slash is normalized. The service must already be running and contain the data the journeys exercise.

External mode is client-only: it does not resolve or copy `AI_RADAR_DB`, create a Playwright DB file, select or bind a port, start a service subprocess, or terminate/kill a service. The harness still launches and closes its own Chromium browser.

The runner forwards extra pytest arguments in both modes, for example:

```bash
AI_RADAR_PLAYWRIGHT_BASE_URL=http://127.0.0.1:8011 ./tests/run_user_verify.sh -q
```

Coverage by file:

| File | Spec checks |
| --- | --- |
| `test_aihot_parity_journey.py` | §L2-3 active journeys, including Phase 2B/3/4 hot, mobile-navigation, `/more`, daily, and changelog paths |
| `test_phase2.py` | V3, V4, V6, V7, V8, V9, V10, V11, V12, V14, V15, V15b |
| `test_phase3_daily.py` | V13, V13a, V13b, V13c, V13d |
| `test_phase4_about.py` | V5 about route/content |
| `test_phase5_boundaries.py` | V15a, V18, V19, V20 runtime boundaries: infinite-scroll prefetch/generation guards, search page normalization, category URL normalization, server-filter retention, and loading/error/empty states |
| `test_fixture_isolation.py` | Deterministic self-managed DB isolation and external-mode no-DB/no-service-process contract |

Public-domain checks V1/V18 production rerun and owner checks V2/V16/V17 are intentionally left for Phase 7.
