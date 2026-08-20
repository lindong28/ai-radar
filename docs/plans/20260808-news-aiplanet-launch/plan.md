> **Archive status**: 已归档，**未收尾**。执行过程产物 `state.md` / `journal.md` / `pending-remediation.md` 按长任务协议不入档。
> **中止点**（判据＝`state.md` 残留 open 项）：公网 `news.aiplanet.live` 已 HTTPS 上线（P4 done），P0/P1-1..P1-5/P2-1/P2-2 done；**未完成**：P0b-5（生产启动形态复验）、P3-timer（enable 同步 timer）、G2（同步频率定值）、P3b（上线前最小告警）、G5（告警手机送达确认）、G3（体验确认 review packet）、G4（sjtu 下线二次确认）、P6（文档同步与收口）pending；P0b（真库 FTS5 probe 判据 T-c）与 P3（增量同步实测 + 蓝绿 operationalize）partially-done；P5（裸域与 sjtu 下线）in-progress。
> 当前生产契约见 [ADR-039](../../adr/039-route-news-through-edgeone-dns-only-cname.md)、[ADR-042](../../adr/042-isolate-production-deploy-commit-from-local-main.md)、[ADR-050](../../adr/050-allow-versioned-data-configs-through-code-deploy.md) 与 [operations/services.md](../../operations/services.md)。以下为原 plan 正文，未修改。

# news.aiplanet.live 正式上线：生产部署迁移到腾讯云国内服务器

> **Long-task mode**：本 plan 按 `~/.claude/references/long-task-protocol.md` 执行。开工前先读同目录 `state.md` 与 `journal.md`；每完成一个步骤更新 `state.md`，每轮执行追加 `journal.md`。交付前按该协议做完整验证。

> 修订于 2026-08-08（**r3**）。已吸收独立 Codex reviewer 两轮共 19 个 violation 集群（r1 的 11 条 + r3 的 8 条，含一次 abstraction reset）、主 session 后续 probe 的 5 项事实更正，以及用户 8 项决策。ICP 备案已通过（沪ICP备2026017013号），公网站点从"审核期间故意下线"状态转为正式上线。

## 输入

本 plan 是实施与评审的唯一入口。上游工件：

| 上游 | 路径 | 承载什么 |
|---|---|---|
| 前序迁移 plan | `plans/20260719-tencent-migration/plan.md` | 架构选型（读写分离）的历史论证。**其"待办"清单已被本 plan 取代，不要并行执行**；其"跨版本 FTS5 malformed"的归因已被本 plan 的实测部分推翻（见「现状事实 · FTS5 跨版本」） |
| 既有同步脚本 | 同目录 `sync-db-to-tencent.sh`、`db-sync-manifest.py`、`test-sync-db-to-tencent.sh` | 已验证可用的实现，P1 将其迁入 tracked 位置并改造 |
| UX 契约 | `docs/contracts/ux-contract.md` | 用户可感知行为的验收契约，本 plan 有投影 |
| 跨仓库 | `~/research/sjtu-aaa/AGENTS.md`、其 `docs/operations/services.md` | P5 要下线的另一个项目，**已读**，事实见 P5 |

---

## L1：最终产物与使用方式

**最终产物**：部署在腾讯云上海服务器、通过 `https://news.aiplanet.live` 对中国大陆公众服务的 AI 资讯站点，以及支撑它持续运行的一套 tracked 部署运维设施。

| 使用者 | 拿到什么 | 用来做什么 |
|---|---|---|
| 公众读者（大陆为主） | `https://news.aiplanet.live` | 浏览 AI 精选资讯。这是站点第一次真正对公众开放 |
| 维护者（用户本人） | `git push tencent main` 发布链路 + `deploy/server/status-server.sh` | 在 Mac 上开发迭代、推上生产；出问题时能回答"生产跑的是哪个 commit / 哪份快照"并回滚 |

**运行边界**：

- **Mac（上海 M4）**：开发迭代 + pipeline（抓取 / LLM 打分 / 写 `radar.db` 主库）。现有 launchd + cron 调度不动。
- **服务器（Ubuntu 24.04, 111.229.134.9, 2C4G/3.6GiB）**：serve + `radar.db` 只读副本 + nginx，承载全部用户流量。
- **单向数据流**：Mac 主库 → 服务器副本。

> **"服务器只读"是本 plan 要建立的性质，不是现状。** 当前服务器 serve 未设 `AI_RADAR_PRE_MIGRATED_DB`，每次启动都会跑 migration 并重写副本的全文索引。G=standard 的定档依赖"副本可从 Mac 重建、服务器不产生独有数据"，该依据只有在 P2 装上 pre-migrated 之后才成立。

**明确不做**：pipeline 不迁服务器；不做多机高可用；EdgeOne CDN 接入拆为独立 plan。

---

## 取舍偏好（用户已拍板）

| 维度 | 选择 | 三层体现 |
|---|---|---|
| 上线速度 vs 边缘性能 | 先上线，CDN 后置 | L1 排除 EdgeOne；L2 延迟锚在源站直连（实测 24ms） |
| 内容新鲜度 vs 服务器负载 | **修根因换取两者兼得** | 用户在得知同步成本 94% 来自 migration 003 后，明确选择「纳入本次、先修再上线」 |
| 工程统一性 vs 改动风险 | 降低风险优先 | 独立轻量 server 服务层，不改 975 行 launchd 脚本 + 1499 行契约测试 |
| 生产可追溯性 vs 发布简便 | 可追溯优先 | bare repo + post-receive，生产状态是可查询的 active release |

---

## rigor 契约

用户已确认向量。默认取共同低基线，per-phase override 只升不降。

| 范围 | 向量 | label | 轴 R 理由（→A） | 轴 G 理由（→V） |
|---|---|---|---|---|
| **默认** | `(A0,V0)` | light | 仓库内编码，可逆本地改动 | 回归由现有测试套件捕获 |
| **P1-4**（migration 003 改造） | `(A1,V1)` | standard | 改 schema 迁移路径，影响所有既有库；本地可回滚但会改写真实 DB | 搜索结果丢失是用户直接可感知的功能退化 |
| **P1-2 authority 定义 / P2-1 安装 / P3 timer enable** | `(A2,V1)` | max | **定义并启用会长期自动改写生产状态的 authority**。"上线前安装"不改变它此后控制生产切换的性质——这三处才是真正的不可逆面 | 同下 |
| **P2 / P3 / P3b 的其余部分** | `(A1,V1)` | standard | 服务器状态漂移、DB 替换，站点尚未对外 | 决定真实用户看到的数据 |
| **P4 / P5** 公网切流 | `(A2,V1)` | max | 生产切流、DNS 变更、跨仓库停用 sjtu 生产服务——不可撤销外部副作用 | 影响真实用户但非资金/安全/数据完整性零容忍 |

### A2 的落地形态（职责分离）

不是"独立 reviewer **或**用户确认"二选一，两者管不同的事：

| 角色 | 审什么 |
|---|---|
| 独立对抗 reviewer | mutation target、将执行的命令、scope 边界、before snapshot 是否完整、rollback packet 是否可执行 |
| 用户 | 只对真正属于他的决定授权：下线 sjtu（G4）、体验是否达标（G3）、push 与 main 整合许可（G0a/G0b） |

**审批失效语义**（防无界重审）：每份 A2 approval 绑定该 unit 的 exact before snapshot + mutation scope + rollback packet。三者任一改变 → 只使**受影响的那个 unit** 失效并重审。不改变 PASS/FAIL 或 authority 的文档、展示、诊断类修改只重跑 impact verify，不触发重审。

- G3 绑定 `deployed SHA + serving snapshot ID + 公开 URL`；仅这些用户面变化才失效。
- G4 绑定两个 DNS record ID + tunnel launchd label + sjtu 服务清单；仅其 scope 变化才失效。

### V1 的落地形态

P2 / P3 / P3b / P6 每个 phase 设**一个 single-reviewer milestone gate**（不是每条命令）。P4 / P5 按 authority unit 合并审查。每个改变用户可见行为的 unit 单独验证被改行为。

**A2 施于 authority 定义，不施于其下的每轮 payload**：P1-2 / P2-1 / P3-timer 的对抗 reviewer 只审 **authority 定义本身**——descriptor 模型、mutation scope、回滚路径、fail-closed 语义。定义冻结后，在该 authority 下运行的每一轮定时 DB 同步只做廉价 conformance，**不逐轮对抗审查**（那是 over-rigor）。

**增量复验语义（BT-01）**：不改变被审行为、PASS/FAIL 判定或 authority 的 docs / tests / diagnostics 修改，**只重跑受影响的 verify**；只有改变被审行为或 gate 定义本身，才使对应 milestone 的 reviewer 批准失效。全 phase 重跑的成本必须保持有界——否则每次文档笔误都会触发整轮重审。

