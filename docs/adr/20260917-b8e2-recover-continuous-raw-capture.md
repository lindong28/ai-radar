# ADR-20260917-b8e2：恢复后续原始数据持续采集，以有限重试和独立健康检查暴露中断

- Status: accepted（决定已通过 L1 独立审查；实施与生产验收待完成）
- Date: 2026-09-17
- Related: [独立采集与 outbox](./20260916-e3a8-decouple-collection-from-processing.md)、[过滤前输入留档](./20260915-1cc7-retain-continuous-prefilter-inputs.md)、[每日 capture 数据推送授权](./20260913-c7d4-let-the-daily-capture-push-its-own-data.md)

## Context

用户要求不补历史缺口，修复根因、保证之后持续收集，允许自动重试与告警。Radar 必须完整保存 prefilter 输入；过滤本身是后续优化对象，不能用预筛后的输入冒充 raw。

本次诊断基线为 `371d21a`。主线程取证：2026-09-17 01:37Z，AIHOT capture 因 `openapi-v1.json` 返回 404 终止，而 `/feed.xml`、`/api/v1/items`、`/all` 返回 200。Radar 在 09-16 16:30 预检连接超时，17:00 发生 SIGABRT；OS 报告包含 malloc invalid free、`xmlDictGrow` 与 lxml deepcopy 调用链。12 个线程共享 trafilatura `HTML_PARSER` 是待复验的崩溃假设，尚无确定性 native 崩溃复现，不能据此宣称根因已证实。

## Options Considered

| 方案 | 取舍 |
|---|---|
| 保持补充探针失败即全轮失败、等待下一次每日调度 | 改动少，但已观察到可用 API/SSR 被 OpenAPI 404 阻断，单次故障继续造成缺日 |
| 将全部抓取串行化或在 raw 前过滤 | 扩大吞吐影响，或改变待优化对象的输入分布；不采用 |
| 提取互斥、网络仍并发；版本化降级补充探针；有界重试与独立检查 | 采用。分别处理已观察故障与失联可见性，保留完整输入及现有交接锁边界 |

## Decision

1. trafilatura 提取操作互斥，网络抓取保留并发。网络暂时故障有限重试；collector 最多尝试 3 次，间隔 15 秒，总预算 14 分钟。主 pipeline/outbox 双锁边界不变，不借恢复采集改变原始输入范围。
2. AIHOT 每小时检查应交付日，仅从启用日的 `capture_start`（UTC 00:00）起收集缺失的完整 UTC 日；不回补启用前历史。跨午夜仍保留欠交付日身份，不因只看昨日而遗忘欠账。已有通过校验的窗口跳过源站采集，但继续重试尚未完成的 Git 发布。AIHOT job 独立互斥，总预算 40 分钟。
3. 新增 `capture_v2`、`window_v3`、`report_v3`：RSS/OpenAPI 是补充探针，其失败须记录，不能阻断仍可用的 API/SSR 采集。旧版本语义保持不变，不把旧格式原地放宽。
4. 每 5 分钟独立健康检查：Radar 超过 20 分钟无完成采集或来源失败、AIHOT 完整 UTC 日结束 2 小时后仍未完成，进入对应故障状态；复用 im-notify 状态去重与恢复通知。完成证据与欠交付身份分开，已完成且合法到达 30 天保留期的窗口不因删除而假报缺口；不为每次健康检查逐窗计算全树 hash。
5. 数据推送仅沿用 ADR-20260913-c7d4 对 `benchmarks/aihot` → `captures/daily` 的既有授权，应用 push 不在授权内。生产 cron/env 启用继续遵守既有审批边界，决策审查放行不等于已启用。

## Consequences

提取串行段可能增加单轮耗时，小时检查和重试会在失败期间增加请求；14/40 分钟是执行预算，不是未来真实耗时的测量结果。重试不能恢复上游已经消失的数据，也不能证明未来永不中断；独立检查用于让未完成与恢复变得可见。

独立 reviewer `capture_reliability_decision` 首轮提出 3 条应修，补正后两问复核的 7 项判据均成立，决定放行。该结果只审决定，不代表实现验收。

待主线程取得的验证包括：提取互斥的实际作用与耗时、真实新格式 capture、实际 cron 重试、告警去重与恢复、长期连续性。当前不声称上述验证完成；运行证据取得后由主线程同步 [持续采集运维入口](../operations/continuous-eval-data.md)。

## 2026-09-17 实施中状态

本地已实现 collector supervisor、AIHOT 小时入口和五分钟健康检查入口；生产仍用原代码，新调度未安装。`capture_v2` / `window_v3` 已接入校验与 freeze；旧 `slice` 明确拒绝 `capture_v2`，本次不扩展切片范围。完整 prefilter 输入留档保持不预筛。

实现审查首轮提出两条 HIGH：同轮 AIHOT 重试应重新检查已有窗口以免重复抓取；补充 OpenAPI response Date 倒序应降为诊断。两项修复后，独立 reviewer 两问复核放行，无新增 findings。最终本地定向结果为 7 个测试文件合计 537 passed、1 deselected（54.38 秒），shell 78 项断言通过；覆盖有限重试成功/耗尽/超时子进程组终止、HTTP 暂时与永久错误、补充探针 404/异常日期降级及 API/SSR 失败严格拒绝、8 worker 提取互斥与 7 类真实文本输出等价。ruff 指出的单项 `Callable` import 已机械移至 `collections.abc`。排除项已在 `371d21a` 复现并留在 [测试基线债](../issues/testing.md)。这些读数不代表真实 cron、新格式远端发布或通知已启用及验收，也不证明未来永不中断。

数据发布被 Git 安全扫描阻塞：主线程重跑得到 52 个 `grafana-api-key` 命中，均位于公开分页 `canonical_query.cursor`，解码为字段键 `a,c,i,k,v` 的 base64 JSON。当前未修改 raw、未跳过扫描或关闭 scanner，等待用户授权精确修复误报。生产 cron/env 启用仍须独立许可；未进行历史回填、网站数据删除或应用 push。最终实现验证与运行状态以运维入口后续记录为准。
