# ADR-024：以 shadow canary 验证公众号后台发现适配器后再替换 Mp2RSS

- 状态：Accepted；identity mapping 与旧 evidence 语义由 ADR-028 supersede；cadence/page-size 由 ADR-025 supersede；deprecated——后台 family 平台级不可用，见 ADR-061
- 日期：2026-08-13
- 范围：AI Radar 对已知微信公众号的新文章 URL 与元数据发现；不改变现有微信正文抓取和公开展示边界

## 背景

AI Radar 当前通过一个 Mp2RSS 聚合源发现 14 个微信公众号的新文章，再由既有微信正文抓取链读取文章。Mp2RSS 当前订阅成本显著上升，因此需要一个可回退、可观测且不会扩大公开转载范围的替代发现层。

公开文章页会暴露公众号 `biz`，但匿名访问公众号历史接口会要求微信客户端或返回 `no session`；仅知道文章 URL 或 `biz` 不能匿名稳定列出最新文章。多个现有实现使用操作者自己的微信公众号后台登录态调用 `appmsgpublish`，并把公开文章的 `biz` 用作后台 `fakeid`。这条路径的真实登录寿命、限流、全部 14 个号的响应形状和合规边界尚未在本机获授权账号上验证。

## 考虑过的方案

1. 继续购买 Mp2RSS。稳定性风险最低，但不解决成本问题。
2. 使用 WeWe RSS 或其他微信读书桥。项目已观察到二维码会话频繁失效，且会引入额外平台依赖。
3. 使用搜狗微信搜索。反爬、覆盖率和时效不可控，只适合人工诊断。
4. 使用个人微信 MITM 或 `profile_ext/getmsg`。需要 `uin`、`key` 等更敏感状态，侵入性和维护成本更高。
5. 完整部署或 vendor `we-mp-rss`、`mp-data-console`。其 UI、数据库、调度、通用 RSS 和认证生命周期与 AI Radar 重叠，当前需求只需要一个窄的发现适配器。
6. 在 AI Radar 内实现最小发现适配器，并先作为 shadow comparator 验证。选择此方案。

## 决策

实现一个默认关闭的微信公众号后台发现适配器，范围限定为私有 AI Radar、当前明确配置的公众号、标题、原文 URL、作者和发布时间。它使用操作者获授权的公众号后台登录态，以已知 `biz` 请求最新文章；不抓阅读量、点赞、评论，不提供通用 RSS 服务，也不公开再分发正文。

实现和切换分阶段进行：

1. 无真实账号时，只实现默认关闭的配置边界、协议 client 接口、脱敏 fixture 解析、错误分类、`biz` 多 seed bootstrap、shadow 比较和凭据隔离的离线测试。不得宣称 authenticated API 或完整方案已经可用。
2. 第一次真实后台请求前，先实现私有凭据载体、日志与异常脱敏，并要求经复核的公开 seed 身份记录同时锚定配置账号名与 `biz`；一次人工显式 feasibility probe 可以先于完整告警，但不得进入定时 pipeline 或称为 canary。
3. 定时 canary 前，先实现专属持久状态与告警，并查清真实 scheduler owner。pipeline 可以每 15 分钟触发，但持久节流应使实际发现请求约每 2 小时一次，重启不能绕过节流。
4. 完成规定观察窗的 shadow canary、故障注入和回切演练后，才允许把新适配器切为正式发现源；此前保留 Mp2RSS，不取消订阅。

### Shadow canary 不写正式 items

人工 probe 与后续 canary 只写非服务态的候选文章、状态和比较结果，不作为第二个正常 source 写入生产 `items`。持久 attempt 必须记录运行类型、目标账号集合和当次候选 URL 集合，不能只保留全历史并集；shadow schema 必须有版本闸门，后续 multi-account canary 通过显式迁移接手。现有去重边界包含 `source_id`；双源同时写正式条目会制造重复文章。跨 source 全局去重不属于本决策范围。