### 对称校验

默认 `(A0,V0)` 省去的机制：P1-1/P1-3 的模板与页脚编写属可逆本地改动，失败会在 P2 部署时立即暴露且无生产影响 → 不需要 A1。反向看 P1-4 没有过度：它改写所有既有库的索引，V1 是必要而非仪式。

---

## 现状事实（本 session 实测，implementer 不必重新调研）

### 本地 Mac

| 事实 | 值 |
|---|---|
| serve | launchd `live.aiplanet.ai-radar.serve`，绑 **0.0.0.0:8010**。plist 注释：备案审核期间**故意**不占 8000，使 tunnel 回源落空。**502 不是故障** |
| tunnel | launchd `live.aiplanet.ai-radar.tunnel`，cloudflared `c01ac79f-0d39-4bf4-9475-0c4e526d5f84`；ingress `aiplanet.live→127.0.0.1:8000`、`sjtu.aiplanet.live→localhost:8100`、`http_status:404` |
| pipeline | user crontab `*/15`，正常 |
| 主库 | `data/radar.db` 2.28 GB / 558,030 页 × 4096B，items=39,458 |
| **写库的解释器** | **uv standalone CPython 3.13.12 → sqlite 3.50.4**（`uv run python`）。这是决定 FTS5 on-disk 格式的那一个 |
| 系统 sqlite CLI | 3.51.0（`.backup` 用它，但页级拷贝不重写索引，不影响格式） |
| 系统 python3 | homebrew 3.14.6 → sqlite 3.53.3（与本 plan 无关） |
| rsync | **仅 Apple openrsync 2.6.9 / protocol 29**，无 GNU rsync |
| python 版本锁 | **仓库无 `.python-version`**，`pyproject.toml` 只有 `requires-python = ">=3.12"` |
| alert / performance-probe | 均 `not installed` |

### 腾讯云服务器

| 事实 | 值 |
|---|---|
| 系统 | Ubuntu 24.04.4, 2 vCPU, 3.6 GiB RAM, 69G 盘（已用 10G，余 56G） |
| 代码 | `~/ai-radar` 是 **Jul-19 的非 git 快照**（无 `.git`），落后 main |
| serve | systemd `ai-radar-serve.service`，`uv run … serve --host 127.0.0.1 --port 8000`，跑了 16 天，healthz 正常。**未设 `AI_RADAR_PRE_MIGRATED_DB`** |
| nginx | `sites-enabled/ai-radar`，`server_name aiplanet.live _`，`listen 80 default_server`，反代 80→8000 |
| DB | `data/radar.db` 1.63 GB，停在 Jul-22，items=32,740 |
| `.env` | **不存在**（`AI_RADAR_SITE_DOMAIN` 未设） |
| 现有 venv | 系统 Python **3.12.3 → sqlite 3.45.1** |
| **已安装（本 session）** | `uv python install 3.13` → **CPython 3.13.14 → sqlite 3.53.1**。未触碰现有 `.venv`，serve 未受影响 |
| rsync | 3.2.7 / protocol 31 |
| 出网 | pypi ✅ DeepSeek ✅ astral.sh ✅ **GitHub ❌** |
| 公网 | **80 可达 healthz 24ms**；**443 connection timeout（安全组未放行，已实测）** |
| metadata | instance `ins-3o0q3359`，region `ap-shanghai` |
| linger | `Linger=no` → 必须用 system 级 systemd 单元（现状已是） |

### 前置可行性（本 session 已核验，implementer 不必重查）

| 项 | 结论 | 备注 |
|---|---|---|
| certbot | ✅ apt `certbot 2.9.0` + `python3-certbot-nginx 2.9.0` 均可得；`snap` 也可用作备选 | P4 无安装风险 |
| 服务器 sudo | ✅ **免密**（`sudo -n true` 成功） | `install-server.sh` 可非交互运行，不需要在脚本里处理密码提示 |
| nginx | 1.24.0 (Ubuntu) | 见下方 default_server 陷阱 |
| Mac GNU rsync | ✅ brew 5.1.14 可用，`rsync 3.4.4 (bottled)` 未安装 | 见下方 brew 代理陷阱 |
| **依赖供应链** | ✅ **GitHub 不通不影响发布**：`uv.lock` 73 个包全部来自 `mirrors.aliyun.com/pypi/simple`，**零** git/url/path 来源；服务器实测 `uv sync --dry-run` → *Resolved 74 packages, Would make no changes* | 阿里云镜像在境内，对这台机器反而更快。post-receive 里的 `uv sync` 无外网风险 |
| **D9 的 lock 兼容性** | ✅ 同一份 `uv.lock` 当前同时服务 Mac 的 3.13.12 与服务器的 3.12.3 | 说明切到 3.13 不需要重解析依赖，D9 的迁移面比看上去小 |

**⚠️ 陷阱一：nginx `default_server` 重复声明。** `sites-available/default` 与 `sites-available/ai-radar` **都**声明了 `listen 80 default_server`。当前只有 `ai-radar` 在 `sites-enabled/`，所以不冲突。但 P4 新增 vhost 或误启用 `default` 时，nginx 会以 *duplicate default server for 0.0.0.0:80* 拒绝启动。**P4 每次改动后必须 `nginx -t` 再 reload**（plan 已要求），且新 vhost 不得再带 `default_server`——444 那个兜底 server 块是唯一持有者。

**⚠️ 陷阱二：`brew` 在本机是带代理的 alias**（`HTTP_PROXY=http://127.0.0.1:59520 … /opt/homebrew/bin/brew`）。非交互 shell 拿不到 alias 也拿不到代理变量，`brew install rsync` 会以网络错误失败，且报错方向会误导向"网络不通"。**一次性安装必须在交互式 shell 里做**，或显式带上代理变量。（这正是 CLAUDE.md「非交互 Shell 里执行命令」那条描述的失败形态。）

**PATH 备注**：`/opt/homebrew/bin` 不在 PATH 前段，装完 GNU rsync 后裸 `rsync` 未必解析到它。这正是 P1-2 用显式 `AI_RADAR_RSYNC`（默认 `/opt/homebrew/bin/rsync`）+ protocol 断言、而不依赖 PATH 的原因——该设计已被此事实验证为必要。

### Cloudflare zone `aiplanet.live`

DNS 读写权限**已实测**（建 TXT 记录成功并清理）。

| 记录 | 指向 | 处置 |
|---|---|---|
| `aiplanet.live` CNAME | tunnel `c01ac79f` | **P5 删除** |
| `sjtu.aiplanet.live` CNAME | tunnel `c01ac79f` | **P5 删除**（跨仓库） |
| `openclaw` / `server` CNAME | tunnel **`694806d9`** | **不同 tunnel，不动** |
| `www` CNAME | squarespace | 不动 |
| MX ×2 / TXT（SPF、DKIM ×2、DMARC） | Cloudflare Email | **不动，邮件依赖** |

### DB 同步成本（实测，推翻前序 plan 的记载）

前序 plan 记为"每次 scp 全传 ~2G"，并把它当成迁移的固有代价。实测拆开后，真正的成本结构是：

**两次独立测量，跨一个 pipeline 轮次，`.backup` 快照逐页比对（页 4096B）：**

| 区间 | 新增 items | 需传输 | 占全库 |
|---|---|---|---|
| 13:20 → 13:33 | 3 | 313.1 MiB | 14.35% |
| 13:49 → 14:04（干净对照） | 4 | **313.6 MiB** | **14.38%** |

两次几乎相同 → **这是每轮的固定开销，不是偶发**。脏页归属：

| 对象 | 每轮脏页 | 说明 |
|---|---|---|
| `items_fts_data` | 213.9 MiB | **100% 重写** |
| `items_fts_content` | 80.5 MiB | **100% 重写** |
| `items_fts_idx` + `docsize` | 1.7 MiB | 100% 重写 |
| **FTS 小计** | **296 MiB (94%)** | |
| `items` + 索引 + curated | **~4.7 MiB** | 真实业务增量 |

**根因**：`src/airadar/migrations/003_add_fts5_search.sql` 第 2 行自述 *"Rebuilt idempotently on every migrate() via DROP ... IF EXISTS"*，其后 `DROP TABLE IF EXISTS items_fts` + `CREATE VIRTUAL TABLE` + 全量 `INSERT ... SELECT`。调用点：

| 调用方 | 位置 | 频率 |
|---|---|---|
| pipeline | `src/airadar/fetcher/runner.py:426` | **每 15 分钟** |
| serve 启动 | `src/airadar/web/app.py:474`（`AI_RADAR_PRE_MIGRATED_DB != "1"` 时） | 每次进程启动 |

