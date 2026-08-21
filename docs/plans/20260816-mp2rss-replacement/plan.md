> **Archive status**: 已归档。执行过程产物 `state.md` / `journal.md` 按长任务协议不入档；同目录 `tools/shadow_compare.py` 是 shadow 期的一次性对比工具，随 plan 归档作为 provenance。
> 双跑与跨源条目身份的最终裁决见 [ADR-059](../../adr/059-dual-run-wechat-feeds-with-a-cross-source-article-identity.md)，平台级发现层失效的归因已提炼进 ADR-061（与本次归档并行新建），取证明细见 [references/wechat-discovery-evidence.md](../../references/wechat-discovery-evidence.md)，运行口径见 [operations/wechat-ingestion.md](../../operations/wechat-ingestion.md)「双跑」与「公众号后台发现候选」两节。
> **中止点**：执行至 TASK-005 中止——路线改向 Wechat2RSS 私有部署，公众号后台 appmsg family 整体出局；正文的 7 日稳定性观察与生产切流 gate 未执行。以下为原 plan 正文，未修改。

> ⚠️ **Long-task mode** — 本 plan 处于长任务模式
> - 进度状态：`./state.md`
> - 决策日志：`./journal.md`
> - 协议详情：`~/.claude/references/long-task-protocol.md`
>
> 实施时（含 compact 之后）必须先读 state.md 和 journal.md 再决定下一步动作。
> 声称任务完成前必须实际跑本 plan 的 verify 步骤并贴出输出。

# Mp2RSS 替代发现链路：持续取证、实现与影子验收

## 目标与当前状态

本计划续接用户最初的目标：为 AI Radar 的 14 个微信公众号建立一个可自托管、可持续发现未知新文章、并能在同一时间窗口内替代 Mp2RSS 的元数据发现链路。已知文章 URL 的正文抓取不在本计划内重做；发现层只需产出经公众号身份核对的文章 URL、标题、作者和发布时间，再交给既有抓取与摄取链路。

当前生产仍由 `wx_mp2rss` 提供数据，任何 shadow 候选、实验 cursor 或登录态都不得写入生产 `items`。现有实现已经具备：授权后台登录、`searchbiz` 的 name-only provisional mapping、`appmsgpublish` 单页 probe、返回 URL `__biz` 校验、versioned shadow ledger、只读 Mp2RSS 同窗比较和精确平台错误码持久化。真实数据只有一个账号两次 provisional match、四次未形成 candidate 的 probe，以及一个微信读书账号书架缺席负例；它们不足以证明替代方案可用。

`refresh_interval_minutes = 1440` 是本地、默认关闭的人工 canary 安全阀，不是微信官方公布的 24 小时窗口。历史程序没有保存 exact `base_resp.ret`，且曾把多个错误合并为 `RATE_LIMITED`，因此历史失败也不能证明 24/48 小时冷却。正式路线不得把 24/48 小时等待作为平台契约或调度基础。

2026-08-16 的公开链路实测已证明：用已知标题可以经搜狗结果解析到真实微信文章页，并从页面取得与配置一致的 `__biz`。这只证明 title replay / 补漏能力，不证明能发现未知新文章。公开索引、独立官网、微信读书和授权后台都只是待验证的 discovery families；未通过 account-listing / result-crawl 验收前，不把任何一个声明为主适配器。

## 最终产物与使用方式（L1）

最终交付是 AI Radar 内的一条可定时运行的微信公众号发现组件，覆盖 `data/wechat-discovery.toml` 的全部 14 个账号。组件按账号维护发现 cursor，周期性产出经身份验证的候选文章元数据，失败时保留可操作状态且不推进 cursor；既有文章抓取器消费候选 URL。操作者通过 `./run.sh admin wechat-discovery status` 判断覆盖、认证、失败层和下一动作。

生产切换前，组件只运行 shadow mode，并以当前 `wx_mp2rss` 为同窗基准。达到验收门槛后，向用户提交覆盖、延迟、请求量、登录续期与失败恢复报告；只有获得用户对生产切流的显式许可，才修改生产 source/scheduler。提交、push 或将非 main 分支整合回 main 也分别遵循现有显式许可边界。

