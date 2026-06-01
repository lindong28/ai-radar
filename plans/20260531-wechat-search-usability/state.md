# State：中文/微信公众号源搜索可用性修复

> 真值文件。implementer 每步开工标 [in_progress]、收尾标 [done] 并附 verify 证据指针（journal 锚点 / commit）。
> 协议：`~/.claude/references/long-task-protocol.md`。

## 进度

### Phase 1 — #4 backfill 可见性（前置）
- [done] P1.1 prefilter+score runner 候选改 fetched_at-only
  - Goal：`prefilter/runner.py:87-88` 与 `scorer/runner.py:74,91-92` 去掉 `published_at >= cutoff`
  - Verify：2026-06-01 01:20 journal P1 GREEN：targeted pytest 10 passed；ruff passed；`uv run mypy src` passed
- [done] P1.2 反转/新增 prefilter+score 测试
  - Goal：改写 `test_prefilter.py:107` 编码 bug 的测试为断言 backfill item 被处理；查 test_scorer 类比项
  - Verify：2026-06-01 01:19 journal RED + 01:20 GREEN；新增 scorer 同款 backfill test
- [done] P1.3 跑 backfill 并核查覆盖率
  - Goal：prefilter→score→enrich→curate 处理 18 篇存量
  - Verify：2026-06-01 01:29 journal：prefilter 18/0, score 24/0, enrich 18/0, curate `20260601T082840Z-9eef`; both WeChat sources now 10/10 prefiltered/scored/enriched; sub-6.5 exclusions listed

### Phase 2 — #6 来源匹配优先 + 多样性（依赖 P1）
- [done] P2.1 common.py 增 source_match helper（LIKE 转义复用）
  - Verify：2026-06-01 01:39 journal P2 GREEN；`test_source_match_expression_reuses_like_escape`
- [done] P2.2 timeline.py 搜索态排序：is_source_match + 来源轮转 window
  - Verify：2026-06-01 01:39 journal P2 GREEN；timeline source-match route test passed
- [done] P2.3 curated.py 搜索态同款排序
  - Verify：2026-06-01 01:39 journal P2 GREEN；curated precomputed source-match route test passed
- [done] P2.4 web 路由测试：首屏含同名两源 + 来源优先 + 无 q 回归
  - Verify：2026-06-01 01:39 journal P2 GREEN；route test asserts search order and no-q order

### Phase 3 — #5 简繁归一化（query 层 + opencc）
- [done] P3.1 context7 查 opencc → 选 pure-python opencc 加入 deps
  - Verify：2026-06-01 01:42 journal：Context7 API docs checked; `uv add opencc-python-reimplemented`; import/API probe passed
- [done] P3.2 common.py expand_st_variants + 模块级 converter 缓存
  - Verify：2026-06-01 01:46 journal P3 GREEN；单测 归藏↔歸藏、openai unchanged
- [done] P3.3 search_id_subquery 接入变体（FTS OR + LIKE OR）
  - Verify：2026-06-01 01:46 journal P3 GREEN；FTS OR and 2-char LIKE expansion tests passed
- [done] P3.4 #6 source_match 复用变体集合（交汇点）
  - Verify：2026-06-01 01:46 journal P3 GREEN；simplified query ranks traditional source match before newer title/content match
- [done] P3.5 单测 + 路由测试（简体命中繁体源）
  - Verify：2026-06-01 01:46 journal P3 GREEN；timeline and curated q=归藏 match 歸藏 source

### 交付
- [done] D1 L2 端到端 V1–V7 全过（expected-vs-actual，零 unexplained 缺席）
  - Verify：2026-06-01 02:04 journal L2 V1-V7：V1/V2/V4 expected-vs-actual match; V3 source diversity pass; V5/V6 no-q diffs empty; V7 exact chain pass
- [done] D2 文档同步（CHANGELOG / issues #4#6 resolved；#5 若 deferred 则落 follow-up issue / ux-contract 演化候选）
  - Verify：CHANGELOG 2026-06-01 entry；docs/issues/general.md #4/#5/#6 resolved；ux-contract issue added；llm-pipeline experience added；plan archived under docs/plans
- [done] D3 commit
  - Verify：Final implementation/docs state included in git commits on `main`; see final response for latest commit hash

## Fallback（用户 2026-06-01 预排）
- opencc 两变体（pure-python → C++）都装不上 → 仅发 #4+#6，#5 转 docs/issues/general.md follow-up issue，V4 标 N/A，交付说明注明 deferred + 原因。

## Open Issues
（空）
