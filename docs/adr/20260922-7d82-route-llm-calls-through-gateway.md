# 统一经 llm-gateway 调用外部模型

- Status: accepted for local implementation; production deployment not authorized
- Date: 2026-09-22

用户要求更新后端统一使用 llm-gateway，并确认项目 `ai-radar` 的费用归属为 `personal`。共享 gateway 接入合同由其 `integration.md` 拥有。

使用一个共享 SDK 接入层迁移仓内业务与评测调用：不读取供应商密钥、不直接回退供应商、SDK 重试为零；发送前产生请求 ID，成功响应校验 gateway companion 版本和同一请求 ID，保留实际模型、路由与 attempt 身份。保留业务 prompt、显式 Flash/Pro 模型区别、成功结果后的 best-effort 本地 usage 记录和冻结历史实验。新评测配置必须明确 gateway transport，旧配置不能静默伪装成新实验。

仅修改 base URL 会遗漏凭据、fallback 和身份校验；逐消费者重写裸 HTTP 会重复边界，故采用共享 SDK 接入层。`deepseek-text` 在当前 gateway 的不同供应商下分别指向 Flash/Pro，不能用它替代明确的模型选择。未登记或未 ready 的模型不得派发；catalog 新增需取得相应事实与许可。

ADR-002 的 Flash/thinking 语义、ADR-017 的计量失败不得重付原则保留。ADR-20260826-68e2 的外部 fetch/image 代理边界不变，LLM 改为同机 loopback next-hop。不通过传环境变量宣称仓外 `AI_ASSISTANT_ROOT` 已受控，须单独核实其调用实现；跨仓变更另界定范围。此次不 push、不部署生产、不重写旧实验数据。

决策审查：独立 reviewer `gateway_decision` 七项成立、无阻塞 finding（2026-09-22）。实施验证须覆盖 SDK 实际请求的头、成功/错误 identity、超时/HTTP 错误不重试，以及已 ready 模型的真实 consumer 调用与 gateway ledger 对账。SDK parse 异常仍须保留发送前请求 ID；不得不加区分地把供应商不支持的 `response_format` 注入所有调用。该放行不证明实现已完成、全模型可用或生产已切换。

实施审查：`gateway_implementation_review` 首轮发现旧 `aihot-fit` 的显式判官模型会被普通环境 override 覆盖。已改为直接传递显式模型，不修改进程全局环境；真实 SDK + MockTransport 的 Flash/Pro 双向冲突反例修前 2 failed、修后 2 passed，独立定向复核关闭该 HIGH，无新增 finding。其余尚未完成的运行接入边界见运维文档；既有 egress 清单缺项继续归测试问题账，不作为迁移成功证据。

后续用户裁决（同日）：授权补齐 exact model catalog、本机安装/重载及最小真实调用；将 Radar 所需 ai-assistant summary-agent 代码、模板和 KB 操作依赖迁入 Radar，以后在本仓维护。用户数据、persona、参考解读与凭据不入库、不删除原仓。新增 `AI_RADAR_KB_ROOT`，保留旧根路径的数据兼容但不执行其代码。解读缺 `criteria_reason` 的同轮摘要重发被 gateway 单次派发纪律取代，跨轮业务失败队列保留；生产发布与 push 仍未授权。

迁入闭包审查发现并修复调用目录改变导致锁身份分裂：用户锁绑定 KB 根、标签锁绑定词表路径，标准旧布局保留原锁路径。四种反例修前失败、修后通过，原 reviewer 定向复核关闭 HIGH，无新增 finding。最终定向回归与四个真实模型调用的 ledger 对账见 [运维读数](../operations/llm-gateway.md#2026-09-22-本地迁移验证)。
