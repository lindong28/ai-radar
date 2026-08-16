# ADR-046：X user ID 解析与 timeline 读取分轮执行

- 状态：Accepted
- 日期：2026-08-13
- 范围：`adapter="x_api"` 的账号来源首次解析身份、后续增量 timeline 读取与相关运行态

## 决策

X 官方 user timeline 只接受数字 user ID，不能把 username 直接拼入 timeline URL。每个尚无持久 `x_user_id` 的来源在本轮只调用一次 `GET /2/users/by/username/{username}`；验证响应中的 username 与配置一致、ID 为十进制后，把 ID 与该轮确定的首次 timeline 起点原子写入 source runtime metadata。下一轮才用持久 ID 调用 `GET /2/users/{id}/tweets`，继续遵守每源每轮最多一个请求、`max_results=5`、不抓回复/转推、只覆盖首次身份解析时点之前 20 分钟以及启用后的增量。

`source.url` 是配置中的 username lookup endpoint，也是 loader 校验账号身份的一部分；timeline URL 不另行持久化，而是运行时仅由验证过的 `x_user_id` 合成。Lookup 前状态、lookup 后等待首次 timeline、checkpointed 与 draining 是互斥的 exact variants，统一进入既有 runtime validator、identity owner、reload preservation 与 SQL CAS 边界。Lookup 轮保存原始冷启动起点，避免下一轮才读 timeline 时窗口向后滑动。

帖子 URL 使用与 handle 无关的 `https://x.com/i/web/status/{post_id}`；每条 timeline post 的 `author_id` 必须等于持久 `x_user_id`，否则整页拒绝且不写 item/checkpoint。配置中的 username 仍是 lookup 输入、公开作者与 homepage 的权威身份；系统不额外付费周期 lookup，不承诺自动追踪 handle 改名。维护者或来源池更新同 slug 的 username 时，sync 将其视为 identity 变化，清空 user ID 与 cursor 并重新解析。

## 被否方案

- 每轮 username lookup 后再读 timeline：每轮重复 User Read，并把每源上限提高为两个请求。
- 首次同轮 lookup + timeline：虽然只多一次初始化请求，但破坏统一的每轮上限，并让两个远端响应的失败与持久化边界耦合。
- 一次批量解析全部账号：HTTP 次数少，但 User Read 资源费不降，且引入跨源批量状态写入；首次部署会一次读取全部账号。
- 在 `sources.toml` 手工维护 user ID：把同一远端身份拆成 username 与 ID 两份独立配置事实，来源变动时容易漂移。
- 改用 recent search 的 `from:username`：端点语义、结果口径与额度不同，不能替代 user timeline。

## 证据、取舍与验证边界

2026-08-16 使用重新生成的 App Bearer Token 对 OpenAI 完成了单账号有界实测：username identity lookup 与 20 分钟 timeline 请求各发送一次，均返回 HTTP 200；该窗口没有帖子，系统仍提交了合法的 `checkpointed` 时间锚。仓库中的离线 fixture 另覆盖合法 lookup、重开 SQLite 后保留 user ID、空/非空 timeline、分页 draining 与最终 checkpoint。live 结论由 `scripts/probe_x_source.py` 的非敏感收据给出；本次只证明单账号 identity/timeline 连通与持久化，不证明实际帖子读取。

首次正常全量调度仍会让当前 109 个 X 源分散地各读取一个 User 资源，下一轮起才读 timeline。公开 source inventory 的 pending trigger 必须区分“等待 identity lookup”和“已有 ID、等待首次 timeline”，逐源 fetch summary 与持久 `blocked` 状态共同承担 lookup/timeline 失败可观察性。109 个账号是否全部仍存在、公开且可读取尚未 live 验证；不得把单账号结果外推成全量已接通，也不得在没有当日官方价格证据时写死费用估算。

若 X 账号改 handle 而配置未更新，timeline 仍以稳定 user ID 读取且帖子 URL 不受影响，但公开作者与主页可能暂时陈旧；这是已知作用域。来源配置更新 username 后会重置身份状态。分页 token 生命周期、生产调度安装状态与 109 个账号的 live 可用性仍需部署后观察。
