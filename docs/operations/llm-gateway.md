# AI Radar 的 LLM Gateway 接入

> Reader: [User] — 配置、验证与排查后端模型调用。决策见 [ADR-7d82](../adr/20260922-7d82-route-llm-calls-through-gateway.md)。

当前发布结果见 [2026-09-22 发布结果](#2026-09-22-发布结果)；下方“本地迁移验证”中的未部署表述只描述当时的历史检查状态。

## 调用范围

仓内 prefilter、score、enrich、OpenAI 备选 scorer、旧评测判官及 `evals/_shared/transport.py` 使用 `src/airadar/provider/llm_gateway.py`，不直接连接供应商。业务 prompt 和评分/分类规则不在此次变更范围。历史实验中的 provider、配置及实际模型身份保持原样，新运行使用 gateway 配置。

微信解读所需 summary-agent 依赖闭包按用户授权迁入 `airadar.interpret.engine` 和 `scripts/interpret/`，其中 chat、标签归一化和 embedding 均走共享 gateway。`AI_ASSISTANT_ROOT` 只保留数据路径兼容，不执行外部仓代码；原仓及用户 KB、凭据没有被复制或删除。配置与维护见 [解读引擎](../references/interpretation-engine.md)。

## 配置与上线前验证

应用只使用 `AI_RADAR_LLM_GATEWAY_BASE_URL`（默认 `http://127.0.0.1:39011/v1`）和 `AI_RADAR_LLM_GATEWAY_PROJECT`（默认 `ai-radar`）。Gateway 应与调用进程运行在同一台主机，不通过开发机转发生产推理。个人 policy authority 是 `~/research/ai-agent-config/llm-gateway/config/registry.json`，供应商 key 由 gateway 管理，不复制到应用。

```bash
curl --noproxy '*' --fail http://127.0.0.1:39011/health
~/research/llm-gateway/bin/llm-gateway --format json discover --project ai-radar --logical-model deepseek-v4-flash
~/research/llm-gateway/bin/llm-gateway --format json discover --project ai-radar --logical-model deepseek-v4-pro
```

要求 health 为 `ok` 且 file/loaded revision 与待使用配置一致；discovery 为 projection 2、exact project/model、scoped `ready` 且至少有 eligible candidate。它们不代替真实 consumer 验证：发送一条获准的业务请求，使用响应 companion v1 的 `logical_request_id` / `attempt_id` 对照 gateway ledger。未登记的模型不能用含糊别名替代；当前 `deepseek-text` 的不同供应商映射包含不同 Flash/Pro 型号，不等价于指定 Flash 或 Pro。

新评测配置：`evals/_shared/configs/baseline-gateway.json`、`prefilter-pro-gateway.json`，以及分类/评分各自的 `configs/*.json`。`transport_identity` 写 `provider=llm-gateway`、exact `project` 和 loopback `base_url`。旧冻结配置保留用于解释历史结果，不直接作为新实验配置。

## 失败、重试与用量

2026-09-22 的 17:45 scheduled pipeline 暴露一条非对象 JSON 响应中断 prefilter 整批的问题（gateway request `1b9e870c-e9e2-433c-9842-c2c7177d3021`，供应商成功不等于业务解析成功）。prefilter 现在把 `GatewayRequestError` 保存为该条 evaluation 的 error，保留 request ID、响应与 companion，不生成负例、不在本轮重发，继续处理其余候选。同 ruleset 只跳过成功评价，失败项在仍属候选时间窗时可于下一轮重试，保留失败历史；不把全部调用失败的一轮报为零错误。

SDK `max_retries=0`，应用不在供应商之间 fallback；同轮富化失败也不重发。Gateway 是一次请求的供应商重试/回退 owner。不确定是否已派发时，先按发送前生成的请求 ID 查询 gateway ledger，不立即重跑任务。既有跨轮失败队列调度不等于同一请求的幂等重放；手工重跑或重新调度产生新业务尝试，可能再次计费。

响应中的 `llm_gateway` 记录真实 provider、native model、route、credential profile 与 revision。业务异常保留发送 ID；评测本地 attempt 在发送前落盘，响应解析失败仍保留 raw/usage/identity。Gateway ledger 是 provider attempt 账；本项目 `llm_usage.db` 保留成功响应的业务归因兼容投影，不升级宣称为完整账单，也不与 gateway 数字相加。未知费用保持未知。

当前已有阶段失败统计与告警继续消费业务失败；此次不增加第二套告警发送器。Gateway 服务不可用按其 lifecycle/status 运维处理，模型未就绪按 discovery 返回的 action 处理。上线前必须在实际执行主机完成登记、配置加载与真实调用验证；本地代码完成不代表生产已部署。

## 2026-09-22 本地迁移验证

隔离 worktree `codex/llm-gateway-20260922` 的组合测试为 392 passed、1 skipped、1 deselected，覆盖业务 providers、prefilter、评分/富化、两个评测 transport 和判官、usage 与微信解释器兼容；均为离线测试，不是模型效果评测。输入面包括多个逻辑模型、成功/超时/HTTP 错误/解析失败、合法/不合法 loopback 地址以及匹配/错配 companion。修复显式判官模型覆盖后，gateway/governance/旧判官/provider 聚焦集 66 passed，其中新增两题覆盖 Flash 与 Pro 的相反环境 override。独立实现审查已关闭该修复项。

被 deselect 的 egress exact-registry 测试单独运行仍失败：三项原有子进程调用未登记，见 [测试基线债](../issues/testing.md)。不能将以上结果称为全量测试通过。

用户随后授权补齐 catalog、本机安装生效和最小真实调用，并要求将 Radar 所需 summary-agent 代码迁入本项目。个人 policy 已提交为 `2209fc45` 并安装；本机 launchd 服务 `live.lindong.llm-gateway` 的 `/health` 为 ok，file/loaded revision 均为 `52ca62398d61c244d1e7c7265bed0a1be24d8670f93a1d3e0fa7d4ed104d8044`。安装器一次 bootstrap 返回 5，内置单次恢复后服务健康与 revision 校验通过。四个 exact model（Flash、Pro、GPT、embedding）的 scoped discovery 均 ready。

本地真实 consumer 验证（每项一个请求，不代表模型效果或持续可用性）：

| 消费者 / logical model | Request ID | 解析 / gateway ledger |
|---|---|---|
| `chat_json` / `deepseek-v4-flash` | `825b3a2a-051f-428f-8df6-bef6c57c5587` | JSON 成功；ARK、1 attempt、success、39 tokens |
| `chat_json` / `deepseek-v4-pro` | `29e78aed-783d-4a8a-b4cf-df68e6bb47b3` | JSON 成功；ARK、1 attempt、success、41 tokens |
| `CodexGptMiniScorer` / `gpt-4o-mini` | `27f0253c-b0a5-4784-aec1-f33d33983dbc` | 严格结构化评分成功；OpenAI、1 attempt、success、357 tokens |
| 迁入引擎 `get_embeddings` / `text-embedding-3-small` | `58d9f2e3-1947-4fb0-93fa-f4860e640a6e` | 1×1536 有限非零向量；OpenAI、1 attempt、success、5 tokens |

四个响应 companion 的 request/attempt 与本机 `~/.local/state/llm-gateway/audit.sqlite3` 逐条一致，project 均为 `ai-radar`；ARK 费用为 unknown，OpenAI 为 estimated，不将未知费用写为零。验证输入为两个简短 JSON 确认请求、一篇合成新闻、一段向量测试文本；每个模型/入口仅一个请求，不代表持续可用性。未跑新闻效果评测、未测试全部供应商候选，未执行真实 KB 保存或部署生产。

迁入后的组合兼容测试 208 passed、1 deselected（上述 egress 基线），覆盖两个 gateway endpoint × 六种响应结果、有效/无效摘要、真实 CLI 读取临时旧布局 catalog、KB 锁与两阶段写、已有微信消费者，以及新旧数据路径两态。新引擎/脚本、gateway 接入层、runtime env 与 CLI 的定向 Ruff 和 shell 语法检查通过。摘要两份 Jinja 模板的字节摘要与来源一致，没有借迁移调整业务 prompt。

最终定向组合回归为 532 passed、1 skipped、1 deselected；skip 是未提供 `PREFILTER_ADOPTION_SOURCE_RUN` 的冻结 300 题 replay，deselect 是上述已单独确认的 egress 基线债。覆盖前述各输入面，另有 KB/词表 × 新/旧布局四种真实文件锁争用与释放反例：修前四题失败、修后通过。独立 reviewer 复核关闭锁身份 HIGH，无新增 finding、无承重未核实项；没有在真实用户 KB 上执行并发写入。

## 2026-09-22 发布结果

用户已授权 push 与部署。以下为主线程当日现场记录，截至 18:16 +08；不是文档编辑时重新探活。

| 发布面 | 已确认结果 |
|---|---|
| GitHub | AI Radar `origin/main=e70d39bf4526d2919d61a3e44c22c8a24eee7ee3`；ai-agent-config `origin/main=2209fc45610ede2c98ac50620922ac1943b88bb8`，均已 push。 |
| 腾讯代码 | 已发布 `0aff4f57faa0be2ef47e0d0d9b9f772bdc97d28c`，父节点 `03837af593aa78708482184e00649be289443dc9`；75 文件的最小 gateway backport，保留生产安全加固与旧 prompt 业务规则。 |
| 腾讯运行态 | `.deployed-sha` 与 code-deploy journal 的 `idle/deployed` 均对应上述发布 SHA；`serve@8000` active、PID `544072`，备用 `8001` inactive 为正常状态。公网 health 为 success/ok，`/` 与 `/wechat` 均 HTTP 200；仅证明 HTTP 可用性，不是视觉验收。 |
| Mac mini gateway | `live.lindong.llm-gateway` healthy；file/loaded revision 均为 `52ca62398d61c244d1e7c7265bed0a1be24d8670f93a1d3e0fa7d4ed104d8044`。定时 pipeline 在 Mac 执行，腾讯仅运行网站及 DB 副本，未在腾讯新增 gateway 服务。 |

导出的腾讯提交树定向测试为 440 passed、2 deselected，覆盖 gateway 成功/失败身份、摘要解析、KB 新旧布局与四种锁争用、provider、admin/runtime-env、部署及安全回归；故意撤销错误隔离后出现 1 failed，确认测试消费导出源码。两项排除均为部署基线独立失败：`test_source_pause_runbook_documents_internal_resolution_ledger_query` 的文档字面串不匹配，以及 `test_checked_in_network_callsites_match_classified_registry_exactly` 中既有 `scripts/eval/measure_curated_composition.py:103` 的 `subprocess.run` 未登记，不宣称全套测试通过。

保留的 MEDIUM 边界：腾讯旧 prefilter 成功 `raw` 尚未像 main 一样附加 `sent_request_id/llm_gateway`，但 `llm_usage` 归因与 gateway ledger 有身份；不影响派发或本次失败记录，未借部署改变其成功输出契约。未手动同步 DB、未改业务候选或 gold、未做真实 KB 写入验收。18:00 prefilter 成功早于 `e70d39b` 修复，不归因于该补丁；18:15 轮在观察时刚开始，不记录为完成。