推论：服务器 serve 那个"冷启动 30–40s"主要是在重建 296 MiB 索引，不是预热 COUNT 缓存。修好后 serve 重启会明显变快，蓝绿切换更稳。

**修 003 的收益：314 MiB → ~5 MiB/轮（~60 倍）**，使 15 分钟一同步可行。用户已决定纳入本次上线（P1-4）。

### FTS5 跨版本：真库实测结论（P0b 已执行，结果如下）

前序 plan 的归因**成立**，但判据必须比它写的更严。两级实测：

**一级（3000 行 fixture，真实 003 schema 含 `tokenize='trigram'`）**：服务器 3.45.1 与 3.53.1 都读得正常，parity 一致。**复现不出问题——fixture 规模不足以代表真库的 FTS5 段结构。**

**二级（真实 2.28 GB 主库 `.backup` 快照，scp 传输耗时 3m11s）**：

| reader | `PRAGMA integrity_check` | FTS5 integrity-check | `MATCH OpenAI` | `MATCH Anthropic` | `items` |
|---|---|---|---|---|---|
| Mac writer / sqlite **3.50.4** | — | — | 3301 | 2072 | 39458 |
| 服务器 uv standalone 3.13.14 / **sqlite 3.53.1** | **ok** | **ok** | 3301 | 2072 | 39458 |
| 服务器现有 venv / **sqlite 3.45.1** | **malformed inverted index for FTS5 table main.items_fts** | **FAIL: database disk image is malformed** | **3301** | **2072** | 39458 |

**两条结论，第二条比第一条重要：**

1. 版本偏斜在真库上确实致命——3.45.1 判 malformed，3.53.1 完全正常。→ **服务器必须用 uv standalone python（D9 已据此翻转）。**

2. **⚠️ 损坏的索引仍然返回完全正确的查询结果。** 3.45.1 那一行 integrity 全 FAIL，但三个查询的命中数与 Mac 基准**一字不差**。所以"搜索能用 / 命中数对得上"**不能**作为索引健康的判据——它在索引健康与索引损坏两种情况下输出相同，是典型的伪判据。
   → **任何 FTS 验收都必须同时包含 `PRAGMA integrity_check` 与 FTS5 专项 integrity-check，命中数 parity 只是补充维度，不能单独使用。** 本 plan 的 L2-4、P0b、P1-2 apply 判据均已按此加严。

**探针词的两个陷阱**（分词器是 `tokenize='trigram'`，三元组下限 3 字符）：

- `MATCH 'ai'` **恒返回 0**（2 字符）——reviewer 原提案用它当"FTS 可用"判据会永远判失败。
- **CJK 同样受限**：`MATCH '模型'` 也恒返回 0（2 字符）。实测三端一致为 0。
- 判据必须用 ≥3 字符的词（`OpenAI` / `Anthropic` 等），且与 Mac 基准数值比对。

### `AI_RADAR_PRE_MIGRATED_DB`：存在但两条路都是隐藏的

| 入口 | 位置 |
|---|---|
| 环境变量 `AI_RADAR_PRE_MIGRATED_DB=1` | `src/airadar/web/app.py:37,474` |
| CLI `serve --pre-migrated-db` | `src/airadar/cli.py:566`，**`help=argparse.SUPPRESS`（不出现在 `--help`）**；`cli.py:364-378` 用它设置同名环境变量 |

`git grep PRE_MIGRATED -- docs README.md .env.example` 无命中——一个决定"进程会不会改写你的数据库"的开关只存在于源码里。P6 要补文档。

---

## 目标架构

```
                    用户（中国大陆）
                          │  https://news.aiplanet.live
                          ▼
              Cloudflare DNS（灰云 / DNS-only，不代理）
                          │  A → 111.229.134.9
                          ▼
        ┌─── 腾讯云上海 2C4G ────────────────────────────┐
        │  nginx :443 (Let's Encrypt) / :80 (ACME + 301) │
        │     ├─ news.aiplanet.live → active port        │
        │     └─ default_server     → 444                │
        │  systemd ai-radar-serve@<port>（蓝绿两实例）    │
        │      环境固定 AI_RADAR_PRE_MIGRATED_DB=1       │
        │  active release = {SHA, snapshot ID, port}      │
        │  data/radar.db（只读副本）+ radar.db.basis      │
        │  bare repo ~/ai-radar.git ← post-receive       │
        └────────────▲─────────────────▲─────────────────┘
                     │ git push        │ GNU rsync 增量（delta 对 basis）
        ┌────────────┴─────────────────┴─────────────────┐
        │  上海 Mac  pipeline cron */15 → radar.db 主库   │
        │            launchd serve :8010（内网预览，保留）│
        │            cloudflared tunnel ← P5 停用         │
        └─────────────────────────────────────────────────┘
```

---

## 阶段计划

### P0 — 用户放行安全组 443（阻塞 P4）

**用户行动项**：腾讯云控制台 → 轻量应用服务器 `ins-3o0q3359` → 防火墙 → 放行 TCP 443。

已穷尽代办路径：无 `tccli`、无 `TENCENTCLOUD_SECRET_*`（全局搜过）；metadata API 不含防火墙管理；经用户授权附加 Chrome Dev（CDP 9222）后确认**控制台未登录**，需本人扫码。登录页已开在 Chrome Dev 标签 `tcfw`，登录后会自动跳到该实例防火墙页。**用户登录后 agent 可完成放行规则本身。**

**Verify**：服务器临时监听 443，Mac 上 `curl --noproxy '*' -m 10 http://111.229.134.9:443/` 返回非 `000`。本 session 已用此法证实当前未放行（阴性对照成立）。

P0 不阻塞 P0b–P3。

---

### FTS acceptance triple（全 plan 唯一的 FTS 健康判据，各处引用不复述）

任何声称"FTS / 搜索没问题"的地方，**必须三条同时成立**。少任何一条都不算通过——实测证明单靠 parity 会在索引 malformed 时判通过（服务器 sqlite 3.45.1 报 malformed，三个 MATCH 却返回与 Mac 一字不差的命中数）。

| # | 条件 |
|---|---|
| T-a | `PRAGMA integrity_check` = `ok` |
| T-b | FTS5 专项：`INSERT INTO items_fts(items_fts) VALUES('integrity-check')` 不抛错 |
| T-c | `items` 与 `curated_items` 行数、以及**全部** Mac 基准 `>0` 的 **≥3 字符**查询词命中数，逐项与 Mac 基准相等 |

**探针词纪律**：分词器是 `tokenize='trigram'`，任何 <3 字符的词恒返回 0——`ai`、以及 CJK 的 `模型` 都是死探针，**不得作为判据**。已知可用基准词：`OpenAI`（3301）、`Anthropic`（2072）。T-c 需要至少一个额外的 ≥3 字符、Mac 基准 `>0` 的词，实施时选定并记入 receipt。

引用本 triple 的位置：P0b 判据 4、P1-2 apply 与 standby readiness、P1-4 的 rebuild 路径、P3 副本正确性、P4 上线验收、L2-4。

---

### P0b — SQLite/FTS5 可行性 probe（定稿前 gate，`(A1,V1)`）— 判据 1–3 已过，判据 4 **部分完成**

**已执行**（结果见「现状事实 · FTS5 跨版本」）：真实 2.28 GB 快照上，服务器 uv standalone 3.13 / sqlite 3.53.1 通过 T-a、T-b，以及 T-c 的一部分（`OpenAI` 3301、`Anthropic` 2072、`items` 39458 均与 Mac 相等）。

**尚缺，补齐后才能标记判据 4 完成**：

- 第三个 ≥3 字符、Mac 基准 `>0` 的查询词（已测的 `模型` 是 2 字符死探针，不计数）
- `curated_items` 行数比对（Mac 基准 292813，服务器侧未测）

**判据 5** 仍待 P2 环境就绪后补齐（以 `serve --pre-migrated-db` 生产启动形态起进程，从 HTTP API 复跑完整 triple），它是进入 P3 的 gate。

结论已并入 D9（服务器必须换 uv standalone python）与 P1-2（apply 判据加严）。下方保留判据定义供 implementer 复跑。

> **遗留物**：probe 用的真库快照留在服务器 `/tmp/real-probe.db`（2.3 GB）与 `/tmp/fts-fixture.db`（9.6 MB），供判据 5 复用。`/tmp` 会被系统清理，判据 5 若发现文件已不在则重新传一次。**判据 5 完成后由 implementer 删除这两个文件**，不要留成孤儿。

输入是 Mac 用 `.backup` 生成的**真实主库**不可变快照，传到服务器持久 scratch 路径（非 `/tmp`，避免被清理）。

判据（全部要过）：

