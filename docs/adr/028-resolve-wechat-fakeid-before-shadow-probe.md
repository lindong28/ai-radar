# ADR-028：先解析并一次性消费已验证 fakeid，再执行微信 shadow probe

- 状态：accepted；searchbiz 验证语义由 ADR-040 supersede（其余部分不受该 supersede 影响）；deprecated——后台 family 平台级不可用，见 ADR-061
- 日期：2026-08-13
- Supersedes：ADR-024 中把公开文章 `biz` 直接作为后台 `fakeid` 的假设，以及旧 attempt 可用于后续覆盖比较的含义；不改变 ADR-025 的临时请求频率、ADR-026 的窗口比较语义或 ADR-027 的页大小来源记录

## 背景

ADR-024 首版把公开文章页的 `biz` 直接作为 `/cgi-bin/appmsgpublish` 的 `fakeid`。之后对两个开源实现的固定 commit 做源码核对：`mp-data-console@e97927d4835b7fa9d8786cc950c5f1e1995c8c26` 从 `/cgi-bin/searchbiz` 分别读取 `fakeid` 与 `biz`，再把 `fakeid` 传给 `/cgi-bin/appmsgpublish`；`wechat_official_account_crawler@ae6ecd77afc9db01dcc2d5250698e55b76e4a31d` 同样先搜索账号取得 `fakeid`，再读取文章。这只能证明“`biz=fakeid`”不再是有依据的通用安全映射，不能证明当前 14 个账号的两个值必然不同。

此前两次真实请求分别在认证和限流分支终止，没有取得成功 `searchbiz` 或文章列表响应，因而没有验证任何目标账号的 `fakeid`。继续把 `biz` 填入 `fakeid` 会让后续成功响应也缺少可审计的目标身份来源。

## 决策

人工 shadow 验证改为两个独立阶段：

1. `resolve` 对单个配置账号调用 `/cgi-bin/searchbiz`。只有搜索结果完整，且恰好一个候选同时满足 Unicode NFKC、去空白、casefold 后的账号名匹配，以及返回的 `biz` 等于经公开 seed 复核的配置 `biz`，才把返回 `fakeid` 记为 `resolved`。无匹配、多匹配、缺字段或结果不完整均 fail closed。
2. `probe` 只消费 shadow DB 中同一账号名与 `biz` 下、未消费、未失效、未被取代的 `resolved` 记录。它不再接受调用方提供 `fakeid`，也不再用 `biz` 代替。
3. 一条成功 resolution 在当前 feasibility 阶段只允许被一个真实 probe reservation 消费，不论该 probe 最终成功、认证失败、限流、网络失败或解析失败。长期复用周期与 TTL 留待成功 live 观测后另行决定。
4. `appmsgpublish` 返回的每篇文章必须使用 URL 中真实观测到的唯一 `__biz`；缺失或不等于配置 `biz` 时，probe 终止为 `identity_mismatch`，不写候选，并使本次使用的 resolution 失效。

`identity_resolution_attempts` 与 `discovery_attempts` 是同一 SQLite shadow DB 中的两张独立 ledger。前者只记录 `resolve`，后者保持 probe-only。每个新 probe 必须引用一条 resolution，并记录 `identity_resolution_origin=verified_resolution`；schema v3 及以前的 attempt 迁移为 `predates_resolution`，永久不能形成 compare 覆盖结论。

两类请求共用 `latest_backend_request()` 作为全局冷却权威。发任何网络请求前，进程必须先在 `BEGIN IMMEDIATE` 事务中提交 `reserved` 行；probe 还要在同一事务中原子标记 resolution 已消费并关联 probe attempt。只有 reservation commit 成功才准发请求。响应终态与候选快照在事务中落盘；若进程在 reservation 后崩溃或终态写失败，`reserved` 保留，status 显示 `REQUEST_OUTCOME_UNKNOWN`，并从 `started_at` 起消耗完整 refresh interval。冷却过后可以新建请求，但旧 unknown 历史不删除。

`status` 分三层显示：全局请求 gate、identity 计数与最新 resolution、probe 最新终态与最近可比较 attempt。总体 gate 优先级固定为 disabled、unconfigured、冷却期内的 unknown reservation、active cooldown、ready to probe、ready to resolve；旧 probe 成功不能覆盖新的身份失败或未知请求结果。

## 生命周期

同一账号名与 `biz` 的新成功 resolution 会 supersede 旧的未消费 mapping。后续 `no_match` 或 `ambiguous_match` 会使旧未消费 mapping 失效；认证、限流、网络和响应形状失败不声称身份已失效。配置账号名或 `biz` 改变后，旧 mapping 因 key 不匹配不可用。历史 probe 保留其原 resolution 引用，但 compare 拒绝旧 schema、缺引用和已失效 mapping。

## 边界

本决策仍只授权默认关闭、显式单账号、私有 shadow 验证；不会启用 scheduler、写生产 `items`、公开 `fakeid`、停用 Mp2RSS 或宣称方案已经可用。冷却结束后的下一次真实请求必须先运行 `resolve`，不能直接 probe；成功 resolution 之后还需等待同一全局冷却窗口，才能执行一次 5 篇 probe。
