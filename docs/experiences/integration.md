# Integration 经验

> Append-only. 跨系统 / 外部工具接口约定相关的坑点和 pattern（ai-assistant、summarize.sh、KB 写入器等）。

## 2026-06-02 复用 ai-assistant summarize.sh 时 stdout 的 result 不含 summary 正文

- Problem: interpret pipeline 零拷贝复用 ai-assistant 的 `summarize.sh` / `run.sh --save-from-batch`。直觉上会以为 `summarize.sh` stdout 的 JSON `result` 对象里含完整摘要正文，照着取 `result["summary_md"]` 会拿到空——因为 ai-assistant 的 stdout schema **故意 pop 掉 summary_md**（避免把大段正文塞进 stdout）。result 只携带 slug / save_decision / recommendation / tags 等元数据。摘要正文实际落在 `<batch_dir>/<slug>_summary.md`（`batch_dir` 由 stdout 的 `batch_dir` 字段给出）。
- Solution: 摘要正文必须从文件读，不从 stdout 取——`src/airadar/interpret/runner.py:_summarize_item` 先 `summary_payload.get("result")` 拿元数据（L487）、`summary_payload.get("batch_dir")` 拿目录（L492），再 `_read_summary_file(_summary_path(batch_dir, batch_slug))` 从 `<batch_dir>/<slug>_summary.md` 读正文（L498）。另一条省钱路径：先 `run.sh --check-url <url>` 探测；若 URL 已在 KB，返回里带 `summary_file_path`（fallback `summary_file`），直接读该文件复用已有摘要，**不重跑 LLM**（runner.py L456-484：命中即 `save_decision=True` / `kb_synced=True` / `saved=False`）。
- Applies when: 改 interpret runner、或新接入任何复用 ai-assistant `summarize.sh` / `run.sh` 的集成时——不要假设 stdout 自带正文，正文一律走 `<batch_dir>/<slug>_summary.md`；处理已可能在 KB 的 URL 时先 `--check-url` 走缓存，省一次 LLM 调用。这是 ai-assistant 的接口约定，从 runner.py 调用代码本身看不出来，需要知道上游 stdout schema 的设计。

## 2026-09-05 在自己的 shell 里手工调组件函数诊断 selector，会得出与真实入口相反的结论

`check-proxy-status` 间歇返回 1 时，我为定位是哪个组件失败，在 `zsh -fc 'source ~/.zshrc'` 里逐个调 `_gcp_tunnel_alive` / `_domain_routing_probe_gcp_component`，读到 gcp 组件 exit 1、`ssh -O check` exit 255、控制 socket 文件不存在，据此判定「GCP 隧道脱管」并进一步判定 `check-proxy-status --repair` 谎报成功。

**三条结论全是假的。** 真实入口 `_domain_routing_live_status` 在起探测前先跑 `_gcp_select_target sg-standard`（zshrc:3457），把 `GCP_CTL` 切到 `tunnel-sg-standard.sock`；而我的 shell 里 `GCP_TARGET` 是 `agent-proxy-runtime.sh:230` 的模块默认值 `sg`，`GCP_CTL` 因此指向 `tunnel.sock`——一个不存在的路径。按真实顺序补上 `_gcp_select_target sg-standard` 后重测，三个组件全部 exit 0，`ssh -O check` 用正确 ControlPath 返回 `Master running (pid=43986)`。

教训：**这些组件函数依赖调用前设置的模块级状态，脱离真实调用序列单独调用不构成对它们的观测**。要定位间歇失败，应在真实入口内加逐组件耗时/退出码留痕，而不是在外部 shell 里复现调用。判据是「我这次调用的前置状态，和真实路径调用它时一样吗」——答不出就别把读数当证据。

## 2026-09-05 egress 前置的收窄点不在退出码闸，而在 `_REQUIRED_VALUES`

`docs/issues/general.md` 记的闭合方向是「把前置收窄到 `parse_proxy_status` 用到的字段」。照字面改 `require_selector_policy()` 的 `returncode != 0` 分支是**空操作**：`parse_proxy_status` 的 `_REQUIRED_VALUES` 本身就要求 `gcp_sg_standard_status: "healthy"`、`tencent_status: "healthy"`、`direct_status: "healthy"`、`overall_status: "healthy"`——任一路由不健康它照样拒绝。

