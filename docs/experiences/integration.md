# Integration 经验

> Append-only. 跨系统 / 外部工具接口约定相关的坑点和 pattern（ai-assistant、summarize.sh、KB 写入器等）。

## 2026-06-02 复用 ai-assistant summarize.sh 时 stdout 的 result 不含 summary 正文

- Problem: interpret pipeline 零拷贝复用 ai-assistant 的 `summarize.sh` / `run.sh --save-from-batch`。直觉上会以为 `summarize.sh` stdout 的 JSON `result` 对象里含完整摘要正文，照着取 `result["summary_md"]` 会拿到空——因为 ai-assistant 的 stdout schema **故意 pop 掉 summary_md**（避免把大段正文塞进 stdout）。result 只携带 slug / save_decision / recommendation / tags 等元数据。摘要正文实际落在 `<batch_dir>/<slug>_summary.md`（`batch_dir` 由 stdout 的 `batch_dir` 字段给出）。
- Solution: 摘要正文必须从文件读，不从 stdout 取——`src/airadar/interpret/runner.py:_summarize_item` 先 `summary_payload.get("result")` 拿元数据（L487）、`summary_payload.get("batch_dir")` 拿目录（L492），再 `_read_summary_file(_summary_path(batch_dir, batch_slug))` 从 `<batch_dir>/<slug>_summary.md` 读正文（L498）。另一条省钱路径：先 `run.sh --check-url <url>` 探测；若 URL 已在 KB，返回里带 `summary_file_path`（fallback `summary_file`），直接读该文件复用已有摘要，**不重跑 LLM**（runner.py L456-484：命中即 `save_decision=True` / `kb_synced=True` / `saved=False`）。
- Applies when: 改 interpret runner、或新接入任何复用 ai-assistant `summarize.sh` / `run.sh` 的集成时——不要假设 stdout 自带正文，正文一律走 `<batch_dir>/<slug>_summary.md`；处理已可能在 KB 的 URL 时先 `--check-url` 走缓存，省一次 LLM 调用。这是 ai-assistant 的接口约定，从 runner.py 调用代码本身看不出来，需要知道上游 stdout schema 的设计。

## 2026-09-05 `/wechat` 停更而抓取正常时，先查 interpret 的 egress 收据闸——它 fail-closed 且干净退出 0

- **Problem**: `/wechat` 只显示有解读的文章（`JOIN wechat_interpretations WHERE save_decision=1`），所以「抓取入库正常、页面停更」这个组合的第一嫌疑不是抓取层，而是 `interpret`。该阶段的前置校验一旦不通过就**干净退出 0、一个外部脚本都不启动**，pipeline 仍打印 `=== interpret OK ===`，A1–A7 全部沉默。实测代价：2026-09-01 15:00 起连续跳过 138 轮、215 篇微信文章无解读，无任何告警，直到用户肉眼发现页面不动。
- **判据**: pipeline 日志出现 `skip interpret: selector compatibility is unproven (...)`。逐字段定位用 `airadar.interpret.runner.expected_selector_compatibility_receipt` 与 `./run.sh egress-preflight` 的当前 `policy_sha256` 比对 `$AI_ASSISTANT_ROOT/ai-radar-egress-contract-v2.json`。注意另有一个不同形态：`require_selector_policy()` 的 status 命令返回非 0 时抛 `EgressPreflightError`，那是 `interpret FAIL (exit 1)`、不是跳过。
- **闸为什么存在**: 解读会 shell 出去调 `$AI_ASSISTANT_ROOT/agents/summary-agent/{summarize.sh,run.sh}`，文章正文离开本进程、由本仓管不着的代码发到外网。ai-radar 唯一的抓手是覆写六个代理变量成受管选择器，而这只对守规矩读 env 的子进程有效（`trust_env=False`、自定义 transport、原生 socket、不受管后代全在保证之外，契约文档明写）。收据是操作者签的证明，补的正是这个洞。详见 `docs/references/ai-assistant-contract.md`「Selector compatibility receipt」。
- **已知失败形态（竞态，不是漂移）**: 策略文件在**另一个仓** `system-config:config/agent-proxy/policies/domain-routing-v2.tsv`，改它就换 `policy_sha256`。`02fce04` 那次 attestation 基于 `a5f3433`（09-02 16:38）跑完，20:36 写盘时生产已被 `236d165`（09-02 20:32）换掉 → **收据落地即失效**。
- **Applies when**: 跨仓改 domain-routing 策略时，记得 ai-radar 的 interpret 收据是它的下游消费方。这条竞态本身已由下一条经验固化的 `airadar.interpret.receipt_writer` 关闭——收据一律经它写，不要再手工编辑收据文件。

## 2026-09-05 selector compatibility attestation 与收据写盘必须是同一个身份校验入口

- Problem: 一轮 attestation 可以正确测试某个 `policy_sha256`，但操作者随后手工编辑收据前，生产 domain-routing 策略已经切换。这样生成的收据即使字段格式完全正确，落盘时也已经不代表本轮实际测试的策略；consumer fail-closed 只能拒绝它，不能修复 producer 的时间竞态。直接把收据 SHA 改成当前值可恢复生产，却把“证明跑过”退化成形式。
- Solution: attestation 结束时同时固定 tested policy SHA 与实现闭包 SHA，只允许 `airadar.interpret.receipt_writer` 写收据。Writer 先拒绝闭包变化，再在任何备份或写盘前清空 selector cache，通过 interpret 共用的 `require_selector_policy()` 重新读取生产 status；live policy 不等于 tested policy 就保持原收据与备份集合不变、非零退出并要求整轮重跑。匹配时才创建时间戳备份并同目录原子替换。负例必须模拟“测试结束后换策略”，并同时观察退出码、收据字节与备份数；只证明检查命令报错不够，因为它没有覆盖实际 writer。
- Applies when: 任何跨仓兼容收据或 attestation artifact 的有效性依赖会独立更新的生产身份时。收据内容应来自本轮实测身份，最后一次权威身份读取与写盘应收敛在同一受测入口；不要把面向人的 status stdout 反向解析成新的机器契约。