1. 服务器用**实际 serve runtime 的解释器**（不是独立 `sqlite3` CLI）打开该快照。
2. **T-a**：`PRAGMA integrity_check` = ok。
3. **T-b**：FTS5 专项 `integrity-check` 不抛错。
4. **T-c**：`items` 与 `curated_items` 行数、以及至少三个 **≥3 字符且 Mac 基准 `>0`** 的查询词，逐项与 Mac 基准相等。已确认可用：`OpenAI`(3301)、`Anthropic`(2072)；**第三个待选定**。`模型` 是 2 字符死探针（trigram 下恒 0），**不得计入**。
5. 用生产启动形态（`serve --pre-migrated-db`）以该快照起进程，从 HTTP API **重跑完整 FTS acceptance triple**——证明 serve 这条路径也读得对，而不只是裸 sqlite。

判据 2–4 合起来即「FTS acceptance triple」。**全部通过后**才可把"免 rebuild 可行"写入现状事实并继续；失败按 Risks R1 的机械 trigger 处置。

> 本 gate 顺带预演了 P3 的全量传输链路，其耗时数据直接用于 G2。

---

### P1 — 仓库内工程（默认 `(A0,V0)`，P1-4 为 `(A1,V1)`）

#### P1-1 独立的 Linux server 服务层

新建（不改 `install.sh` / `deploy/lib/services.sh` / 现有契约测试）：

| 文件 | 职责 |
|---|---|
| `deploy/systemd/ai-radar-serve@.service` | 模板单元，`%i` = 端口 |
| `deploy/systemd/ai-radar-db-apply.service` + `.timer` | 定时同步/应用 |
| `deploy/server/install-server.sh` / `status-server.sh` / `uninstall-server.sh` | 幂等安装、状态、卸载 |
| `deploy/nginx/news.aiplanet.live.conf` | vhost 模板（ACME location + TLS + default_server 444） |

约束：

- **system 级单元**（`Linger=no`）。
- 不写死路径：`EnvironmentFile=/etc/ai-radar/server.env` 提供 `AI_RADAR_HOME` / `AI_RADAR_SITE_DOMAIN` / `AI_RADAR_ICP_BEIAN` / **`AI_RADAR_PRE_MIGRATED_DB=1`**。现有单元把 `/home/ubuntu/...` 写死，重装无法复现。
- `%i` 只给端口，**DB 路径由 active release state 决定**（见 P1-2 invariant），不靠实例名区分。

**内部 verify**：`bash -n` + shellcheck；新增 `tests/test_server_deploy_contract.py` 断言模板含 `%i`、含 `EnvironmentFile`、**含 `AI_RADAR_PRE_MIGRATED_DB=1`**、无硬编码 `/home/ubuntu`，且 install/status 的服务清单一致；dry-run 路径可在 Mac 上测试。

#### P1-2 DB 同步链路：per-slot release descriptor + 持久 journal `(A2,V1)`

> **rigor 说明**：本节**定义** authority——它规定此后每一轮自动 DB 切换的行为。定义 authority 属 A2（见 rigor 契约）。定义冻结之后，其下每轮的定时 DB payload 只做廉价 conformance，不逐轮对抗审查。

**共享抽象（取代先前的裸三元组）**——先前写法只描述 active 状态，答不出 standby 绑哪个库、切一半崩了怎么收敛、代码与快照 schema 不匹配怎么拒绝，因此重设为 per-slot descriptor：

> **每个 slot（8000 / 8001）各持有一份完整的 release descriptor**：
> `release ID + deployed SHA + snapshot ID + DB 绝对路径 + schema contract ID + port`
>
> - **standby 从自己的 candidate descriptor 取代码与 DB**，绝不从 active pointer 取 DB——这是"standby 误读 active 库"这类错误在结构上不可能发生的前提。
> - accepted-snapshot receipt 带 **schema contract ID**；代码部署与 DB apply **都必须校验 code ↔ snapshot 兼容**，不兼容即拒绝（pre-migrated 的 serve 不会自己迁移，schema 不匹配只会静默出错）。
> - 切换过程写**持久 journal**，状态机 `prepared → switched → committed`；进程启动前先做 **reconciliation**。
> - **任何 crash 之后必须收敛到"完整的旧 release"或"完整的 candidate"，不允许留下混合状态**（例如 nginx 已 reload 指向新 slot、但 active state 尚未持久化）。
> - **只有 committed 之后**才推进 `basis`、`.deployed-sha` 与 accepted receipt。
> - 代码部署与 DB apply 共用一把排他锁。

**内部 verify（针对本抽象，不是针对某次切换）**：在三个位置注入失败——`nginx reload` 前后、state commit 前后、旧实例停止前后——每次都断言最终的 `active tuple / nginx upstream / 实际运行实例 / basis / .deployed-sha` **五者互相一致**，且等于"完整旧 release"或"完整 candidate"之一。

脚本迁入 tracked 位置（`plans/` 是会被清理的工作区，按持久资产规则不能是唯一副本）：

| 从 | 到 |
|---|---|
| `sync-db-to-tencent.sh` | `deploy/sync/sync-db-to-server.sh` |
| `db-sync-manifest.py` | `deploy/sync/db-sync-manifest.py` |
| `test-sync-db-to-tencent.sh` | `tests/test_db_sync.sh` |
| **新建**（非迁移，scratchpad 不是持久来源） | `deploy/sync/pagediff.py` |

改造点：

1. **GNU rsync 强制**：`AI_RADAR_RSYNC` 环境变量，默认探测 `/opt/homebrew/bin/rsync`；开头断言 protocol ≥ 31。探测不到则 **fail closed 并提示 `brew install rsync`**，不静默退回 openrsync 或 scp——静默退回正是当初丢掉增量能力、且很可能损坏了 FTS5 字节的那条路。
2. **basis 语义**：`data/radar.db.basis` 始终等于"上一个**已接受**的 Mac 原始快照字节"。rsync 必须**显式以它为 delta basis**（`--copy-dest` / `--compare-dest`），不是留一个异名文件了事。**basis 仅在 candidate 验证并成功激活后推进**；失败不推进。
3. **移除服务器侧 rebuild**（前置：P0b 通过）。apply 的放行判据换成**完整 FTS acceptance triple**（T-a + T-b + T-c，见上节），fail-closed：任一条不过则保留旧 active DB 并告警，**不自动回退到 rebuild**——自动回退会让"前提失效"这件事永远不被发现。
4. **零停机切换**：新库就位 → 起 standby `ai-radar-serve@8001`（带 pre-migrated，绑**自己 candidate descriptor 里的 DB**）→ **standby readiness = healthz 通过 + 完整 FTS acceptance triple 通过**（不是"healthz + 命中数对得上"）→ `nginx -t` → reload 切 upstream → journal 落 `committed` → 停旧实例。
   - **内存不足时 fail closed**：保留旧 active DB 并告警，**不降级为停机切换**。这由已锁定的 L2-7 零停机承诺直接推出，不是新取舍。

   **内存 gate 实测（T2 已解决）**——服务器上运行中的 serve（DB 1.63 GB）：

   | 指标 | 值 |
   |---|---|
   | 进程 RSS（稳态） | **39 MiB** |
   | systemd `MemoryCurrent` | 144 MiB |
   | systemd `MemoryPeak` | **522 MiB** |
   | 整机 available / swap | **2989 MiB** / 1987 MiB（未用），`/proc/pressure/memory` 全 0 |

   RSS 只有 39 MiB 是因为 SQLite 经 page cache 读盘，不把库载入进程内存——所以"2GB 库需要 2GB 内存"的直觉不成立。522 MiB 峰值主要来自启动时 migrate 的 FTS 重建，**P1-4 + pre-migrated 落地后该峰值本身即消失**。

   两实例并存最坏 ≈ 2 × 522 MiB ≈ 1.05 GiB，对 2989 MiB 可用内存有充足余量。**gate 阈值定为 `available ≥ 1536 MiB`**（约 3× 单实例峰值），低于此则 fail closed。
5. **manifest 改造**：`db-sync-manifest.py` 改成被 gate 与 status 实际消费的 **accepted-snapshot receipt**（snapshot ID、生成时间、行数基线、parity 基准值）。删除无人消费的全表逻辑 digest——一个高成本却不参与 PASS/FAIL 的 artifact 只会制造"已校验"的错觉。
6. **timer 在 P2 只安装、不 enable**；P3 gate 全过后才 enable。

**内部 verify**：`tests/test_db_sync.sh` 覆盖——GNU rsync 缺失 fail closed；integrity 失败保留现库；parity 不符保留现库；内存不足 fail closed 且不降级；失败后 basis 与 `.deployed-sha` 均未推进。用两份 fixture DB 跑完整 sync→apply→切换。

#### P1-3 ICP 页脚 + 站点域名

