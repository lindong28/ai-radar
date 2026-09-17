# ADR-20260917-b8e2：恢复后续原始数据持续采集，以有限重试和独立健康检查暴露中断

- Status: accepted（决定与本地实现审查通过；已批准并安装调度，新日窗与连续性待验收）
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

## 2026-09-17 启用前实施记录

本地已实现 collector supervisor、AIHOT 小时入口和五分钟健康检查入口；生产仍用原代码，新调度未安装。`capture_v2` / `window_v3` 已接入校验与 freeze；旧 `slice` 明确拒绝 `capture_v2`，本次不扩展切片范围。完整 prefilter 输入留档保持不预筛。

实现审查首轮提出两条 HIGH：同轮 AIHOT 重试应重新检查已有窗口以免重复抓取；补充 OpenAPI response Date 倒序应降为诊断。两项修复后，独立 reviewer 两问复核放行，无新增 findings。最终本地定向结果为 7 个测试文件合计 537 passed、1 deselected（54.38 秒），shell 78 项断言通过；覆盖有限重试成功/耗尽/超时子进程组终止、HTTP 暂时与永久错误、补充探针 404/异常日期降级及 API/SSR 失败严格拒绝、8 worker 提取互斥与 7 类真实文本输出等价。ruff 指出的单项 `Callable` import 已机械移至 `collections.abc`。排除项已在 `371d21a` 复现并留在 [测试基线债](../issues/testing.md)。这些读数不代表真实 cron、新格式远端发布或通知已启用及验收，也不证明未来永不中断。

数据发布被 Git 安全扫描阻塞：主线程重跑得到 52 个 `grafana-api-key` 命中，均位于公开分页 `canonical_query.cursor`，解码为字段键 `a,c,i,k,v` 的 base64 JSON。当前未修改 raw、未跳过扫描或关闭 scanner，等待用户授权精确修复误报。生产 cron/env 启用仍须独立许可；未进行历史回填、网站数据删除或应用 push。最终实现验证与运行状态以运维入口后续记录为准。

## 2026-09-17 获批启用与当前边界

用户随后明确答复「批准启用」「授权精确修复」，上节的待授权状态由本节更新。本地 main 已快进到 `6eeb9dc`；AIHOT 独立 runtime 使用该源码，运行树 parent pin 为 `ee9ac71`，数据来自远端 `97f5ed0`。macmini/lindong 已安装 AIHOT `7 * * * *`、health `*/5`，Radar 保留 `*/15`，其他 cron 未改；主树原有 precompute WIP 哈希前后一致。

状态起点为 `2026-09-17T00:00:00+00:00`、`delivered_days=[]`。真实 AIHOT 入口于 06:10:46Z—06:10:56Z 退出 0，因尚无到期日未请求新 raw，不计新日窗成功；首个完整 UTC 日最早在本地 09-18 08:00 后到期。06:10:30Z 健康检查对 `claude_youtube` 触发真实告警，im-notify 返回 `alert sent via feishu`；06:11:43Z 独立演练 key 验证故障发送、重复不改 `sent_at`、恢复发送，未确认手机收件。

scanner 精确修复已授权，隔离 worker 实施中、尚未集成；数据发布仍未取得成功读数。自动调度后续运行、新日窗发布和长期连续性仍待验收，不据入口退出或发送回执作扩大结论。未补历史或改网站数据，具体运行入口、状态与配置备份位置见 [运维说明](../operations/continuous-eval-data.md)。

### 同日最终现场补记

上述 scanner 待集成状态已更新：harness 修复 `4c9be51f` 合入其 main，新 runtime 的 pre-commit 使用 canonical hook。实际旧 capture 暂存数据验证为 `rawFindingCount=52`、`publicCursorCount=52`、`effectiveFindingCount=0`、`indexUnchanged=true`，未改 raw 或绕过扫描。修复决定见 harness 仓 `docs/adr/20260917-2f6a-grafana-public-cursor-classification.md`；worker 测试 19/19、main cursor 测试 7/7，仅代表该修复的局部验证，不代表新日窗发布。

代码 `6eeb9dc` 的真实 14:15 自动周期完成三次尝试，分别保存 4,199、4,183、4,183 条 raw；第一轮仅微信专用源失败，后两轮另有 `claude_youtube` 失败。第三轮于 06:28:08.593133Z 完成，supervisor 于 06:28:09Z 退出 1；06:30:00.844320Z 下一自动周期启动并进入 fetch，证明本次锁释放后仍可继续，未将失败记为全源成功。health 于 06:20 自动发送恢复、06:25 再次发送故障通知。

完整 prefilter 输入仍不按成员或分数过滤，重试保留前次 raw。YouTube RSS 的间歇 404/500 仍为外部故障，继续按计划重试、告警；没有全源无缺口或连续多日验收结论。AIHOT 手动入口 exit 0 只覆盖无欠账及发布核验路径；09-17 完整 UTC 日在本地 09-18 08:00 结束，08:07 小时任务可采集，10:00 起仍未交付则进入晚到告警条件。新格式完整日与远端发布仍待实际到期运行验证。