shadow comparator 逐账号比较候选与 Mp2RSS 基准中的原文 URL、发布时间和发现延迟。它只能支持以下收窄结论：在明确的观察窗、账号集合和延迟上限内，候选覆盖全部 Mp2RSS 基准 URL，并通过独立人工抽样。它不能证明双方不会共同漏文，也不能证明未来或整个平台永不漏文。

### 凭据使用独立私有载体

Cookie、token、Playwright storage state 和其他认证材料只能进入独立、明确 gitignore 的本地私有文件；不得写入 `sources.url`、`sources.meta_json`、日志、异常文本、状态记录或测试 fixture。`/api/v1/sources` 会公开 source URL 与 meta，因此二者只承载非敏感静态配置。启动登录浏览器时只传必需环境，不继承与该任务无关的完整 dotenv secrets。

### 专属状态和告警

A1 检测 LLM provider 错误，不适用；A4 的聚合 fetch 失败率可能被其他来源稀释，不能单独承担微信发现失效检测。发现适配器必须有独立、持久且不含凭据的状态面，并让运维读者能区分：

- `disabled`：明确关闭，不期望运行；
- `unconfigured`：启用意图存在但缺少必要非敏感配置或凭据文件；
- `ready`：配置与私有凭据有效，但当前没有尚未解除的失败，且已到允许人工请求的时间；
- `next_request_at`：距下次允许请求仍有时间时作为最近一次成功终态的附加维度，不覆盖该终态；
- `auth_required`：登录态缺失或失效，需要重新登录；
- `identity_unverified`：公开 seed 未能同时证明配置账号名与 `biz`，不得发 authenticated request；
- `rate_limited`：平台限流，包含不泄密的重试时间或下一步；
- `request_failed`：网络或 HTTP 请求在取得可解析响应前失败；
- `response_invalid`：响应形状变化或解析失败；
- `success_no_new_shadow_candidates`：请求与解析成功，但相对 shadow state URL 集合没有新增候选；
- `success_with_new_shadow_candidates`：请求与解析成功，并相对 shadow state URL 集合发现新增候选。

状态值的权威来源是每次适配器 attempt 的持久结果；CLI 和告警只是投影，不独立改写第二份状态。首版持久契约只接受单账号 `probe`，不会暴露尚无 producer 的 `partial_failure`；multi-account canary 的 schema migration 必须新增聚合规则与失败账号投影。`next_request_at` 不能掩盖已经存在的 `auth_required`、`identity_unverified`、`rate_limited` 或 `response_invalid`。第一次定时 canary 之前，必须对认证过期、限流、响应形状变化、单账号失败和异常空响应做故障注入，并在实际投递链验证 firing 与 recovery。

## 切换门槛

正式替换 Mp2RSS 前必须同时满足：

- 操作者确认拥有获授权、可登录的公众号后台账号，并完成条款与合规边界判断；
- 在真实账号上验证 `biz == fakeid`、14 个号的响应形状、发布时间字段和异常返回；
- 证明两小时持久节流在进程重启和 15 分钟 pipeline 触发下仍成立，并查明真实 scheduler owner；
- 验证最新一页的容量覆盖观察到的最大发文突发，或实现有界分页；
- 多日 shadow canary 逐账号比较 URL 集合、发布时间和发现延迟，对每个差异逐条归因，并进行不依赖 Mp2RSS 的人工抽样；
- 故障注入证明专属状态、告警投递和恢复通知生效；
- headed 扫码在操作者实际使用的桌面上可见，不以进程、端口或 CDP 存活代替；
- Mp2RSS fallback 保留且回切演练成功。

## 后果与边界

该方案不直接 vendor 第三方完整项目，只在许可证允许范围内参考请求协议、字段、错误分类和 session 更新思路。若实测表明认证维护本身需要完整产品级组件，应作为新决策重新评审。

本 ADR 允许默认关闭的离线薄层实现，不证明当前 authenticated 路径可行。缺少获授权账号时，交付必须明确停在“离线候选已实现、live feasibility 未核实”；缺少专属状态和告警时，不得启动 scheduled canary；缺少规定观察窗证据时，不得切换或取消 Mp2RSS。