- 新建 `web/templates/_icp_footer.html`：`沪ICP备2026017013号`，链接 `https://beian.miit.gov.cn/`，`target="_blank" rel="noopener"`。
- 挂载点：桌面端 `sidebar-foot`（`index.html:31` 附近既有容器）**且** `/more` 页（移动端侧边栏不常开，须保证移动可达）。
- 配置化：`AI_RADAR_ICP_BEIAN`，未设置**不渲染**（项目规则要求配置优先于硬编码维护者身份；开源 fork 不应带他人备案号）。
- 服务器 `.env` 设 `AI_RADAR_SITE_DOMAIN=news.aiplanet.live`。

**内部 verify**：`tests/test_web.py` 增——设变量时 `/` 与 `/more` 含备案号与 `beian.miit.gov.cn`；未设时两页均不含、且无空区块残留。

#### P1-4 修 migration 003：停止每轮重建全文索引 `(A1,V1)`

用户已决定纳入本次上线。这是同步成本 94% 的来源。

**已勘察的迁移机制（implementer 不必重查）**：

- `db.migrate()`（`src/airadar/db.py:99-105`）每次跑 `MIGRATIONS_DIR` 下**全部** 14 个 `.sql`，靠每个文件自身幂等。
- 唯一的跳过钩子是 `_migration_already_applied()`（`db.py:88-96`），当前**硬编码只认 `004_enrich_stage.sql`**：查 `airadar_migrations` 账本表里有无 `004_enrich_stage` 记录。
- `_execute_migration_idempotent()`（`db.py:50-`）逐语句执行，靠 `END;` 行识别 `CREATE TRIGGER` 块边界，并把 `duplicate column name` 当幂等 no-op。**改写 003 的 SQL 时必须保持这个文本形状**，否则语句切分会错。
- 004 另有一套 SQL 内条件应用模式（TEMP 表 `_airadar_migration_004_apply` + `airadar_migrations` 判存）。但**该模式对 DDL 无效**——SQLite 无法条件化 `DROP TABLE` / `CREATE VIRTUAL TABLE`，所以 003 不能照抄。

**因此 003 的改法走 Python 侧的跳过钩子**（复用既有机制，不发明新的）：

把 `_migration_already_applied` 从"硬编码 004"泛化为按文件名分派的谓词；对 `003_add_fts5_search.sql`，**当且仅当下列全部成立时返回 True（跳过）**：

1. `items_fts` 存在，且 `sqlite_master.sql` 与迁移文件里 `CREATE VIRTUAL TABLE` 的声明**规范化后逐字相同**（空白归一，不做语义比较——语义比较会漏掉 tokenizer 变更这类致命差异）。
2. **五个** `CREATE TRIGGER` 声明的触发器——`items_ai_fts` / `items_au_fts` / `items_ad_fts` / `sources_au_fts` / `enrich_ai_fts`——全部存在且定义规范化后逐字相同。
3. **遗留触发器 `evals_ai_fts` 不存在**。003 的 `DROP TRIGGER` 清单有 **6** 个名字、`CREATE` 只有 **5** 个：多出的 `evals_ai_fts` 是已废弃项（当前库中确实不存在）。谓词若照搬 DROP 清单会把"它不存在"误判成漂移、从而每轮都重建——正是要消除的行为。

任一不成立 → 返回 False → 文件照现状全量执行（DROP + 重建 + 重灌）。这样：

- 稳态下每轮 pipeline **完全不碰 FTS**（省下 296 MiB/轮）。
- 将来编辑 003 改触发器或改 tokenizer，比较即失配，下一次 migrate 自动重建——**"保持最新"的保证没有丢失，只是从"无条件重做"变成"检测到漂移才重做"**。
- 冷库（无 `items_fts`）走原路径，行为不变。

**约束**：003 第 78 行注明 `enrich_ai_fts` 块在 003 与 004 中必须逐字节相同。谓词的比较基准必须从**迁移文件本身**解析，不要在 Python 里另写一份期望字符串——否则就制造了第三处需要同步的副本。

**对既有库安全**：现存库的 FTS schema 已正确，改后首次 migrate 应**不触发重建**（这是必须断言的行为，不是期望）。

**内部 verify**：

- **跳过路径必须用执行证据，不能用页数**：断言"schema 一致时 003 被整体跳过"要么记录 `_execute_migration_idempotent` 实际执行了哪些 migration，要么用 SQL trace / authorizer 断言**没有执行**针对 `items_fts` 的 `DROP` / `CREATE` / `INSERT`。~~断言 `items_fts_data` 页数前后不变~~ **不可用**——DROP + 重建后页数完全可能相同，该判据在"跳过"与"重建了但结果一样"两种情况下输出相同。
- schema 被人为改坏时确实会重建，且重建后跑**完整 FTS acceptance triple**（不只是"重建发生了"）。
- **实测收益**：真实库副本上跑一轮 pipeline，用 `deploy/sync/pagediff.py` 测 delta，断言 **< 20 MiB**（当前 314 MiB）。这条是端到端收益证据，保留。

#### P1 出口

commit 到工作分支，过 review-gate。**不自动整合进 main，不 push**——见 G0a / G0b。

---

### P2 — 服务器搭建 `(A1,V1)`

#### P2-1 可追溯的发布链路

- 服务器建 bare repo `~/ai-radar.git` + `post-receive`。
- Mac：`git remote add tencent ubuntu@111.229.134.9:ai-radar.git`。
- **post-receive 必须走 candidate 模式**（不是 `checkout -f` 到活动工作树）：先检出到隔离 candidate 目录、`uv sync`、跑验证，通过后才按 active release invariant 切换；失败恢复旧 SHA。
- `data/` 与 `.env` 永不进入检出流程。
- hook 写 `~/ai-radar/.deployed-sha`。

**Verify**：`git push tencent main`（需 G0b 许可）后服务器 `.deployed-sha` == Mac `git rev-parse main`；`data/radar.db` 大小与 mtime 未变。

#### P2-2 服务器环境

- `/etc/ai-radar/server.env`：`AI_RADAR_SITE_DOMAIN=news.aiplanet.live`、`AI_RADAR_ICP_BEIAN=沪ICP备2026017013号`、`AI_RADAR_PRE_MIGRATED_DB=1`。**不放任何 LLM key**。
- **不装 Playwright**（serve 不依赖；微信抓取与 performance-probe 都在 Mac）。
- 跑 `install-server.sh`（timer 只装不 enable）。

**Verify**（方向已修正）：

- **CORS**：向实际 API 发带 `Origin: https://news.aiplanet.live` 的请求，断言响应 `Access-Control-Allow-Origin` **精确匹配**该值。
- **User-Agent**：~~从首页响应验证~~——服务器不跑 pipeline，首页响应里根本不含 outbound UA，该断言方向错误，**删除**。改为保留/扩展既有 `test_fetch_user_agent_uses_configured_site_domain`，在 Mac 侧验 outbound client header。
- `status-server.sh` 各服务 loaded；`curl localhost:8000/api/v1/healthz` 200。
- **serve 不写库**：重启 serve 前后 `radar.db` 的 mtime 与 `items_fts_data` 页数不变（证明 pre-migrated 生效）。

---

### P3 — DB 同步上线 `(A1,V1)`

1. 全量首同步，建立 `radar.db` + `radar.db.basis` + receipt。
2. **副本正确性**：完整 **FTS acceptance triple**（T-a + T-b + T-c）。不得退化为只比命中数——索引 malformed 时命中数照样全对，实测已证。
3. 实测增量与端到端耗时（P1-4 已修，预期 ~5 MiB/轮），产出 G2 packet。
4. enable timer，连续观察 3 轮。

**零停机验收（有终态的 orchestration，不是 `while true`）**：采样在 sync 前启动、在 apply 到达终态后停止；断言 `sample_count > 0`、每个样本均为 200、**连接错误也算失败**。

---

### P3b — 上线前最小告警 `(A1,V1)`

**必须在 P4 之前**——P4 之后服务器就是唯一生产入口，先上线后建告警是阶段依赖倒置。

- 覆盖四类规则：serve 存活 / healthz / 磁盘水位 / DB 同步失败。设计前读 `~/.claude/references/alerting-review-principles.md` 与 `service-operations-protocol.md` §6。
- **必须是被真正调度的常驻检查，不是一次演练**：checker 脚本 + systemd service/timer 在 **P1-1 一并 tracked 落仓**，P3b 负责 `enable` 并 **readback 下一次触发时间**（`systemctl list-timers`）。只跑一次停服演练能让 L2-11 通过，却留下一个根本没在跑的告警。
- 投递沿用既有 `im-notify` 飞书 webhook；服务器需装 `~/.local/bin/im-notify`。安装后做**无发送 preflight**（检查可执行文件、Node、两个 webhook 键是否存在；**只输出键名与计数，不回显 URL**）。
- **~~装不上则退回 Mac 侧探测公网 URL~~ —— 已删除**。该 fallback 有两个致命问题：P4 之前那个 URL 根本不存在；且它只覆盖 healthz，会静默丢掉磁盘与 DB 同步失败两类规则。**安装失败即 fail closed，阻止进入 P4。**
- **趁站点尚未公开**，实际停一次 serve，验证真实判定链 firing → resolved → 飞书真实送达。
- P4 之后只做无破坏的外部 health readback。

