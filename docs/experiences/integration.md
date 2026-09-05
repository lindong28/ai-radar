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
- **Applies when**: 补跑 attestation 时，**写盘前必须再校验一次生产 `policy_sha256` 未变**，变了就重跑，否则重演同一竞态；跨仓改 domain-routing 策略时，记得 ai-radar 的 interpret 收据是它的下游消费方。
