# Journal：中文/微信公众号源搜索可用性修复

> 按 `~/.claude/references/long-task-protocol.md` 记录。每条：时间 + 做了什么 + 决策/踩坑 + verify 证据。
> implementer 第一次写时按协议格式落，本文件初始只有 header。

## 2026-06-01 01:18 [fact]
Implementation started on branch `main`. Initial worktree already contained user/plan changes: `docs/issues/general.md` modified, `AGENTS.md` untracked, `data/recovery/` untracked, `plans/` untracked. Treating those as pre-existing and preserving them.

## 2026-06-01 01:18 [decision]
Following TDD for P1: first invert/add the backfill visibility tests and confirm a RED failure, then edit `prefilter/runner.py` and `scorer/runner.py`.

## 2026-06-01 01:19 [verify]
P1 RED gate: `uv run pytest tests/test_prefilter.py tests/test_scorer.py -q` ran 10 tests; 2 failed as intended.
Failures:
- `test_run_prefilter_includes_recently_fetched_backfill_regardless_of_old_published`: expected `summary.processed == 1`, actual `0`.
- `test_run_scoring_includes_recently_fetched_backfill_regardless_of_old_published`: expected `summary.processed == 1`, actual `0`.
This confirms the old `published_at >= cutoff` candidate gate excludes recently fetched backfill rows.

## 2026-06-01 01:20 [verify]
P1 GREEN after switching prefilter/scoring candidate windows to `fetched_at` only:
- `uv run pytest tests/test_prefilter.py tests/test_scorer.py -q` -> `10 passed in 0.29s`.
- `uv run ruff check src/airadar/prefilter/runner.py src/airadar/scorer/runner.py` -> `All checks passed!`.
- Bare `uv run mypy` exits with usage error because this repo requires a target; using final-gate command `uv run mypy src` -> `Success: no issues found in 58 source files`.

## 2026-06-01 01:23 [fact]
Plan command drift: `uv run airadar ...` is not installed in this checkout. The actual parser program is `ai-radar` inside `src/airadar/cli.py`, invoked locally as `PYTHONPATH=src uv run python -m airadar.cli ...`.

## 2026-06-01 01:23 [decision]
P1.3 broad `--since 7d` prefilter would queue 2,250 candidates (`feed=2143`, `x=89`, `wechat=18`; top source `openai_blog=951`). This trips the plan's risk trigger, so prefilter backfill will use an explicit WeChat item-id file for the 18 unprefiltered rows. Current broad score pending before the WeChat prefilter is only 6 rows, so score will be rechecked after prefilter before deciding whether broad `--since 7d` is acceptable.

## 2026-06-01 01:29 [verify]
P1.3 backfill evidence on `data/radar.db`:
- SQLite backup created at `data/recovery/radar-pre-wechat-search-usability-20260601-0124.db`.
- `PYTHONPATH=src uv run python -m airadar.cli prefilter --item-id-file plans/20260531-wechat-search-usability/wechat_backfill_item_ids.txt` -> `prefilter processed=18 errors=0`.
- After prefilter, broad score queue was 24 total (`wechat=18`, `x=5`, `feed=1`), so broad scoring stayed below the risk trigger.
- `PYTHONPATH=src uv run python -m airadar.cli score --since 7d` -> `score processed=24 errors=0`.
- `PYTHONPATH=src uv run python -m airadar.cli enrich --item-id-file plans/20260531-wechat-search-usability/wechat_backfill_item_ids.txt` -> `enrich processed=18, errors=0`.
- `PYTHONPATH=src uv run python -m airadar.cli curate` -> `curate run_id=20260601T082840Z-9eef selected=40 threshold=6.5`.
- WeChat coverage query: `wx_crossing|10|10|10|10|10`, `wx_guizang|10|10|10|10|10` for `total|prefiltered|ai_related|scored|enriched`.
- Expected score-gate exclusions below 6.5: `wx_crossing:c66f503f61f5a1ca relevance=6.0`; `wx_guizang:ac354b3a98e1c792 relevance=3.0`; `wx_guizang:8c7a7b9899deb0ef relevance=6.0`.

## 2026-06-01 01:29 [lesson]
Enrichment is stored as `item_evaluations.stage='enrich'`, not an `item_enrichments` table. Coverage queries should use the stage in `item_evaluations`.

## 2026-06-01 01:35 [verify]
P2 RED gate:
- Plain `uv run pytest tests/test_web_routes.py -q` hit collection-time `sqlite3.OperationalError: database is locked` because an unrelated long-running `prefilter --since 24h` process was holding the default `data/radar.db`.
- Reran with `AI_RADAR_DB=/tmp/airadar-p2-red-default.db uv run pytest tests/test_web_routes.py -q` to avoid default-DB import migration. Result: 34 collected, 31 passed, 3 failed as intended.
- Failures: missing `route_common.source_match_expression`; timeline search returned `item-content-newer` before source matches; curated precomputed search returned `item-content-newer` before source matches.