---

### P4 — news.aiplanet.live 上线 `(A2,V1)`

前置：P0（443）、P3、P3b。

**顺序（DNS 必须先行——HTTP-01 挑战要求 Let's Encrypt 能通过申请域名的 80 端口取到 challenge，域名没解析到本机则 certbot 必定失败）**：

1. **建 DNS**：Cloudflare `news` A → `111.229.134.9`，**proxied=false（灰云）**。橙云会把流量拽回跨洋边缘，正是要消除的根因。
2. **DNS readback**：权威 NS 与公共 resolver（1.1.1.1 / 8.8.8.8）均返回该 IP，再往下走。
3. 装 **tracked 的 port-80 vhost**（含 `/.well-known/acme-challenge/` 显式 location），`nginx -t` → reload。
4. **challenge 路径探针**：往 `/.well-known/acme-challenge/` 放一个测试文件，从公网 `curl http://news.aiplanet.live/.well-known/acme-challenge/<file>` 取得它，证明 HTTP-01 的路必然通——再去申请证书。
5. `certbot certonly --nginx -d news.aiplanet.live`——只让 nginx plugin 临时完成 challenge，**不让它改写站点配置**。
6. **确认 certbot 未留下未跟踪的配置漂移**：`git`-外的 nginx 配置与步骤 3 装的 tracked 版本逐字比对。
7. 启用**仓库内 tracked 的 TLS vhost**，`nginx -t` → reload。
8. `certbot renew --dry-run`；确认续期 timer 已启用。
9. `default_server` 改 `return 444`（ACME location 必须先于 444 匹配）。

> **为什么顺序是这样**：原稿把建 DNS 排在 certbot 之后，那样 P4 执行到第 5 步必定失败——Let's Encrypt 会去解析 `news.aiplanet.live` 并访问其 80 端口，而此时该域名尚未指向本机。这是执行顺序错误，不是文字问题。

**Verify（成对探针，V-04）**：

```bash
# 1. 正向：同一 IP/443 上正确 vhost 健康——先证明可达
curl -sS --noproxy '*' --resolve news.aiplanet.live:443:111.229.134.9 \
     -o /dev/null -w '%{http_code} %{time_total}\n' https://news.aiplanet.live/api/v1/healthz
# 2. 反向：裸 IP 与任意 Host 均不返回站点（用 -k，使证书失败不被误当作拒绝证据）
curl -sSk --noproxy '*' -o /dev/null -w '%{http_code}\n' https://111.229.134.9/
curl -sSk --noproxy '*' -H 'Host: nonsense.example' -o /dev/null -w '%{http_code}\n' https://111.229.134.9/
# 断言：均不含站点 sentinel / HTML / 2xx
```

未加 `-k` 的裸 IP 请求失败**不构成** default-server 拒绝的证据——TLS hostname mismatch 会伪造出同样的失败。

其余：ICP 页脚可见（按 L2-6 的完整 lens）、**完整 FTS acceptance triple + L2-4(b) 的公开入口搜索验收**（不是"搜索 parity"）、HSTS 头。

---

### P5 — 裸域与 sjtu 下线 `(A2,V1)`

> **跨仓库**。`~/research/sjtu-aaa/AGENTS.md` 与其 `docs/operations/services.md` **已读**，核实事实：
> - `sjtu.aiplanet.live` 由 **ai-radar 的** tunnel 配置服务；**sjtu 仓库明确不负责安装或删除该 tunnel**。
> - sjtu 的所有路径（含 `/admin`）都代理到 origin，由 Cloudflare Access + 应用 JWT 把关。
> - sjtu 自有服务：`serve`(:8100) + 4 个 user cron（`pipeline` / `loop` / `promote` `41 4 * * *` / `exa-fetch` `31 4 * * *`），由其自己的 `./install.sh` / `./uninstall.sh` 管理。
>
> **发现两仓库文档冲突**：ai-radar 的 `docs/operations/services.md` 称 sjtu 的 `/admin` 必须由 tunnel 级 `http_status:403` 规则挡住；sjtu 的 `AGENTS.md` 明确说**不要**加该规则（会在 Access 认证前短路 `/admin`，授权用户也进不去）。实际 `config.yml` 里**没有**该规则，与 sjtu 那份一致 → **ai-radar 的文档在这一点上是错的**，P6 修正。

**用户已决定：连本机服务一并停。**

mutation packet 固定为：**2 个 DNS record ID + 1 个 launchd label + sjtu 的服务清单**。

1. **before snapshot 存盘**——落点固定为 **`plans/20260808-news-aiplanet-launch/state.md`** 的「P5 before packet」段（该文件由 execute-plan 在开工时创建并全程维护，见 plan 末「长任务状态文件」）。内容：两条 CNAME 的完整 JSON（含 record ID）、`openclaw`/`server` 的全部 A/AAAA、MX ×2 与 TXT ×4 的 ID 与内容、`cloudflared tunnel list` 中该 tunnel 行、`~/.cloudflared/` 文件集合与 sha256、tunnel launchd 单元状态、sjtu `./status.sh` 全量输出与其 crontab、sjtu 数据文件的大小与 sha256。
2. 删除 `aiplanet.live` 与 `sjtu.aiplanet.live` 两条 CNAME。**删除后对 Cloudflare 完整 record set 做 before/after diff，唯一允许的变化就是这两个 ID 消失。**
3. Mac：`cd ~/research/ai-radar && ./uninstall.sh tunnel`（保留 `~/.cloudflared/` 凭据）。
4. sjtu 侧：`cd ~/research/sjtu-aaa && ./uninstall.sh`（停其 serve 与 4 个 cron）。**先读该仓库当轮的 `AGENTS.md`**，按其脚本契约执行，不手工改它的 crontab。
5. Mac serve 保留 8010 不变。**更新 plist 里那段"审核期间不得上线"的注释**为新事实（生产已迁服务器），否则下一个 session 会被误导。

**preservation assertion（必须是会失败的断言，不是散文承诺）**——before/after 逐项比对，任一不符即中止并回滚：

| 对象 | 断言形式 |
|---|---|
| tunnel object | `cloudflared tunnel list` 中 `c01ac79f…` 的 **tunnel ID 仍存在**（停用 launchd 单元 ≠ 删除 tunnel） |
| `~/.cloudflared/` 凭据 | **文件集合与逐文件 sha256 前后一致** |
| MX ×2 / TXT ×4 | Cloudflare record set 中这 6 条的 **ID 与内容逐条相同** |
| `openclaw` / `server` | **真实 HTTPS readback**（不是 `dig`）：两站各请求一次并断言返回站点内容 / health 正常。只查 DNS 证明不了它们还活着 |
| sjtu 数据 | `sjtu-aaa/var/sjtuaaa.sqlite3` 及相关数据目录的 **大小 + sha256 前后一致** |

**DNS 验收三层 + 陈旧缓存**：

```bash
# 层1 Cloudflare API readback：两条记录不再存在，其余 record set 逐条相同
# 层2 权威 DNS
dig +short @<zone-ns> aiplanet.live; dig +short @<zone-ns> sjtu.aiplanet.live
# 层3 TTL 后公共 resolver
dig +short @1.1.1.1 aiplanet.live; dig +short @8.8.8.8 sjtu.aiplanet.live
# 层4 陈旧边缘缓存：对删除前记录的旧 Cloudflare edge IP 直连
curl -sSk --resolve aiplanet.live:443:<旧edgeIP> -o /dev/null -w '%{http_code}\n' https://aiplanet.live/
# 未误伤
dig +short openclaw.aiplanet.live; dig +short server.aiplanet.live; dig +short MX aiplanet.live
```

**陈旧边缘缓存的 PASS/FAIL 边界**：删除前记录两个 host 的**全部 A/AAAA**（不是取一个 IP）；TTL / 缓存窗口过后对**每一个**旧 edge IP 做 `--resolve` 直连，断言 **不含站点 sentinel 且状态码非 2xx**。仍有 stale content 时**必须先完成授权的 cache purge 并复验**，不得以"缓存总会过期"放行。该 zone 上有 Cache Rule（`AI Radar short public pagination TTL`），只验 `dig` 不足以证明用户已经看不到旧站。

**两种 rollback 区分清楚**：

