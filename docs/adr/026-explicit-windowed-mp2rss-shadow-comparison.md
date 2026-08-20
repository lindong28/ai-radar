# ADR-026：以显式只读命令执行窗口化 Mp2RSS shadow 对比

- 状态：Accepted；持久状态演化由 ADR-027 supersede；deprecated——后台 family 平台级不可用，见 ADR-061
- 日期：2026-08-13
- 范围：默认关闭、单账号人工 probe 的 shadow 结果与生产 `wx_mp2rss` 基准对比

## 背景

ADR-024 要求在替换 Mp2RSS 前逐账号比较 shadow 候选与生产基准，但现有实现只有未接入消费者入口的集合比较 helper；`status` 与成功 probe 的输出要求操作者继续比较，却没有可执行命令。当前真实 shadow 数据库只有 `auth_required` 和 `rate_limited` 两次失败 attempt，没有成功候选，因而还不能形成 live coverage 结论。

生产基准是聚合 source `wx_mp2rss`。2026-08-13 的只读检查显示，配置中的 14 个账号名与该 source 的 14 个 distinct author 精确一致；这只证明当前数据快照可按账号名分桶，不是长期稳定性保证。生产历史中单账号每日最大发文数可超过 5，而 ADR-025 的人工 probe 默认只取 5 篇，因此不是任意观察窗都具备可比较性。

## 考虑过的方案

1. 每次成功 probe 后自动读取生产数据库并比较。这样会让外部受限请求路径依赖生产数据库可用性，也无法独立重放历史 attempt。
2. 在 `status` 中隐式比较最新结果。`status` 的既有职责是投影持久 discovery 状态；自动读取生产大库会引入额外失败面，且观察窗不明确。
3. 保留未接线 helper，由操作者另写查询。现有 CLI 承诺的下一步不可执行，不能形成消费者可见证据。
4. 提供独立、显式、只读的窗口化 compare 子命令。选择此方案。

## 决策

增加独立 compare 入口，要求显式指定配置账号、成功 attempt 和观察窗起点；终点固定为该 attempt 的完成时间。命令不发微信后台请求，不写生产 `items`，不改变 probe attempt 状态，也不持久化第二份可漂移的比较结果。

只有以下门禁全部成立时才输出 `COVERED_IN_WINDOW` 或 `MISSING_IN_WINDOW`：

- attempt 是指定账号的单账号成功 probe；历史 schema v1 attempt 或没有请求页大小的记录不可比较；
- `since` 不晚于 attempt 完成时间，候选非空且候选发布时间不晚于完成时间；
- 返回数小于请求页大小，或页面最旧候选时间已经触达 `since`；否则单页可能在窗口内截断；
- 生产 source `wx_mp2rss` 存在、启用，配置账号名仍在其 distinct author 集合中，并以 `source_id='wx_mp2rss' AND author=account.name` 精确分桶；不做模糊匹配；
- 窗口内 Mp2RSS 基准非空；空基准不能证明覆盖；
- 两侧 URL 都能生成同族的公众号文章 canonical key。

公众号短链 `https?://mp.weixin.qq.com/s/<id>` 以文章 id 为身份，忽略 scheme、fragment、尾斜杠和 query tracking。旧式 `/s?__biz=...&mid=...&idx=...&sn=...` 只有四个身份参数齐全时才使用四元组；其他 host、path、缺字段或两侧 key family 不同均为 `NOT_COMPARABLE`，不能把不可证明的 URL 等价关系读成漏文。

报告逐账号展示 matched、missing 和 candidate-only URL。对 matched URL 可派生发布时间差与两条路径的发现延迟作为诊断字段，但这些值不参与 coverage gate；当前尚未证明两个路径的发布时间语义完全一致。

`COVERED_IN_WINDOW` 只表示指定账号、指定 attempt、`since..attempt.finished_at` 内的全部 Mp2RSS 基准 URL 出现在该 attempt 候选中。它不证明双方没有共同漏文，不覆盖其他账号或未来，也不满足最终切换门槛。

## 持久状态演化

shadow schema 升到 v2，为新 attempt 持久化当次 `requested_count`。v1 行迁移后该字段保持未知，因此永久 `NOT_COMPARABLE`；不伪造默认值。该字段的权威是实际请求输入，由 probe 写入路径与 attempt 一起生成并校验，compare 只消费该冻结值。

## 已知未验证项

- 尚未取得第一次成功 live response，因此真实 compare 当前应为 `NOT_COMPARABLE`；
- 首次成功后仍需核验 URL 形态、发布时间字段和发现延迟；
- 配置账号名与生产 author 的精确映射若发生改名，必须重新建立公开身份与生产映射证据；
- 每页 5 篇仍可能让较长窗口不可比较；正式 canary 需要有界分页或经实测证明容量足够；
- 逐账号窗口覆盖不是 14 账号多日覆盖，也不能代替独立人工抽样。
