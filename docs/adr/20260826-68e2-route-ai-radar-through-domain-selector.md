# ADR-20260826-68e2：AI Radar 经 status 验证的域名 selector 隔离出网

- Status: accepted
- Date: 2026-08-26
- Decision provenance:
  - T2 approved packet SHA256: `6b19a9a55d38b25d30f3474d0a9afea7d5033a25e0046f511a5abeda7ffe945c`
  - T1 final packet SHA256: `0b906e20c9e877d5e80a49a94b96b2e796623e4c57620336d50fd42cfc64f26d`

## Context

AI Radar 的生产 pipeline 曾通过 `AI_RADAR_PROXY_FILE` 读取 `~/.config/agent-proxy/current-proxy`，再把同一个地址写入标准 proxy 环境。父 Claude Code/Codex session 携带 GCP proxy 时，信任环境的 X、Ark、RSS、新闻和网页请求因而一起进入 GCP；应用自己的 provider 名称又不足以决定实际 route，因为 OpenAI-compatible SDK 同时承载 OpenAI、Ark 与 DeepSeek hostname。

用户要求路由意图保持为：Anthropic 所属域名只能经 GCP SG，线路失败时 fail closed；OpenAI/ChatGPT 与 `api.x.com` 只能经 Tencent，绝不能经 GCP；Ark、DeepSeek、RSS、新闻与普通网页默认 direct。域名分类与实际 route authority 已由 system-config T1 决策持有，AI Radar 不应复制第二份 domain table。

应用还包含多种 transport：默认信任环境的 httpx、OpenAI SDK、`urllib`、Playwright、显式 `trust_env=False` 的既有路径，以及可选的仓外 `AI_ASSISTANT_ROOT` 子进程。单独修改 pipeline shell 或单一 client factory 不能形成可验证的应用边界。

## Decision

### Status-derived selector interface

AI Radar 不读取 `AI_RADAR_PROXY_FILE`、`current-proxy` 或一个假定已导出的 `DOMAIN_ROUTER_PROXY` 环境变量。受管外部阶段先执行 `check-proxy-status --format=kv`，只有同时满足以下条件才接受 selector：

- `stored_mode=domain-routing`
- `effective_mode=domain-routing`
- `agent_proxy=http://127.0.0.1:59521`
- `policy_id=domain-routing-v1`
- `policy_sha256` 是合法的 64 位小写十六进制值
- `policy_projection=matched`
- `router_status=running`
- `gcp_sg_status=healthy`
- `tencent_status=healthy`
- `direct_status=healthy`
- `route_attribution=available`
- `overall_status=healthy`

缺字段、重复或无法解析的字段、错误 mode、错误地址、policy mismatch、非 healthy status 或 status 命令失败，都使受管外部阶段在发出请求前 fail closed。T1 名为 `DOMAIN_ROUTER_PROXY` 的值只是稳定契约常量，不是 T2 的配置源；T2 以 status 中已验证的 `agent_proxy` 为 owned client 与 managed subprocess 的唯一显式 proxy 值。

### Ambient cleanup belongs to T2

父进程的 `http_proxy`、`https_proxy`、`all_proxy` 及其大写形式不参与 selector 选择。Preflight 通过后，T2-owned client 明确不信任 ambient env；T2 启动的、仍按标准 proxy env 工作的 managed subprocess 则把六变量全部覆盖为已验证的 `agent_proxy`，同时保留 loopback bypass。T1 的 `effective_mode=custom` 只描述显式 `AGENT_PROXY_ADDR` override，不能用来解释父进程六变量不一致；六变量清理完全由 T2 实现和测试。

Preflight 通过后若某个 matched upstream 才发生故障，T1 仍只让匹配该 upstream 的请求失败，不尝试另一 proxy 或 direct。AI Radar 不从 expected route、异常类型或应用 intent 反推实际 route。

### Observable callsite closure

保证范围是 checked-in、可枚举和可测试的调用点闭包，不是“所有外部 HTTP(S) 请求”：

| 类别 | 当前闭包 | 契约 |
|---|---|---|
| httpx/feed/X | `src/airadar/fetcher/http_client.py`、`fetcher/x_api.py`、`admin/x_media_backfill.py` | External 请求使用以已验证 `agent_proxy` 构造的显式 client；loopback 保持 direct。 |
| OpenAI-compatible SDK | `provider/deepseek_chat.py`、`provider/codex_gpt_mini.py`、`eval/judge.py` 中实际创建 client 的路径 | 注入 selector-backed httpx client，由 T1 按最终 hostname 区分 OpenAI 与 Ark/DeepSeek。 |
| `urllib` | `pricing.py`、`performance/http_probe.py`、`wechat_discovery/protocol.py` 及已登记的 checked-in scripts | External URL 使用显式 selector opener；loopback health/contract probe 使用 no-proxy opener。 |
| Playwright | `wechat_discovery/login.py`、`performance/browser_probe.py`、`tests/playwright/` harness | 真实 external navigation/fetch 显式传 selector launch proxy；local/synthetic base URL 明确 no-proxy。 |
| managed subprocess | Checked-in runner 启动且只使用标准 proxy env 的子进程 | 继承 T2 清洗后的六变量与 loopback bypass；主动自建 transport 的子进程不自动进入保证。 |

实现必须维护调用点 registry，并用测试使新增但未登记的 httpx、OpenAI SDK、`urllib`、Playwright 或 subprocess 网络入口显式失败。已有 active `trust_env=False` 或自定义 transport 只能逐个分类；未分类前不在保证中，不能因进程继承 env 就声称受控。

### External AI_ASSISTANT_ROOT boundary

