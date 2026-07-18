# ai-radar Playwright User Verify

This suite maps local user-facing checks from `plans/ai-radar-aihot-parity-20260511/plan.md` to executable browser tests.

Run from the repository root:

```bash
./tests/run_user_verify.sh
```

The fixture in `conftest.py` creates a session-scoped SQLite backup of the configured `AI_RADAR_DB`, migrates only that temporary copy, starts `./run.sh serve` on a free local port with the copy explicitly selected as pre-migrated, and opens Chromium. The source database supplies meaningful local data but is never served or migrated by the suite.

Coverage by file:

| File | Spec checks |
| --- | --- |
| `test_phase2.py` | V3, V4, V6, V7, V8, V9, V10, V11, V12, V14, V15, V15b |
| `test_phase3_daily.py` | V13, V13a, V13b, V13c, V13d |
| `test_phase4_about.py` | V5 about route/content |
| `test_phase5_boundaries.py` | V15a, V18, V19, V20 DOM check |

Public-domain checks V1/V18 production rerun and owner checks V2/V16/V17 are intentionally left for Phase 7.