如果所有已枚举的合规、可自托管 discovery families 都无法满足“未知新文章发现 + 14 账号覆盖 + 可持续运行”，最终产物改为 `plans/20260816-mp2rss-replacement/evidence/unreachability-report.md`。它只能给出有边界的 `NO_EXECUTABLE_ROUTE_FOUND` 结论，不声称数学意义上的永久不可达；报告必须逐路线列出直接观测、阳/阴对照、失败层、已尝试缓解、覆盖缺口、账号分区组合结果和搜索空间闭合证据。闭合 gate 要求两次相互独立的路线搜索（开始时一次、结案前一次）得到的所有具体可执行候选都进入 route matrix，且不存在尚未执行、能区分结论的下一检查。两次搜索分别落 `evidence/search-pass-a.json` 与 `evidence/search-pass-b.json`；manifest 必须记录不同 search backend、独立形成的 query-set、开始/结束时间、冻结前未读取另一轮 raw results 的声明、raw result URL 集及 digest。checker 拒绝 backend 相同、query-set digest 相同、缺 raw snapshot/digest、第二轮未晚于第一轮，或先合并候选再伪造第二份结果的证据。任何未测试的具体路线、可行的账号分区组合、`pending` 区分性检查或仅等待自然发布/认证事件即可取得的必要观察都会阻止该结论。单个接口失败、搜索结果陈旧或一次 `200013` 都不构成不可达证明。

## 用户视角验收（L2）

以下全部是完成 gate；内部单测、schema review 或单账号正例不能替代它们。

1. **未知新文章发现（agent 可执行）**：对每个配置账号，从不提供目标文章标题或 URL 的状态开始运行 discovery；新发布文章进入候选集合。已知标题检索只能计为补漏证据，不能计为该项通过。
2. **14 账号覆盖（agent 可执行）**：expected 集合取同窗 `wx_mp2rss`、候选 provider、账号第一方发布面和随后由真实微信页确认的文章之并集，而不是只从 Mp2RSS 生成。候选实际集合对 14/14 账号逐项比较；任一经公开文章页或第一方发布面确认、但候选 provider 未发现的文章都使该账号失败，即使 Mp2RSS 也漏掉它。每个缺口必须被匹配或列为未解释缺口；“两边共享缺失”只是一种诊断标签，不能成为 pass path。检查必须能在“只少一篇”时失败，不能只检查每个账号至少有一篇。
3. **身份正确（agent 可执行）**：每个进入 shadow candidate 的微信文章 URL 都通过 canonical host/path 校验，并从真实页面或 URL 观察到与配置一致的公众号身份；同名、缺失身份、重定向归属不明或矛盾时整批 fail closed，不能用配置值补写观察值。
4. **分页与高峰容量（agent 可执行）**：真实或可回放数据证明 discovery 能从最新页追到上次成功 cursor；至少覆盖现有基准中的单账号 24 小时峰值（虎嗅 APP 18、量子位 17、AI 科技评论和 InfoQ 12），且响应失败、解析失败或中途页失败均不推进 cursor。
5. **延迟（agent 可执行）**：当前生产 pipeline 的实测与仓库配置均为每 15 分钟运行一次；该数值在实验开始前冻结，不得通过改调度放宽验收。逐篇比较 discovery 首次观察时间与 Mp2RSS 首次观察时间；每篇未解释的额外延迟不得超过 15 分钟。报告同时给出中位数、P95 和最差样本，不能只报平均值。
6. **持续稳定性（agent 可执行；扫码边界由用户完成）**：先用至少 30 天 Mp2RSS 历史做回放/回溯覆盖，再连续运行至少 7 个自然日的 live shadow，覆盖全部 14 个账号，并经历至少一次真实登录续期或明确证明观察期内无需续期。进入可能出现二维码的步骤前，agent 先以不含秘密的测试图片自主验证 `FEISHU_GENERAL_NOTIFICATION_WEBHOOK` 图片投递链路；需要扫码时，再截取二维码并通过同一链路发送。用户只完成扫码，后续状态核验由 agent 继续。
7. **失败恢复与操作面（agent 可执行）**：认证失效、明确频控、其他平台拒绝、HTTP/网络失败、响应形状变化、身份不可验证、单账号源缺失和共享会话失效在 CLI 中可区分；共享认证失败阻止所有依赖账号，账号局部失败不阻止无关账号；任何未知终态或关闭未确认均不给自动重试授权。
8. **生产不变（agent 可执行）**：shadow 阶段前后生产 `items`、`wx_mp2rss` 配置和 scheduler 行为不变；只有上述 1–7 全部通过并取得用户显式切流许可后，才执行 production cutover。切流后以该窗口内已接受且身份已验证的 candidate 为 expected set，按 canonical article identity 分别与生产 DB 和真实 `/wechat` actual set 做 14/14 逐文章比较；任一未解释缺失或重复 identity 都使切流失败并触发回滚，不能用“每账号至少可见一篇”通过。