| 场景 | 动作 |
|---|---|
| **撤销 P5、恢复功能等价前态** | 重建两条 CNAME（用存盘的 record JSON）+ `./install.sh tunnel` + sjtu `./install.sh`。Mac serve **保持 8010**（快照中的状态）。**注意不是"精确前态"**——重建的 DNS 记录会获得**新的 record ID**，无法复原旧 ID。因此验收判据是**功能等价**：DNS 取值、tunnel 状态、sjtu 的 service 与 cron 清单与 before snapshot 逐项等价；新 record ID 需记录下来替换 mutation packet 里的旧值 |
| **新站故障、需恢复旧公网入口** | 在上一条基础上，**另需明确授权**才把 Mac serve 改回 8000 |

代码层 rollback 由 P1-2 的 active release invariant 承载（checkout 旧 SHA 走 candidate 流程），不是"checkout 前一个 commit"这种模糊说法。

---

### P6 — 运维收口 `(A1,V1)`

1. **文档同步**（按 `docs/CLAUDE.md` 与 docs-organization-protocol）：
   - `docs/operations/services.md`：服务器侧服务表、发布链路、DB 同步 timer；**修正 sjtu `/admin` 403 规则那段错误记载**；标注 tunnel 已停用、Cloudflare Cache Rule 当前无流量命中（**不删规则**——EdgeOne plan 或回滚可能用到）。
   - **补文档：`AI_RADAR_PRE_MIGRATED_DB`（含隐藏的 `--pre-migrated-db`）** 进 `.env.example` 与 services.md。
   - `docs/experiences/deployment.md`：记录 openrsync 损坏被误判为版本不兼容、以及 migration 003 每轮重建索引这两条教训。
   - `README.md` / `CHANGELOG.md`：**不能只换域名**。`README.md:228` 附近的运维入口仍描述旧的 tunnel 模型，会把维护者引向已停用的链路。必须同时改服务表与部署入口，写清：**Mac** = pipeline + 内网预览；**腾讯服务器** = systemd serve / db-sync / nginx；发布 = `git push tencent main`；状态 = `deploy/server/status-server.sh`；**tunnel 已停用**；深入操作链接到 `docs/operations/services.md`。
   - `docs/contracts/ux-contract.md`：见下节。
2. **归档前序 plan**：`plans/20260719-tencent-migration/plan.md` 顶部标注已被本 plan 取代。
3. 观察一周，再评估提高同步频率与推进 EdgeOne。

---

## L2：用户视角 verify

| # | 使用者 | 验收条件 | 可执行形式 | 人机 |
|---|---|---|---|---|
| L2-1 | 读者 | 公网可打开且有内容 | `https://news.aiplanet.live/` 200 且 HTML 含精选条目标题（非空列表） | agent |
| L2-2 | 读者（大陆） | 明显快于迁移前 | Mac（上海）实测：首页 < 1.5s、healthz < 200ms。基线：迁移前经 CF 1.5–4.5s | agent |
| L2-3 | 读者 | **ux-contract 声明的全部公开 route** 正常 | 真实浏览器遍历 `/`、`/all`、`/hot`、`/daily`、`/bookmarks`、`/about`、`/changelog`、`/more`、`/wechat`、`/wechat/<slug>`，各含其特征元素（非仅状态码）；覆盖桌面 + 移动 + 契约声明的缩放档 | agent |
| L2-4 | 读者 | 搜索结果完整、索引健康、**且公开入口真的能搜** | **两层都要**：(a) 服务器侧完整 **FTS acceptance triple**（见上节）；(b) **部署后经真实公网入口** `https://news.aiplanet.live` 做 HTTP/浏览器搜索验收——title / body / source / author 各一个正向样例，结果 ID 与 count 与 Mac 基准一致；另验 source/author 优先级、无结果空态、清空搜索恢复列表。DB 层 triple 全过、公开搜索 UI/API 仍可能坏，(b) 不可省 | agent |
| L2-5 | 读者 | 内容是新的 | 首页最新条目距当前 ≤ 同步周期 + 一个 pipeline 轮次 | agent |
| L2-6 | 监管/读者 | 备案号可见可点 | 桌面侧栏底部可见且不遮挡内容；`/more` 移动端可达；文本与 URL 正确；新窗口行为成立；未配置时无空区块 | agent |
| L2-7 | 读者 | 同步不致不可用 | 有终态采样：sync 前启动、apply 终态后停止；`sample_count > 0`、全部 200、连接错误算失败 | agent |
| L2-8 | 读者 | 旧入口不再困惑 | P5 的四层 DNS 验收全过 | agent |
| L2-9 | 维护者 | 能发布且知道跑的是什么 | 改一行 → push → `.deployed-sha` == 本地 `git rev-parse main`，改动在公网可见 | agent |
| L2-10 | 维护者 | 能回滚 | 按 active release invariant 切回旧 SHA，站点恢复旧行为；DB 从 Mac 重新同步可恢复 | agent |
| L2-11 | 维护者 | 挂了会被告知**且告警确实在跑** | **三层都要**：(a) scheduler 已 enable，`systemctl list-timers` readback 到下一次触发时间；(b) 四类规则（serve 存活 / healthz / 磁盘 / DB 同步失败）**逐条**测试判定；(c) 未公开时停一次 serve，走完真实 firing → resolved → 手机送达 | (a)(b) agent；(c) **人工**确认收到 |
| L2-12 | 维护者 | 一眼看清生产状态 | `status-server.sh` 显示 deployed SHA、**当前正在服务的 source snapshot 完成时间**、active port；最后一次失败尝试另报 degraded，**不覆盖**前者 | agent |
| L2-13 | 读者 | 未误伤其他站点 | `openclaw` / `server` 仍解析并响应；MX 记录仍在 | agent |

**负向验收**：

- L2-14：裸 IP 与任意 Host 均不返回站点内容——**成对探针**（先证同 IP/443 正确 vhost 健康，再用 `-k` 证裸 IP 被拒）
- L2-15：服务器无 LLM key——覆盖 `DEEPSEEK_API_KEY|ARK_API_KEY|OPENAI_API_KEY|GLM_API_KEY` 四个 provider × `/etc/ai-radar/server.env` + `~/ai-radar/.env` + **实际 serve 进程环境** 三个来源；**只输出变量名与命中计数，不回显值**；先断言被检查的配置源与目标进程数量非零，再允许报告"0 keys"；读不到必须输出 `UNVERIFIED`，不算通过
- L2-16：serve 重启不改写副本（mtime 与 `items_fts_data` 页数不变）

---

## UX 契约影响

**有影响**：域名变更 + 新增 ICP 页脚。

| Section | delta |
|---|---|
| §产品概述（L10） | `https://aiplanet.live` → `https://news.aiplanet.live` |
| §用户视角验证条件（L225） | 打开地址更新 |
| §范围与约束 · 在范围内（L555）、L569 | 域名前缀更新 |
| **新增** §全站通用 · 页脚 | 备案号页脚契约：桌面侧栏底部 + `/more` 可见，文本 `沪ICP备2026017013号`，链接 `beian.miit.gov.cn`，新窗口；未配置时不渲染 |
| §全站负向断言（L374） | 增：未配置备案号时任何页面无空的备案区块 |

**域名替换影响全部公开 route**，所以 L2 引用的是完整公开页面 smoke（L2-3）+ 页脚 acceptance lens（L2-6），不是只有三条。

未浮现新取舍。**给 execute-plan 的指令**：apply 上表 delta，按 L2-1 / L2-3 / L2-6 / L2-8 验证；不要自行新增本表未记录的契约改动。

---

## 用户决策 gate

| Gate | 位置 | 决定什么 | 材料 | 回复格式 |
|---|---|---|---|---|
| **G0a** | P1 出口 | 是否把工作分支整合回**本地 main**（BINDING：需显式许可，纯 ff 也要问） | agent 列出将整合的 commit、能否纯 ff、历史形态选项 | 选历史形态 = 授权该形态下的整合 |
| **G0b** | P2-1 前 | 是否 `git push tencent main`（BINDING：push 需显式许可；**plan 的上线目标不构成 push 许可**） | agent 说清将 push 什么到哪 | 明确许可 / 拒绝 |
| **G1** | P0 | 无需决定，需**执行**：控制台登录 + 放行 443（登录后 agent 可完成规则） | Chrome Dev 标签 `tcfw` | 登录完成后告知 |
| **G2** | P3 结束 | 同步频率定值 | 固定列的 packet：频率 / 每轮 + 每日传输量 / 端到端耗时 / 最大内容陈旧度 / 内存余量 / 失败时 active DB 行为。数值由实测生成，**比较口径不得临场发明** | 从 agent 给的选项中选 |
| **G3** | P4 后、P5 前 | 新站体验是否达标、可否下线旧入口 | **单一 review packet**（不是"你自己打开看看"）：列出全部精确 URL + 建议浏览顺序 + 每页预期看到什么 + L2 自动证据摘要 + 结构化 rubric。**agent 必须先自验 packet 里每条链接都能打开**，再交用户 | 结构化 rubric：主要页面可用性 / 内容新鲜度 / 主观延迟 / ICP 页脚位置·可见性·点击行为 / 是否同意进入 P5。回复"批准 P5"或按 `页面 + 维度` 报问题 |
| **G4** | P5 | 二次确认 sjtu 下线 | agent 列出 2 个 DNS record ID + tunnel launchd label + sjtu 将停的服务清单 | 确认 / 改主意 |
| **G5** | P3b | 告警真实送达 | 手机飞书 | 收到 / 没收到 |