真要收窄得动 `_REQUIRED_VALUES`，而那是安全边界：attestation 的意义就是证明流量确实走了受管路由。动它之前必须先回答「某条路由不健康时 selector 会不会静默回落直连」。该问题可由 `agent-proxy-route-audit` 回答——它给出 `expected_route` / `selected_route` / `error_class`，是权威归因，比一次 curl 的 http 码强得多。

改 `src/airadar/egress.py` 会改变 `egress_implementation_sha256`、使收据失效并需重新 attestation，所以别为了一个未经验证的假设去改它。

## 2026-09-09 出口端口可配置之后，一个陈旧的覆盖会把出网静默指向死端口

- **形态**：`AI_RADAR_EGRESS_PROXY_PORT` 让端口可配置，代价是**任何一层的陈旧覆盖都会赢过代码默认**——`.env`、调用方 shell、继承来的进程环境都算。当天实测两次：① `.env` 钉着 7897 而 clash 已搬到 59527，10:30/10:45 两轮 `preflight FAIL`；② 一次 400 条重算 **400/400 全错**，`latency_ms=22`。
- **为什么难认**：失败串是 `all DeepSeek provider endpoints failed: …`，读起来像 provider 挂了。**要看 latency**——22ms 是连接被拒（本地端口没人听），~19000ms 才是网络超时。两者的上层文案一模一样。
- **判据**：怀疑出网时，先分清「代码默认」与「实际生效值」。`./run.sh egress-preflight` 的 reason 里**印着实际用的那个端口**，照它查；要看不受调用方污染的值就 `env -i PATH="$PATH" HOME="$HOME" ./run.sh egress-preflight`。cron 不继承交互 shell，所以**交互式读数为 FAIL 不等于生产 FAIL**，反之亦然——当天正是这种分叉：我的 shell 恒指 7897（失败），同一时刻 11:00 轮 `preflight OK` 且 30 分钟入库 4393 条。
- **对长跑脚本的纪律**：任何排队跑批的脚本先 `unset AI_RADAR_EGRESS_PROXY_PORT` 再调 `./run.sh`，让它走代码默认；否则调用方环境里的一个陈旧值就能把整批跑废，而且废得像上游故障。

## 2026-09-09 出网边界改写使**全部现存收据失效**——端口一立起来 interpret 就静默停产

- **状态**: 未闭合，动作在用户那边（需要跑那五个兼容性测试的人签字，agent 不能代签）。
- **发生了什么**: 出网边界从「十二字段 `check-proxy-status` 证明」换成「单一本地端口 + 经它实发一次请求」，`policy_id` 由 `domain-routing-v2` 变为 `local-egress-port-v1`，`policy_sha256` 改为按**出口端口**派生。现场 `$AI_ASSISTANT_ROOT/ai-radar-egress-contract-v2.json` 仍是旧值，必然对不上。
- **两种表现，危险的是恢复之后那种**（这正是上一条记的形态）：出口端口没人在听时 `require_selector_policy()` 抛错 → `interpret FAIL (exit 1)`，**响**；端口一旦立起来，preflight 过、收据比对失败 → `skip interpret: selector compatibility is unproven` → `cli._interpret` 返回 **0** → `pipeline.sh` 打 `=== interpret OK ===`。**pipeline 自己的成功信号在说谎**，直到 A5 在 4 小时后开火。`docs/issues/archive/closed.md` 记过同形态持续 **138 轮 / 215 篇** 未处理。
- **新的 `policy_sha256`（按出口端口，`sha256("local-egress-port-v1\n" + agent_proxy + "\n")`）**:

```
59527: f6368fdc8b82fd491cd52f3524d53eef0bbe84a92eac8135c7ee2019b831290d
  7897: 0c83bc529767b079b63bbdf5d5942aec517e417476f8dfbe74312086d3acf58f
  59521: 5bf7faef4d3512456537edff0aeeef649f7904c1868e00cf1e2dc5cb40d2c837
```

- **重新签发**（**只能由跑过那五个测试的人做**）：`uv run python -m airadar.interpret.receipt_writer --tested-policy-sha <上表中对应端口那个> ...`。写入器只校验 policy 与 implementation 摘要在写盘那一刻是活的，**它不跑那五个测试、只记录调用者的断言**——所以代签等于伪造证据。
- **换出口端口就要重签一次**：sha 依赖端口，`AI_RADAR_EGRESS_PROXY_PORT` 一改，收据立刻再次失效。这是上一条「竞态」的新形态：以前跟着另一个仓的策略文件变，现在跟着本机配置变。