## 取舍、rigor 与运行时成本

用户明确把“可行且稳定地替代 Mp2RSS”置于快速 demo 之上，同时授权使用一个微信公众号后台账号并愿意按需扫码。由此采用以下向量：默认 `(A0,V1)`；真实登录/只读外部 probe 为 `(A1,V1)`，限定账号、请求目的和无自动重试；生产切流、scheduler 启用与数据迁移为 `(A2,V2)`，必须另取用户显式许可并做真实链路对抗复核。R 轴理由是本地 shadow 结果可逆，但账号态请求可能造成外部状态漂移，生产切流影响真实数据；G 轴理由是 discovery 回归会造成漏文，生产阶段不可用单测替代同窗真实覆盖。

运行时成本要素如下：

- 公开网站/索引探测会产生网络流量但无新增订阅费；独立账号 I/O 使用小规模有界并发，默认并发不超过 3，并遵守源站响应和明确限流信号。
- 授权微信后台请求可能影响共享账号会话或触发平台限制。每个 probe 必须验证一个区分性假设，只发最少请求、保存 exact `ret`、不自动 retry；没有官方证据时不使用固定 24/48 小时等待，也不把请求频率提升到 scheduler 级。
- 微信文章正文页解析只对去重后的候选执行，复用现有文章抓取能力；不为已抓取 URL 重复打开浏览器。
- 7 日 live shadow 消耗墙钟时间但不要求用户持续在场；只有真实扫码边界通过飞书通知。若某路线已由直接证据否决，立即停止向它继续付请求成本。

这些成本服务于用户明确要求的稳定性证明；去掉它们会把“可替代”降级成一次性正例。生产运行频率和账号风险预算不在证据出现前拍脑袋固定，最终由实测每轮请求数、源更新频率和精确错误码决定。

## 设计边界与共同契约（L3）

### 1. 把 discovery 能力拆成三种，不互相冒充

- `title_replay`：给定已知标题/链接找回文章，只能用于补漏和诊断。
- `account_listing`：从账号身份直接列出该账号的新文章。
- `result_crawl`：从不含目标标题的结果流/站点流中持续发现并可按账号过滤。

只有 `account_listing` 或经同窗证明完整的 `result_crawl` 可以承担主发现。适配器统一返回 provider-observed 的候选、分页/游标边界、观察时间和失败层；配置身份、搜索条件或历史 Mp2RSS 条目不得伪装成 provider observation。

### 2. 先以证据筛路线，再实现生产适配器

对每个账号建立 route matrix，覆盖：独立官网/RSS/API、公开微信索引/搜索结果流、授权微信后台 `searchbiz + appmsgpublish/list_ex`、微信读书相关入口以及当前仍维护的自托管开源实现。每条路线记录：是否能发现未知新文、最早/最旧可见范围、分页、身份信号、登录要求、错误原文/码、请求量和维护状态。矩阵另有 route-inventory 表，记录两次独立搜索发现的所有具体路线、是否已执行、为何不适格、是否仍为 `pending`、是否依赖下一次自然发布/认证事件，或对应的下一项区分性检查；存在可执行但未测试、pending 或 wait-dependent 的行时不得进入负结论。

公开官网优先使用第一方列表/API；公开索引必须通过“无标题种子的新文章”反例；授权后台只做区分 `endpoint shape / account capability / session / actual rate limit` 的最小 probe。第三方项目用于发现协议和交叉验证，不直接采信其“零风控”“固定冷却”或把错误吞成空列表的语义。

路线选择是证据驱动的实现决策：选择能组合覆盖 14 个账号且满足 L2 的最小适配器集合。若证据支持多个非平凡主架构，在写生产代码前再过一次 decision-review；不得默认搜狗、私有后台或某个开源项目就是主路线。

### 3. 统一 provider-neutral 发现关系

当至少一条路线通过未知新文章 probe 后，在 `src/airadar/wechat_discovery/` 内建立最小公共契约：`discover(account, cursor, limit) -> page`，page 包含候选、下一游标、是否已追到既有游标和 provider evidence。现有 `protocol.py` 的私有后台逻辑保留为一个实验/备用 adapter，不让 `provisional_searchbiz_match` 成为所有 provider 的比较前提。