G3 是唯一必须人工的体验判断——按 `~/.claude/references/web-ui-observation.md`，"视觉/体验已对齐"不能由逐值审计或截图矩阵代替用户自己打开看一遍。

---

## Defaulted Decisions

| # | 决策 | 默认值 | 理由 |
|---|---|---|---|
| D1 | 服务器不装 LLM key | 不装 | 不跑 pipeline，装了只扩大泄露面 |
| D2 | 服务器不装 Playwright | 不装 | serve 不依赖，省 ~400MB 与内存 |
| D3 | DB 同步频率初值 | P1-4 修完后预期 ~5 MiB/轮，初值 **每 30 分钟**，G2 用实测定终值 | 用户倾向"越频繁越好，只要每次代价小"；修完后 15 分钟也可行，但先留一档余量观察 |
| D4 | ICP 页脚配置化 | `AI_RADAR_ICP_BEIAN`，未设不渲染 | 项目规则要求配置优先于硬编码；开源 fork 不应带他人备案号 |
| D5 | 蓝绿端口 | 8000 / 8001 | 8000 是现状；与 Mac 的 8010 无关（不同机器） |
| D6 | Mac serve 保留 8010 | 保留 | 已是内网预览入口；改回 8000 会在 tunnel 停用前的窗口意外上线 |
| D7 | 不删 Cloudflare Cache Rule | 保留并标注失效 | EdgeOne plan 或回滚可能用到；删除不可逆且无收益 |
| D8 | performance-probe / 完整告警体系不迁 | P3b 只做最小告警 | `plans/20260721-alerting-quality-fixes` 有未完成的告警 plan，不并行动它 |
| D9 | 服务器 python | **必须换成 uv standalone 3.13（sqlite 3.53.1）** | 真库实测：现有 venv 的 3.45.1 对 Mac 写的索引报 malformed，3.53.1 完全正常。这不再是可选优化，是 serve 正确读副本的前提。落地：仓库加 `.python-version`（`3.13`）+ 服务器 `uv python install 3.13` + 重建 `.venv`。注意补丁版本漂移会换掉捆绑 sqlite（Mac 3.13.12→3.50.4，服务器 3.13.14→3.53.1），判据是**服务器 ≥ Mac**，不是完全相同 |

---

## Bounded TODO

| # | 细化 plan 哪一处 | 内容 |
|---|---|---|
| T1 | P1-1 | `install-server.sh` 的幂等策略（是否复用 `deploy/lib/services.sh` 的路径校验工具函数） |
| ~~T2~~ | ~~P1-2~~ | **已实测解决**，见下「内存 gate 实测」。阈值定为 `available ≥ 1536 MiB`；实施时只需在 `.next` 库规模显著增长后复测一次 |
| T3 | P2-1 | candidate checkout 的具体隔离方式与切换原子性实现 |
| T4 | P3b | 告警 fire 条件与文案（按 alerting-review-principles 设计） |
| ~~T5~~ | ~~P1-4~~ | **已勘察定案**，见 P1-4「已勘察的迁移机制」+「003 的改法」。剩余细化仅为规范化函数的具体实现（空白/大小写处理） |
| T6 | P1-2 / P3-3 | 实现 `deploy/sync/pagediff.py`：输出页大小、变化页数、增量字节、占全库比例；以两份 fixture DB 验证 |

---

## Risks

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R1 | P0b 判据 5 失败（生产启动形态下 serve 读不对） | "免 rebuild"前提失效 | **已改为机械 trigger，不再默认回用户**：先核实并修复实际 serve runtime 与 HTTP search path（D9 已选定，不作为候选重新询问）。**只有**在正确 runtime 下完整 FTS acceptance triple 仍失败，才升级为 Stop Gate 交用户重选机制 |
| ~~R2~~ | ~~3.6 GiB 内存放不下两个预热实例~~ | **已实测基本解除** | 单实例 RSS 39 MiB / systemd 峰值 522 MiB，available 2989 MiB，两实例最坏占 1.05 GiB。gate 阈值 `available ≥ 1536 MiB` 仍保留为 fail-closed 兜底（防将来库规模或 workload 变化），但已非高风险项 |
| R3 | 公网流量暴露性能问题 | 不达 L2-2 | **用户已拍板（D-01 = 选项 A）：保住性能承诺，暂停下线旧入口。** 不达标即保持 P5 未执行（裸域与 sjtu 维持现状），做无用户影响诊断，转入性能 / EdgeOne 子计划，解决后再收口。**不放宽 L2-2，不加 nginx 静态缓存绕过验收。** 旧入口本就处于下线状态，多挂一段时间的实际损失很小 |
| R4 | certbot 与 tracked vhost 争夺权威 | 续期失败或配置漂移 | P4 顺序已按 `certonly --nginx` 重排；ACME location 先于 444；`renew --dry-run` |
| R5 | sjtu 下线波及未知依赖 | 另一项目静默损坏 | 已读其 AGENTS.md 与 services.md（事实见 P5）；G4 二次确认；preservation assertion 逐项；全程可回滚 |
| R6 | 与 `plans/20260721-alerting-quality-fixes` 在告警面重叠 | 两份 plan 互相覆盖 | D8 划清边界；P6-2 归档时检查 |
| R7 | P1-4 改 003 引入搜索退化 | 用户可感知功能损坏 | `(A1,V1)`；执行 trace 断言跳过路径（不用页数）+ 改造前后在真实副本上跑**完整 FTS acceptance triple** + delta < 20 MiB 收益断言 |

---

## 并发隔离声明

**按存在并发写入者处理。** 依据：`git status` 显示本 session 之外的改动（`docs/issues/general.md` 被修改、多个 `.pipeline.lock.reclaim.*`、`data/` 下未跟踪文件），且 pipeline cron 每 15 分钟自动写 `data/` 与 `logs/`——这些是不受本 session 控制的写入者。**不能声明单 session 独占。**

按 `~/.claude/references/concurrent-plan-isolation.md` 定隔离方案，实施前读其三层表。要点：

- **代码改动（P1）适合在 git worktree 里做**，与主工作树的 dirty 状态隔离。
- **`data/` 与 `logs/` 不可隔离**——pipeline 正在写。P0b / P3 的同步必须针对真实主库并用 `.backup` 取一致快照（**不要**直接 rsync live 库，前序 plan 已踩过）。
- **保留无关 dirty 改动**，不清理 `.pipeline.lock.reclaim.*` 与 `docs/issues/general.md` 的既有修改。

---

## 长任务状态文件

本 plan 按 long-task 模式执行，同目录三件套：

| 文件 | 谁维护 | 内容 |
|---|---|---|
| `plan.md`（本文件） | planner；**独立 reviewer 复审期间冻结**，SHA 记于 `.plan-frozen-sha` | 唯一权威方案 |
| `state.md` | **execute-plan 开工时创建**，全程维护 | 各步骤 `[pending]/[done]`；**P5 before packet**（见 P5 第 1 步）；A2 approval 绑定的 before snapshot / mutation scope / rollback packet；Open Issues |
| `journal.md` | execute-plan 按协议逐条追加 | 执行记录，含 G2 需要的实测数据（每轮传输量、耗时、内存余量） |
| `pending-remediation.md` | 复审冻结期的发现缓冲区 | 冻结期新事实写这里，复审返回后一次性合并进 `plan.md`，然后清空 |

**冻结纪律**（上一轮踩过）：复审期间任何人不得写 `plan.md`——包括 planner 自己。四次"顺手补个事实"造成三次快照失效、整轮审查作废。新发现走 `pending-remediation.md`。

---

## 拆出的独立 plan（未定稿，不得直接进 execute-plan）

**EdgeOne 国内 CDN 接入**阻塞在只能由用户提供的外部事实：套餐尚未实名领取；源站保护策略取决于 EdgeOne 分配的回源 IP 段（领取前不可知）。连"算不算完成"都取决于尚未发生的外部动作 → 独立成篇。

落点：`plans/20260808-edgeone-cdn/plan.md`，由 P6 观察期结束、用户领取套餐后创建。前置由用户推进：领取 EdgeOne 国内免费版（需个人实名）。
