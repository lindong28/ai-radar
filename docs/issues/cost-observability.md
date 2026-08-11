# Issues — LLM 成本观测

来源：plan `20260810-llm-cost-observability` P1 的执行与 review gate。P1 已交付「查询时派生成本 + cost-audit 对账 + 最小 admin 视图」，下列各条是**已知未闭合项**，不随 P1 交付而消失。

## ISSUE-004 · ARK 挂牌价来源非权威，而它占已知成本的 87.6%

**状态**：open · **优先级**：high

`src/airadar/pricing.py` 的 ARK supplement（`deepseek-v4-pro-260425` 等三型号）单价来自火山引擎**开发者社区的一篇个人文章**，不是官方计费页。它支持 flash `¥0.02/¥1/¥2` 与 pro `¥12/¥24`，但**无法权威证明** pro 的 cache 价 `¥0.1`、带日期后缀的型号 ID、以及用户实际的套餐/配额计费语义。

实测影响：最近 30 天窗口 `nominal_share ≈ 0.876`——即报表上约 87.6% 的金额建立在这批未权威核实的挂牌价上。因此 `cost-audit` 的结论被刻意限定为「对已加载 catalog 的**计算一致性**」，并在输出里明写 `tariff authority is not verified`。

**闭合方式**：plan 的 T1——去方舟控制台核实实际计费语义与实付单价。按量计费 → 用实价替换并把状态从 `nominal` 改为 `priced`；包量计费 → 保留 `nominal` 并在周报明示。在此之前**不得据 nominal 数字做 ARK vs 官方直连的路由决策**。

## ISSUE-005 · 三条状态路径在真实数据中从未出现，仅由合成 fixture 覆盖

**状态**：open · **优先级**：medium

真实生产数据从未产生过 `stale`、`due-review`、`unpriced` 三种阳性状态（当前窗口 `unpriced=[]`、`pricing_freshness=['fresh']`）。因此这三条路径在 SQL / API / HTML / CLI 上的行为**只被合成 fixture 验证过**。同类还有：fuzzy / 未知 ARK 后缀的页面警示路径、非零 cache-read token、SQLite 计量失败时的 paid-result 保留路径、以及 raw-catalog 费率负控。

这不是缺陷，是**证据边界**。记录它的原因是：真实计数为零不等于阳性链路已接地，而两者在读数上不可区分。下次这些状态真的出现时（上游刷新失败、ARK 上新型号、P3 打开 cache 采集），应把首次真实出现当作一次验证机会而不是当作故障。

## ISSUE-006 · db-sync 的异常 base-copy 路径 fail-open

**状态**：open · **优先级**：high

`deploy/sync/logical_delta.py::_apply_delta` 在检出 schema 不等（`ReplicaInvalid`）后自愈为 `_replace_with_base_copy()` 整库替换。该路径**只打一行 stderr，sync 仍报成功**——于是一次 1GB 量级的异常传输与一次 16MB 的稳态轮在退出码上不可区分。

**2026-08-11 实测收口**：11:41 那一轮由 supervisor 观察全程。日志确认 `[replica] !!! SELF-HEAL: non-FTS schema differs from snapshot; rebuilding the base-only shipping replica`；主库传输 `Total file size: 1.68G / Total bytes sent: 1.26G / speedup 1.32`，即**实传 1.26 GB**（稳态轮 16–34 MB），11:41:01 起、12:15:51 止，耗时 34 分钟。**最终结局是 `sync OK`** —— 该轮在退出状态上与一次健康轮完全不可区分，fail-open 由代码推断升级为实测事实。自愈前的逐表核对全部 `match=1`，故触发原因确实只是 schema 不等、数据本身一致。

本次触发源：migration 016 为把 `item_evaluations.cost_usd` 改可空而整表重写（实测 388.8 MB / 93,499 行 / 99,532 页）。016/017 已把这次一次性豁免写进头注释与 ADR-014/016，但**该 fail-open 行为本身未修**，属 db-sync 的范围。

**闭合方式**：让异常 base-copy 至少在退出码或告警上与稳态轮可区分。

## ISSUE-007 · `verify_admin_metrics.py` 报出 3 项 P95 口径差异

**状态**：open · **优先级**：low

修掉该脚本对已退役成本列的读取后，它得以完整跑完，随即暴露既有差异：

```
SUMMARY fail count=3 names=pipeline.stages.prefilter.p95_latency_ms,
                           pipeline.stages.scoring.p95_latency_ms,
                           pipeline.stages.enrich.p95_latency_ms
expected 2404/3491/7119  vs  actual 2056/3148/6385
```

与成本改造无关，是 expected 侧与 API 侧的 p95 计算口径差异。该脚本目前全仓无 caller，非自动化可达。

## ISSUE-008 · `radar.db` 有 932 MB freelist

**状态**：open · **优先级**：low

migration 016 的整表重写在 `data/radar.db` 留下约 932 MB 空闲页（库总计 3.2 GB）。`VACUUM` 可回收，但本身又是一次整库重写，会再次触发 ISSUE-006 那条路径——两件事应一起安排，不要单独做。