`store.py` 的下一版 schema 只在选定接口后设计并走 `$custom-review-schema`：每次 attempt 先 reservation，终态与候选同事务写入；candidate snapshot 不可变；cursor 只由完整成功页推进；共享认证错误和账号局部错误分层；compare 消费的是“经接受的 discovery relation 产生且身份已验证的 candidate snapshot”，而不是“fakeid 已验证”。不得为可从 candidate、attempt 或 cursor 唯一推导的计数/状态建立第二事实源。

### 4. 同窗比较与切流

扩展 `shadow.py`/CLI，使比较接受任一已验证 provider attempt，并按账号、时间窗和 canonical article identity 对比 `wx_mp2rss`。URL 不同但指向同一微信文章时使用可证明的 `__biz + mid + idx + sn` 身份；无法证明等价时保持缺口。比较输出始终限定到账号、attempt、provider 和时间窗，不从单窗推出长期覆盖。

shadow scheduler 只写隔离 DB/证据目录，不写生产 `items`。在 L2 通过前，`data/wechat-discovery.toml` 的私有后台开关保持关闭；适配器 cadence 从实测源更新频率和请求预算推导。切流阶段另做 production source 去重、回滚点和真实 `/wechat` 验证，不在 shadow 实验时预先修改生产拓扑。

### 5. 证据 authority 与行为演进

`evidence/authority.json` 为每个 adapter/account 切片记录两类锚点。`behavior_digest` 覆盖参与 discovery 的源码，以及影响请求/解析/身份/分页/cursor/调度/错误恢复的配置；上述行为或 provider/account mapping 变化时，只失效受影响切片，共享代码变化失效所有依赖切片。受影响切片重跑 Step 4/5，并从新行为锚点重新累计连续 7 日；未受影响账号证据保留。`verification_digest` 覆盖 expected-union 构造、canonical identity、comparison/checker、定义 green 的 contract 与 fixture；verification authority 变化只使派生 gate 失效，并先用已保存的原始七日 observation 重算，不自动作废观察期。只有新 verifier 需要此前未保存的观察时，才对受影响切片补采缺失证据。仅不定义 green 的测试/fixture、文档或 CLI 文案变化完全不失效证据，避免无关改动重收完整墙钟成本。

决定 `NO_EXECUTABLE_ROUTE_FOUND` 的负证据也带行为/验证/外部观察时间锚点；结案前必须重验已过时、来源状态可能变化或第二次搜索再次发现的决定性负路线。无法安全重验时该行改为 `pending`，结论只能是 `NEEDS_MORE_EVIDENCE`。cutover decision packet 同时绑定 `behavior_digest` 与 `verification_digest`；用户批准后、执行精确 diff 前再次核对源码、配置和证据 authority。行为漂移按影响切片重收，验证器漂移先重算派生 gate、缺原始观察才补采；任一 digest 漂移都使旧授权失效，必须重生成 packet 并重新取得授权。

## 实施步骤

### Step 1 — 固化当前证据与纠正冷却语义

涉及：`src/airadar/wechat_discovery/{protocol,status,store}.py`、`src/airadar/cli.py`、相关测试、ADR-043/044/045 与 `docs/operations/wechat-ingestion.md`。

- 完成 exact integer `base_resp.ret`、历史 provenance 和“本地安全策略、非官方窗口”的 CLI/文档闭环。
- 在隔离临时 DB 上重跑 Python/JS 测试、ruff、mypy；用真实私有 DB 只读核对 schema/integrity/历史行，不重放不可重复请求。
- Verify：历史宽分类不产生官方冷却结论；synthetic recorded `200013` 仅触发本地 policy 文案；非整数 ret 在 parser/store/raw SQLite 三层被拒绝。

### Step 2 — 建立 14 账号 route matrix，并做区分性公开 probe

产物放在本计划目录的 `evidence/`，只保存公开 URL、标题、时间、账号名、public biz、HTTP/业务状态和脱敏 request metadata，不保存 cookie、token、私有 fakeid 或 header 值。