`AI_ASSISTANT_ROOT` 指向仓外实现。AI Radar 可以向其传入清洗后的标准 env，但无法据此控制对方主动创建的 `trust_env=False`、自定义 client、native socket 或子孙进程。某个 external root 的具体版本只有先通过 selector-compatible contract 检查与 fake-selector 测试，且 receipt 同时匹配当前 T1 `policy_id`、`policy_sha256` 与两份脚本摘要，才能纳入保证；未证明兼容时不得启用 interpret，并沿用 ADR-007 的 stage 语义记录 `skip interpret...`、exit 0，不阻断前置 fetch/curate。Receipt 是 trusted operator 的声明，AI Radar 负责严格比对身份与摘要，但不独立证明声明真实性。

### Playwright split and ADR-057 preservation

Playwright 不作为一个整体代理化。只访问动态 loopback、local web server 或 synthetic fixture 的浏览器保持 no-proxy；实际 external browser/navigation path 必须显式使用已验证 `agent_proxy` 作为 Chromium launch proxy，并在 fake selector 上观察到请求后才能宣称支持。Shell env 或 launch 参数存在本身不是请求已进入 selector 的证据。

ADR-057 的 `/img` 路径保持原样：它继续使用独立 Tencent SG 图片链路、显式 proxy 与 `trust_env=False`，proxy 缺失时 fail closed。该路径不并入本 ADR 的 macmini fetch/LLM selector，也不得被通用 client factory 改写。

### Audit authority

实际 route 与 outcome 的唯一权威是 T1 的 `~/.local/bin/agent-proxy-route-audit --format=jsonl`。AI Radar 只记录应用侧 `callsite_id`、已知 hostname、launch 类型、policy identity 与本地异常，用来证明哪个调用点尝试进入 selector；应用日志不得自称 route authority，也不得记录 path、query、headers、body、token 或 credential-bearing proxy URL。T1 无法归因时必须保留 `selected_route=unknown`/`outcome=unknown`，不能用应用 intent 填补。

## Options Considered

### 保留全局 GCP proxy 并扩充 NO_PROXY

否决。绕过表不能表达 OpenAI/X 经 Tencent、其余 direct 的三路正向政策；漏项会把非 Anthropic 流量静默送入 GCP。

### 只在 pipeline.sh unset GCP，再逐 client 指定 route

否决。它只覆盖 cron，不能覆盖直接 CLI、评测入口、Playwright 与 managed subprocess，并会在 AI Radar 复制 T1 的 hostname policy。

### 读取导出的 DOMAIN_ROUTER_PROXY 环境变量

否决。T1 只冻结了该名称对应的稳定常量，没有承诺导出它作为 T2 配置源；机器可消费接口是 `check-proxy-status --format=kv` 及其 `agent_proxy` 字段。

### 所有 Playwright 与所有 trust_env=False transport 一律送 selector

否决。它会劫持 loopback/synthetic fixture，并破坏 ADR-057 的独立 `/img` 路由。主动 transport 必须逐点分类和验证。

### 仅给 AI_ASSISTANT_ROOT 传标准 env 后宣称受控

否决。对方可主动忽略 env；该检查在“全部受控”和“部分直连”时输出相同，不能支撑保证。

### 全部 unset 后默认 direct，或由 AI Radar 自建三路 proxy

否决。前者违反 Anthropic、OpenAI/X 的固定 route；后者复制 T1 的 policy、服务、生命周期与审计权威。

## Consequences

- AI Radar 启动受管外部阶段前增加一次严格 status preflight；任一路径非 healthy 会阻止该阶段启动，这是为使用单一已验证 selector 接口接受的可用性代价。
- 父 Claude Code/Codex session 即使携带 GCP 六变量，已纳入闭包的 owned clients 与 managed subprocess 仍以 status-derived `agent_proxy` 为准。
- 新网络调用点必须登记、分类并测试；没有登记的调用点不应静默扩大“受保护”主张。
- 域名表、policy digest、selected route 与 outcome 继续由 T1 单一持有；AI Radar 只消费 identity 和实际 audit，不维护投影。
- External interpret 在 selector compatibility 未证明时会被跳过，fetch/curate 继续遵循 ADR-007 的既有 fail-safe stage 语义。

## Scope and Unverified Items

本 ADR 覆盖 AI Radar checked-in 调用点闭包、owned clients、managed standard-env subprocess、对应 fake-selector/status tests 与应用侧 callsite audit。它不覆盖 T1 router/lifecycle、GCP/Tencent tunnel、生产 `.env`/cron/DB、仓外自建 transport、任意 Claude/Codex 临时命令、Codex Desktop 或 ADR-057 `/img`。

决策被接受时尚未实现 `check-proxy-status` preflight、status-derived client factory、ambient cleanup、Playwright external proxy、callsite registry 或应用 audit；OpenAI SDK default endpoint/retry/redirect/stream、真实 external Playwright、external `AI_ASSISTANT_ROOT` compatibility、所有生产 feed 的 direct 可达性、Tencent 的 X/OpenAI 可达性与实际 audit 对账都仍待验证。T1 的公司域名清单是 best effort；未来新域在 T1 纳入前可能走 default direct，T2 不声称自动识别或补齐。

当前 MacBook 只允许离线检查和使用动态 loopback 端口的隔离测试；不得修改其 proxy 配置、网络、`~/.config/agent-proxy/mode`、`current-proxy`、六个 proxy env、59520/59521/59625 listener 或相关 live 状态，也不得在固定端口探测。真实 GCP SG/Tencent 路由、出口 IP、断线、fail-closed、mode/wrapper、quiesce、stop/uninstall 与生产切换实验全部由 root 在 macmini 执行；dgx0023 本阶段也不做 live 线路实验。
