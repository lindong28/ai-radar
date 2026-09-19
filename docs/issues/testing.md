# Test-suite Issues

> 测试套件自身的红项与基线债——与具体功能 domain 无关、但会污染每一次改动的验收读数。
> 协议：`~/.claude/references/docs-organization-protocol.md` §4.8。

## [open] 2026-09-13：全量套件有 9 条常红，与改动无关，会淹掉每次改动的验收信号

- Type: test baseline debt · Priority: medium · Discovered: 2026-09-13 给 `docs/adr/20260913-e21a` 做基线归因时

`uv run pytest` 在**干净的 HEAD** 上固定失败 9 条。取法是同类对照，不是推断：在
`git worktree add --detach <tmp> HEAD` 的干净树上跑全量，失败集与带改动的工作树
**逐条相同**（我方树 9 failed / 2794 passed，基线 9 failed / 2736 passed；通过数差 58
是基线树不含主树那些未跟踪的测试文件）。

| # | 测试 | 备注 |
|---|---|---|
| 1 | `tests/test_admin_cli.py::test_source_pause_runbook_documents_internal_resolution_ledger_query` | runbook 文档断言 |
| 2 | `tests/test_aihot_dataset.py::test_capture_writer_refuses_non_repo_root_and_existing_capture` | |
| 3 | `tests/test_cost_observability_round3.py::test_cache_measurement_unknown_stays_null_through_sql_api_and_html[live-schema]` | |
| 4 | 同上 `[post-p3-schema]` | |
| 5 | `tests/test_egress_callsite_registry.py::test_checked_in_network_callsites_match_classified_registry_exactly` | 注册表与实际 callsite 漂移 |
| 6 | `tests/test_p1_narrowed_contract.py::test_p2_admin_contract_restores_consumer_defined_aggregate_fields` | |
| 7 | `tests/test_pipeline_scheduler.py::test_real_run_sh_chain_preserves_pipeline_lock_fd_and_generation` | 失败点是 `egress preflight FAIL (exit 1)`，疑与本机出网环境相关 |
| 8 | `tests/test_egress_routing.py::test_playwright_external_and_loopback_reach_the_selected_listener` | **只在全量跑时红**；单独跑两次皆绿（含基线树），疑测试间干扰或环境相关 |
| 9 | `tests/playwright/test_fixture_isolation.py::test_playwright_session_db_is_deterministic_and_serve_uses_it` | 基线树单独跑两次皆红 |

**为什么值得跟踪**：9 条常红使「全量绿」不再是可用的验收判据，每次改动都要自己去建一次基线
对照才分得清归因——而这一步很容易被省掉，于是新引入的回归会被读成"又是那几条老的"。
第 8 条另有一层：它在单独跑与全量跑下读数不同，说明套件内存在顺序或共享状态依赖，
那类依赖会让任何"单独跑过、是绿的"的局部验证失去效力。

**未做**：没有逐条诊断根因。第 7 与第 8 条看起来与本机出网 / playwright 环境耦合，
若确实如此，它们在 CI 或另一台机器上的读数可能不同——本轮没有第二台机器上的读数。

### 2026-09-17：第 2 项的基线复现与本次去向

持续采集恢复任务在基线 `371d21a` 再次复现 `tests/test_aihot_dataset.py::test_capture_writer_refuses_non_repo_root_and_existing_capture`：断言期望 `output_root_invalid`，实际得到 `git_checkout_invalid`。本次定向回归仅排除此单项，不能把排除后的结果称为该文件全量通过。该问题继续归本测试基线债，本轮不修旧测试；未来修复需对齐非仓根路径的错误码契约与断言，不能仅为变绿放宽检查。

### 2026-09-19：基线已漂到 14 条（HEAD `0d0dc36`）

FTS 触发器修复（003 `items_au_fts` 改为 `AFTER UPDATE OF …`）的全量对照：工作树 14 failed / 3270 passed。上表第 1、7 项本次未红；新增 7 条在 `git archive HEAD` 导出树上（`PYTHONPATH=<tree>/src`）同样失败或同样只在全量时红，与本次改动无关：

| 测试 | 读数 |
|---|---|
| `tests/test_fetcher.py::test_fetch_source_delegates_to_fetch_then_apply` | `fake_apply_source_feed_result() takes 2 positional arguments but 3 were given`（`runner.py:127`），测试桩与代码签名漂移 |
| `tests/test_ingestion.py::test_abrupt_process_exit_after_source_commit_is_recoverable` | 子进程 `python -c` 报 `No module named 'airadar'`，环境耦合 |
| `tests/test_audit_receipts.py::test_x_success_allows_terminal_zero_item_and_draining_connectivity[...]` ×2 | `production code hashes does not match current file: src/airadar/fetcher/runner.py`，收据钉住旧 `runner.py` 哈希 |
| `tests/test_eval_dataset_merge.py::test_cli_base_only_and_invalid_arguments_and_corrupt_base` | HEAD 树同红，未诊断 |
| `tests/test_eval_system_integration.py::test_four_object_run_uses_distinct_o1_cases_and_preserves_queries` | HEAD 树同红，未诊断 |
| `tests/test_eval_object_datasets.py::test_cli_usage` | 单跑绿、全量红，同上表第 8 项的形态 |

取数时工作树另有一位写入者的未提交改动（`scripts/collection_supervisor.py` 等），上述 HEAD 树对照已排除它们的影响。