- 从 `data/radar.db.snapshot` 动态抽取每个账号最近文章和发布节奏，建立 positive/negative controls。
- 完成第一轮路线搜索，冻结 `search-pass-a.json` 后再写入 route-inventory；对 14 个账号计算单路线与账号分区组合的覆盖，不以“没有一个统一 provider”否决可行的混合方案。第二轮在结案前使用不同 search backend 和独立 query-set，先冻结自己的 raw result manifest，再与第一轮 union。
- 对 14 个账号逐一查找第一方官网/RSS/API，并测试无需已知标题的列表、分页和发布时间；相同源站批量请求使用并发上限 3。
- 对搜狗及其他公开索引分别验证 title replay 与 unknown-new/result-crawl；搜索命中后必须走真实微信页验证身份。
- 读取并复现当前维护开源实现的关键请求/错误路径；把错误吞成空列表、失败推进 cursor 等行为作为反例，不复制。
- Verify：matrix 每个单元有直接读数或明确 `not_observed`，且至少有一个已知良好 positive control 和一个会失败的 negative control 证明仪器有区分度；route-inventory 中没有被遗漏的已知候选，每个未执行项都有可审计的不适格理由而不是“以后再看”。

### Step 3 — 对授权后台做最小区分性诊断

- 先离线核对当前 session、endpoint 参数和微信后台实际脚本/请求形状；比较 `appmsgpublish` 与仍可观察到的 `appmsg?action=list_ex&type=9` 路径，不从第三方 README 猜参数。
- 只有某个请求能区分“参数/端点错误、跨账号 capability 拒绝、认证失效、明确频控”时才发一次 read-only probe；无自动重试。进入登录动作前先用不含秘密的测试图片预检飞书图片投递；需要新登录时将二维码截图通过飞书 webhook 发送给用户，扫码后由 agent 自行验证 session。
- 保存 exact `ret`、HTTP 状态、请求形状标识和 started/finished time；敏感值只在权限 `0600` 的 gitignored session/store 中。
- Verify：成功必须返回至少一个未知候选并通过 URL biz；失败只收窄到原始证据支持的层，不把 `200013` 翻译成固定 24/48 小时。

### Step 4 — 选择并实现最小适配器集合

- 根据 Steps 2–3 的 route matrix 选能覆盖全部 14 账号的最小组合；任何账号没有 unknown-new 路线时保持 open issue，不用 title replay 代填。
- 为获选路线实现 provider-neutral page/cursor contract、错误分层、身份校验和 attempt ledger；必要时迁移 shadow schema，并按数据契约原则审查。
- 将现有私有后台 canary 作为获选 adapter 或诊断 fallback；删除/改写与 provider-neutral 语义冲突的“verified fakeid”消费，不重写可证明的历史事实。
- Verify：每个 adapter 有真实响应 fixture、malformed/partial/pagination/failure tests；同一 cursor 重跑幂等，失败不前移，重复 URL 不产生第二 candidate。

### Step 5 — 回溯容量和同窗覆盖

- 用至少 30 天生产 Mp2RSS 历史生成初始基准，再把候选 provider、账号第一方发布面和随后由真实微信页确认的同窗文章合并为 expected union；运行适配器能支持的回溯或回放。不能回溯的 provider 明确标出 live-only 范围，不用搜索存在性冒充完整性。
- 验证单账号高峰分页、canonical identity、Mp2RSS/provider 共享缺失与 provider-only 候选；逐缺口保存原因。共享缺失仍是 provider 的 coverage failure，除非能证明该文章不属于验收时间窗或目标账号。
- Verify：生成 14 账号 coverage/latency/request-count 报告；任一 expected-union 文章未被候选链路发现都使该账号保持未通过。

### Step 6 — 连续 7 日 live shadow 与恢复演练

- 在隔离 DB 和独立 scheduler namespace 中运行选定 adapter，生产 `wx_mp2rss` 保持原样；建立 `evidence/authority.json`，每次行为锚点或状态变化更新 `state.md`，每天追加带 authority digest 的 coverage 证据。
- 观察全部 14 账号的新发布、分页追赶、重复运行、认证续期、局部失败与共享失败。可控 failure injection 先覆盖 CLI/store；真实登录续期只在平台实际要求时扫码，不人为注销账号。
- 行为变化时按 authority contract 计算影响切片：受影响切片重跑 Step 4/5 并从新锚点重新累计 7 日，未受影响切片沿用原证据。verification 变化先在保存的 raw observations 上重算 gate，缺观察才补采。用共享 pagination/parser 改动、expected-union checker 改动和纯文档改动做三项对照，分别证明会失效全部依赖行为切片、只重算派生 gate、以及不会误失效。
- Verify：每个最终将切流的 adapter/account 切片都在同一最终行为锚点下连续 7 日，expected union 中每篇文章都被发现；“Mp2RSS 也没发现”不豁免。延迟、请求量、登录次数、失败恢复和 cursor 行为满足 L2；中断/重启后无漏文或重复候选。