## 2026-09-05 `/wechat` 停更而抓取正常时，先查 interpret 的 egress 收据闸——它 fail-closed 且干净退出 0

- **Problem**: `/wechat` 只显示有解读的文章（`JOIN wechat_interpretations WHERE save_decision=1`），所以「抓取入库正常、页面停更」这个组合的第一嫌疑不是抓取层，而是 `interpret`。该阶段的前置校验一旦不通过就**干净退出 0、一个外部脚本都不启动**，pipeline 仍打印 `=== interpret OK ===`，A1–A7 全部沉默。实测代价：2026-09-01 15:00 起连续跳过 138 轮、215 篇微信文章无解读，无任何告警，直到用户肉眼发现页面不动。
- **判据**: pipeline 日志出现 `skip interpret: selector compatibility is unproven (...)`。逐字段定位用 `airadar.interpret.runner.expected_selector_compatibility_receipt` 与 `./run.sh egress-preflight` 的当前 `policy_sha256` 比对 `$AI_ASSISTANT_ROOT/ai-radar-egress-contract-v2.json`。注意另有一个不同形态：`require_selector_policy()` 的 status 命令返回非 0 时抛 `EgressPreflightError`，那是 `interpret FAIL (exit 1)`、不是跳过。
- **闸为什么存在**: 解读会 shell 出去调 `$AI_ASSISTANT_ROOT/agents/summary-agent/{summarize.sh,run.sh}`，文章正文离开本进程、由本仓管不着的代码发到外网。ai-radar 唯一的抓手是覆写六个代理变量成受管选择器，而这只对守规矩读 env 的子进程有效（`trust_env=False`、自定义 transport、原生 socket、不受管后代全在保证之外，契约文档明写）。收据是操作者签的证明，补的正是这个洞。详见 `docs/references/ai-assistant-contract.md`「Selector compatibility receipt」。
- **已知失败形态（竞态，不是漂移）**: 策略文件在**另一个仓** `system-config:config/agent-proxy/policies/domain-routing-v2.tsv`，改它就换 `policy_sha256`。`02fce04` 那次 attestation 基于 `a5f3433`（09-02 16:38）跑完，20:36 写盘时生产已被 `236d165`（09-02 20:32）换掉 → **收据落地即失效**。
- **Applies when**: 跨仓改 domain-routing 策略时，记得 ai-radar 的 interpret 收据是它的下游消费方。这条竞态本身已由下一条经验固化的 `airadar.interpret.receipt_writer` 关闭——收据一律经它写，不要再手工编辑收据文件。

## 2026-09-05 selector compatibility attestation 与收据写盘必须是同一个身份校验入口

- Problem: 一轮 attestation 可以正确测试某个 `policy_sha256`，但操作者随后手工编辑收据前，生产 domain-routing 策略已经切换。这样生成的收据即使字段格式完全正确，落盘时也已经不代表本轮实际测试的策略；consumer fail-closed 只能拒绝它，不能修复 producer 的时间竞态。直接把收据 SHA 改成当前值可恢复生产，却把“证明跑过”退化成形式。
- Solution: attestation 结束时同时固定 tested policy SHA 与实现闭包 SHA，只允许 `airadar.interpret.receipt_writer` 写收据。Writer 先拒绝闭包变化，再在任何备份或写盘前清空 selector cache，通过 interpret 共用的 `require_selector_policy()` 重新读取生产 status；live policy 不等于 tested policy 就保持原收据与备份集合不变、非零退出并要求整轮重跑。匹配时才创建时间戳备份并同目录原子替换。负例必须模拟“测试结束后换策略”，并同时观察退出码、收据字节与备份数；只证明检查命令报错不够，因为它没有覆盖实际 writer。
- Applies when: 任何跨仓兼容收据或 attestation artifact 的有效性依赖会独立更新的生产身份时。收据内容应来自本轮实测身份，最后一次权威身份读取与写盘应收敛在同一受测入口；不要把面向人的 status stdout 反向解析成新的机器契约。