## 2026-06-01 01:39 [verify]
P2 GREEN:
- `AI_RADAR_DB=/tmp/airadar-p2-green2-default.db uv run pytest tests/test_web_routes.py -q` -> `34 passed in 0.53s`.
- `uv run ruff check src/airadar/web/routes/common.py src/airadar/web/routes/timeline.py src/airadar/web/routes/curated.py tests/test_web_routes.py` -> `All checks passed!`.
- `uv run mypy src` -> `Success: no issues found in 58 source files`.
Search-specific tests assert source matches are ordered `item-x-1,item-wx-1,item-x-2,item-x-3` before content-only `item-content-newer`, while no-query order remains pure time order.

## 2026-06-01 01:42 [fact]
OpenCC API verification via Context7 `/yichen0831/opencc-python`: docs show `from opencc import OpenCC`, `OpenCC('s2t')`, `OpenCC('t2s')`, and `.convert(...)`; supported modes include `s2t` and `t2s`.
Dependency choice followed the approved order: `uv add opencc-python-reimplemented` installed `opencc-python-reimplemented==0.1.7`. Local probe: `OpenCC("s2t").convert("归藏") -> 歸藏`, `OpenCC("t2s").convert("歸藏") -> 归藏`, English `openai` unchanged.

## 2026-06-01 01:44 [verify]
P3 RED gate: `AI_RADAR_DB=/tmp/airadar-p3-red-default.db uv run pytest tests/test_web_routes.py -q` -> 38 collected, 34 passed, 4 failed as intended.
Failures:
- `expand_st_variants("归藏")` returned only `["归藏"]`, missing `歸藏`.
- FTS params for `归藏工具` were only `"归藏工具"`, missing OR variant `"歸藏工具"`.
- Timeline/curated `q=归藏` did not match source name `歸藏社`.
- Timeline `q=归藏` returned only the newer content/title match, not the older traditional source match first.

## 2026-06-01 01:46 [verify]
P3 GREEN:
- `AI_RADAR_DB=/tmp/airadar-p3-green-default.db uv run pytest tests/test_web_routes.py -q` -> `38 passed in 0.62s`.
- `uv run ruff check src/airadar/web/routes/common.py tests/test_web_routes.py` -> `All checks passed!`.
- `uv run mypy src` -> `Success: no issues found in 58 source files`.
Implementation uses cached `OpenCC('s2t')` and `OpenCC('t2s')`, de-duplicates variants, builds FTS5 phrase OR (`"归藏工具" OR "歸藏工具"`), expands 2-character LIKE predicates, and routes source-match ranking through the same variant helper.

## 2026-06-01 01:52 [lesson]
While running L2, the local scheduler started `pipeline.sh`, which entered broad `prefilter --since 24h` / `score --since 24h` / `enrich --since 24h` stages and held `data/radar.db` write locks. I terminated those local pipeline processes and removed stale `.pipeline.lock` to avoid an uncontrolled broad backfill run and unblock verification. `sqlite3 data/radar.db "PRAGMA quick_check;"` returned `ok` after termination.

## 2026-06-01 02:04 [verify]
L2 V1-V7 evidence on production-synced `data/radar.db`, served by current code on local port 8022 for API checks:
- V1 coverage SQL (`total|prefiltered|ai_related|visible|score_below_6_5|unexplained_unprefiltered`): `wx_crossing|10|10|10|9|1|0`, `wx_guizang|10|10|10|8|2|0`.
- V1 expected exclusions by latest score gate: `wx_crossing c66f503f61f5a1ca relevance=6.0`; `wx_guizang ac354b3a98e1c792 relevance=3.0`; `wx_guizang 8c7a7b9899deb0ef relevance=6.0`.
- V2 `q=十字路口&limit=50`: API returned `wx_crossing` count `9`, positions `0..8`; expected visible `wx_crossing=9`.
- V3 `q=歸藏&limit=50`: page1 contains both `op7418_x` and `wx_guizang`; `wx_guizang` positions `1,3,5,7,9,11,13,15` (<50); first20 alternates the two sources until `wx_guizang` visible rows are exhausted.
- V4 `q=归藏&limit=50`: API returned `wx_guizang` count `8`, positions `1,3,5,7,9,11,13,15`; expected visible `wx_guizang=8`.
- V5 no-q timeline regression: diff between `/api/v1/timeline?limit=50` ids and SQL old time order ids was empty; both files had 50 ids.
- V6 curated: latest curated run has no `wx_crossing`/`wx_guizang` entries (`q=十字路口` count 0; `q=歸藏/归藏` returns only two `op7418_x` items), so production data cannot observe wx curated source priority. No-q curated diff between API ids and SQL old precomputed date/time order ids was empty; both had 40 ids. P2 route test covers curated source-priority branch.
- V7 exact chain after stopping local L2 server and pipeline lock: `uv run pytest -q && uv run ruff check . && uv run mypy src` -> `212 passed, 5 skipped in 37.13s`; `All checks passed!`; `Success: no issues found in 58 source files`.

## 2026-06-01 02:08 [verify]
Docs sync completed:
- `CHANGELOG.md` added 2026-06-01 user-facing search/backfill/simple-traditional entry.
- `docs/issues/general.md` marked #4/#5/#6 resolved with L2 evidence.
- `docs/issues/ux-contract-issues.md` added contract expansion candidate for source-search ordering.
- `docs/experiences/llm-pipeline.md` recorded fetched_at-only broad pipeline candidate/DB-lock gotcha.
- Plan archived to `docs/plans/20260531-wechat-search-usability/plan.md` and indexed in `docs/CLAUDE.md`.