### Step 7 — 交付审查与生产切流 gate

- 对代码走 review-gate；对 CLI 走 `$custom-review-cli-output`；对新 schema 走 `$custom-review-schema`；同步 README/CHANGELOG/operations/ADR，但避免覆盖并发 writer 的文件，必要时在独立 worktree 解决。
- 结案前执行第二次独立路线搜索，更新 route-inventory，并运行账号分区 set-cover 检查；第二轮再次发现或外部状态可能变化的决定性负路线必须重验并更新观察时间，不能沿用陈旧失败。只要仍有具体、合规且可执行的未测试路线或组合，或任一行仍为 pending/wait-dependent/需重验，结论必须是 `NEEDS_MORE_EVIDENCE`，不能输出负结论。所有路线闭合且仍无覆盖时，生成 `evidence/unreachability-report.md`，并用检查脚本拒绝缺路线、缺对照、缺缓解、缺账号组合、缺第二次搜索证据、两轮 manifest 不独立、含 pending/wait-dependent 行或含陈旧决定性负证据的报告。负对照必须覆盖“复制第一轮并改时间戳冒充第二轮”“inventory 留有等待下一次发文的 pending 行”和“第二轮再次发现但未重验的旧失败路线”，三者都应非零退出并强制 `NEEDS_MORE_EVIDENCE`。
- 向用户交付 `plans/20260816-mp2rss-replacement/evidence/cutover-decision.md`：包含 1–7 gate 的逐项证据链接、14 账号 coverage/latency/request-count 摘要、精确生产 diff、回滚点与切流后验证命令。结论只能是 `READY_FOR_CUTOVER`、`NEEDS_MORE_EVIDENCE` 或有上述闭合报告支撑的 `NO_EXECUTABLE_ROUTE_FOUND`。只有第一种才通过 `AskUserQuestion` 请求生产切流授权。两个选项的取舍与后续动作固定为：(a) 继续 shadow——保留 Mp2RSS 和生产不变，继续承担订阅费与观察墙钟时间，但降低列出的证据风险；agent 持续运行到最早一个覆盖该风险的后续 7 日窗或目标事件出现，随后自动生成下一版 decision packet；(b) 按列出的精确 diff 切流——停止继续积累切流前证据，承担 packet 中列出的已接受运行风险，执行 diff、真实 fetch、逐文章用户面核验，失败即按列出的回滚点恢复。推荐优先级写死：存在任一未解决证据风险时只能推荐继续 shadow；只有 1–7 全过且证据风险为零时才推荐切流，已接受但不可消除的运行风险必须同屏列出而不改变该判据。
- 发出 `AskUserQuestion` 前运行 agent-autonomous preflight：decision file 可读；每个 evidence link 可解析；两个 option 的收益、成本、选择后动作与下一次评审触发字段完整；gate table 与结论一致；精确 diff 能应用到当前 checkout；回滚与验证命令在无副作用模式下可解析并指向现存对象；packet 的 behavior/verification digests 与当前源码、配置及有效证据一致。任一项失败时修 packet，不把缺口交给用户。用户批准后、执行前再次核对两个 digest；漂移即停止切流，按 authority contract 重收行为切片或重算验证 gate，并重新请求授权。
- 获得显式许可后再修改生产 source/scheduler，执行真实 fetch，并以该切流窗口的 accepted candidate expected set 对生产 DB 与 `/wechat` actual set 做 canonical identity 的 14/14 逐文章比较；任一未解释 missing 或 duplicate 都回滚。未经许可不 push、不切流。

## 风险与触发响应

