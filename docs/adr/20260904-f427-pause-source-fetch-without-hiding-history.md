# ADR-20260904-f427：暂停来源抓取但保留历史可见性

- 状态：已接受；仅表示仓内代码与配置语义，生产迁移、同步、发布与真实消费者验收尚未执行
- 日期：2026-09-04
- Supersedes：[ADR-059](059-dual-run-wechat-feeds-with-a-cross-source-article-identity.md) 中“Mp2RSS 继续主动运行”的状态；其跨源文章身份与 ordinary source 只匹配 enabled 来源的设计继续有效
- 相关：[微信公众号摄取运维](../operations/wechat-ingestion.md)、[信源维护与验证](../references/source-maintenance.md)

## 背景

ADR-059 为避免切换期漏文，决定让 Mp2RSS 与 Wechat2RSS 主动双跑，并建立跨源文章身份。本发布单元把 Wechat2RSS 设为主动微信抓取入口，而已到期的 Mp2RSS 订阅不再供稿。若把 `wx_mp2rss` 直接设为 disabled，历史文章会从 `/wechat`、搜索和详情中消失，既有文章也不再作为跨源去重锚点；若仍保持普通 enabled，它又会继续进入 fetch 与 A7，产生无意义请求和静默告警。

所需状态不是删除、隐藏或“健康”，而是：停止这个来源的主动工作，同时保留它已经贡献的历史内容与稳定身份。

## 决策

来源增加持久 boolean `paused`。v2 contract 的每一条来源必须显式声明它，任何 `schema_version=2` 配置（包括 ad-hoc v2）也同样必须显式声明 boolean `paused`。仅 versionless 或 `schema_version=1` 的兼容配置在未声明时默认为 false。`wx_mp2rss` 声明为 `enabled=true, paused=true`，其余当前来源声明 `paused=false`。

四个集合分别定义：

| 集合 | 判据 | `wx_mp2rss` 暂停后的归属 |
|---|---|---|
| inventory / identity | `enabled=true` | 保留 |
| fetchable | `enabled=true AND paused=false` | 排除 |
| A7 可评估 | `enabled=true AND paused=false` | 排除 |
| 历史可见与既有消费者 | 继续使用各自的 enabled 语义 | 保留 |

“既有消费者”包含 `/wechat` 列表、搜索和详情、interpret 候选、A5、跨源 dedup 与 discovery compare。不得把 fetchable predicate 复用到这些消费者。ordinary disabled row 退出这些历史可见与跨源去重集合；固定 disabled 的 `wx_ai_assistant_kb_archive` 是集中维护的保留例外，只进入 `/wechat` 与跨源 dedup，并继续排除在公开 source API、runtime source loading、fetch 与 A7 之外。这个例外不适用于 `wx_mp2rss`，也不把 generic disabled 改成可见状态。公共 source API 不增加 `paused` 字段；About 的“已启用”解释为已收录且历史可见，运行时配置状态只说明抓取入口是否可用，不承诺主动抓取。

显式 paused 的 v2 optional WeChat source 即使 `required_env` 缺失也以 inert row 加载，因此 reload 继续得到 `enabled=1, paused=1`，不会因缺 secret 把历史身份静默 disabled。该例外只适用于满足完整 v2 WeChat placeholder 契约的 paused row；普通未暂停 optional source 缺 env 时仍跳过。仅补上 `MP2RSS_FEED_URL` 不会解除暂停。

A7 在 episode 首次 firing 时冻结 opening source identity。若已 announced 的 firing episode 中全部 opening source 后续都变为 paused，不发送“恢复”或任何其它通知，状态直接 closed/ok，并只追加一条 `type=resolved, channel=INTERNAL, reason=source_paused` ledger。内部 ledger 写入失败时保持 firing 并在下轮重试，不用外部通知补偿。若这个已 announced 旧 episode 的全部 opening sources 都在同轮 paused，但同轮又出现新的 unrelated silent sources，只有在状态中恰好存在一个 firing lifecycle、且旧 episode 与 current result 的 source identity 均非空时才允许 rollover：先成功写入旧 episode 的 INTERNAL `source_paused` resolved ledger，再关闭旧 episode，并以 current source IDs 和当前时刻建立未 announced 的新 firing episode。ledger 写失败、存在多个 firing lifecycles，或任一侧 identity 为空时，sender 为 0 且原样保留旧状态。

迁移前没有 source ids 的 legacy announced-firing episode 必须先由 `admin alert-prepare-source-pause` 从 opening firing ledger 恢复身份；只接受 `ts` 与 `episode_since` 都表示 lifecycle `since` 的同一时刻、且归一化 source identity 唯一的记录。opening 记录缺失、畸形、不含目标来源或同一时刻有冲突身份时 fail closed，不从 episode 后续变化的成员集猜测原始身份。

未 announced 的 legacy firing 不进入 source identity prepare/blocking 或 INTERNAL `source_paused` ledger 分支，而是沿用 pending 且从未成功公告 episode 的静默关闭语义。

若只有部分 opening source 变为 paused，暂停本身不能证明其余来源已恢复。只有每个未暂停 opening source 都有本轮逐源评估证据时才允许普通 resolved；证据不足则保持 firing 且 sender 为 0，全局结果仍 degraded 时保留 degraded 语义。证据充足时的普通 resolved 文案只声明有当前证据的恢复子集，并单列因暂停退出评估的子集。

## 被否的方案

**把 `wx_mp2rss` 设为 disabled。** 作为 ordinary source，它会同时退出 v2 inventory、历史页面、去重与比较基线，违反“停请求但保历史”的目标；保留归档源的专用例外不能外推到它。

**只在 fetch runner 按 source id 特判。** 它无法同步关闭 A7，也把一项通用生命周期状态藏进单一调用点；后续新增 fetch 入口容易绕过。

**缺 env 时仍让 loader 跳过 paused row。** reload 会把数据库行 disabled，使“历史仍可见”取决于 secret 是否存在，状态不再幂等。

**把 `paused` 暴露到公共 API 或页面加 badge。** 当前用户需要的是历史不消失、状态文案不误导；新增 API 字段或管理 UI 会扩大兼容面，且不属于本次切换。

## 后果与恢复门槛

- 配置权威变为 contract → renderer → `data/sources.toml` → loader/sync；手改生成 TOML 不构成恢复。
- 恢复 `wx_mp2rss` 主动抓取前，必须验证上游可用，补齐 env，将 contract 的 `paused` 改为 false，重新生成、reload 并复验 fetch/A7；只设置 env 不够。
- 维护者机器上 repo 外的 `shadow-observe` 会直接读 Mp2RSS，不受应用 selector 约束。它必须在生产收口的独立授权 gate 中精确移除；若现场已无目标行，则用 no-op readback 证明其它 crontab 行不变。
- 本 ADR 接受的是代码与配置语义，不表示生产已经迁移、发布或停止所有外部请求；这些终态只能由真实 migration、DB sync `committed`、code-deploy journal 与 serving SHA、crontab readback、自然 pipeline 以及公网消费者证据关闭。