| 风险 | 接受理由 | 触发响应 |
|---|---|---|
| 私有跨账号列表已从频控变成 capability 限制 | 现有多项目读数支持这种可能，但缺本账号 exact-ret 正例 | 停止等待式重试，将私有后台降为诊断/fallback，继续官网与公开 result-crawl；只有新 endpoint/capability 证据才再 probe |
| 公开索引只有标题回放、结果陈旧或排序不完整 | 已有 title replay 正例但无 account-listing 证据 | 不计入主覆盖，只保留补漏；转向第一方站点或另一独立发现源 |
| 部分账号无独立官网或公开列表 | 14 账号异质，组合适配器可能是必要形态 | 用其余 discovery families 补齐；仍无 unknown-new 路线则记录为未解释覆盖缺口，不宣告完成 |
| 转载文章的 URL biz 与发布账号不同 | 全 URL biz 匹配可能产生安全假阴性 | fail closed 并单独调查发布/转载关系；没有可证明账号归属前不入 candidate，不把 mismatch 归因成 fakeid 所属错误 |
| 登录态过期或二维码需要人工扫码 | 用户已授权该账号并同意飞书收二维码 | agent 自动发送二维码、等待扫码、验证 session 后继续；不让用户手工查路径或回传 cookie |
| Mp2RSS 自身漏文，不能作为绝对真值 | 对比源也可能不完整 | expected 使用 Mp2RSS、provider、第一方发布面和已确认真实微信页的并集；共享缺失照样使 coverage 失败，provider 多出的已验证文章扩充 expected 而不是被判错 |
| 7 日内某低频账号没有新文章 | 无新样本不能证明 live discovery | 用 30 日回溯和真实历史 positive 补结构验证；live 维度继续到该账号出现新文章或找到独立 first-party listing 直接证明当前列表更新 |

## UX 契约与文档范围

本任务改变的是内部摄取与管理员 CLI，不改变当前公共网页的交互契约；在 production cutover 前不修改 `docs/contracts/ux-contract.md`。切流若改变 `/wechat` 的可见来源、延迟或空态语义，再按 docs 协议更新对应 UX contract。README、CHANGELOG、`docs/operations/wechat-ingestion.md` 与 ADR index 是最终文档同步面；公开/运维文案必须区分 observed fact、本地 policy 和未核实推断。

## 并发隔离

当前 main 工作树已有本 session 的大量未提交 WeChat discovery 改动，同时另有决策者在不同 worktree 修改部分共享文档。继续执行时：

- 本 session 只写其已拥有的 `src/airadar/wechat_discovery/`、相关 WeChat tests、新计划目录和隔离 evidence；发现同文件外部变化立即停止该写入并重读。
- 所有开发/测试 DB、scheduler、browser profile 和端口使用独立副本/namespace；真实 `data/wechat-discovery.db` 仅按明确迁移步骤备份后操作，普通测试始终使用临时 `AI_RADAR_DB`。
- README/CHANGELOG/docs index 等与其他 writer 重叠的文件在最终同步时重新判定 ownership；不能安全合并时先保留本计划内的精确 delta，不用旧全文覆盖。
- 任何取证命令块先输出并核对绝对 `pwd`，证据只来自本计划声明的 checkout/运行时。

## 默认决策与 bounded TODO

| 决策 | 默认 | 理由 |
|---|---|---|
| 方案数量 | 单 plan | 目标和验收已明确，当前需要持续执行多个 discovery families，而不是让用户在尚无证据的架构名之间挑选 |
| 生产切换前基准 | 保留 Mp2RSS 并做同窗 shadow | 用户目标是替代而非冒险停源；现有替代路线尚无未知新文章覆盖正例 |
| discovery 架构 | 先 route matrix，后选最小组合 | 当前只有 title replay 正例和私有请求失败，直接指定主 adapter 会把假设写成架构 |
| 默认 rigor | `(A0,V1)`；live auth `(A1,V1)`；cutover `(A2,V2)` | 将高强度机制只放在账号外部状态与生产切流，避免让公开只读研究承担不相称成本 |

Bounded TODO（由 implementer 在相应步骤用实时证据细化）：

- Step 2：根据每个第一方站点实际限流/robots/响应时间，把“并发不超过 3”向下收紧；不能提高到 3 以上而不重新审计源站约束。
- Step 4：根据获选 provider 的真实 pagination token 细化 cursor 字段；不得在证据出现前预建多 provider 通用 token hierarchy。
- Step 5：延迟阈值已按实验前真实 scheduler 冻结为 15 分钟；paired observations 只用于测量，不得改变阈值或调度来让结果通过。
- Step 6：若观察期内低频账号没有新发布，按风险表延长该账号的 live 观察，不把“零发布”误判为适配器失败或成功。
