> **Archive status**: 已归档，**未按本文收尾**。本 plan 无 `state.md`，其「待办」清单已由 [20260808-news-aiplanet-launch](../20260808-news-aiplanet-launch/plan.md) 整体取代（**不要并行执行本文的待办**）；正文「跨版本 FTS5 malformed」的归因也已被该 plan 的真库实测推翻（结论：服务器必须换 uv standalone python，而非只 pin 版本）。
> 同目录的 `sync-db-to-tencent.sh` / `db-sync-manifest.py` / `test-sync-db-to-tencent.sh` 与两份 manifest 未随本次归档复制（其 tracked 后继实现随 20260808 落在仓内，manifest 含本机路径）。
> 当前生产拓扑与部署契约见 [ADR-039](../../adr/039-route-news-through-edgeone-dns-only-cname.md)、[ADR-042](../../adr/042-isolate-production-deploy-commit-from-local-main.md) 与 [operations/services.md](../../operations/services.md)。以下为原 plan 正文，未修改。

# aiplanet.live 迁移方案：腾讯云备案版（读写分离）

> 定稿于 2026-07-19。用户已逐项拍板。备案周期 1–3 周，本文件是跨 session 接续锚点。

## 目标与收益

把公开访问延迟从当前 **1.5–4.5s** 降到 **40–200ms**（对标 AIHOT 实测 100–215ms），面向**中国大陆用户**。

## 根因（已实证，勿再走回头路）

- 当前架构：上海家用 Mac（`Asia/Shanghai`）经 Cloudflare Tunnel 暴露，`region: us` 强制连美国 edge。
- 主导根因 = **origin↔edge 跨太平洋回源**：同一 healthz，origin 7ms vs public 2.4s；journey P95 16–20s。
- **已证伪的死路**：删掉 `region: us` 改 global anycast → colo 仍是 lax/sjc（美西），时延反而更抖（1.6–4.4s）。**上海机器无论 region 设不设都被路由到美西 CF edge，中国出网到 CF 亚太 edge 可达性受限。region 参数无解，已回滚，不要再试。**
- 对照 AIHOT（体验好）：腾讯云 EdgeOne（国内 CDN，`eo-cache-status: HIT`）+ 火山引擎国内 origin，`s-maxage=300`+swr+single-flight，整链在中国境内就近。

## 定稿决策（用户已拍板）

| 决策点 | 选择 | 理由 |
|---|---|---|
| 备案 | **备案版**（`.live` 已确认在工信部可备案列表） | 延迟最优 + EdgeOne 国内免费版 CDN + 总价更低 |
| 云厂商 | **腾讯云同厂**（origin + EdgeOne 都用腾讯） | 同厂回源走内网/免流量费、备案与运维统一；EdgeOne 原厂；微信生态契合。AIHOT 的火山+腾讯混搭是历史包袱（复用公司共享机），非最优，不照抄 |
| 架构 | **读写分离**：serve+DB 副本迁国内，pipeline 留上海 Mac，DB 单向同步 | serve 要就近用户（国内最优）；pipeline 要就近数据源（含境外 X 源），放国内会被墙+合规。上海 Mac 天然兜底 |

## 目标架构

```
用户(大陆) → EdgeOne 国内节点(缓存HIT ~40-100ms)
                    │ MISS 回源(同厂内网,就近)
                    ▼
        [腾讯云国内轻量 2C4G]  serve + radar.db 只读副本
                    ▲
                    │ 单向 DB 同步(rsync 增量,pipeline 每轮 WAL checkpoint 后推)
        [上海 Mac]  pipeline 抓取(微信/RSS/X)+ LLM 打分 + 写 radar.db 主库
                    (不动,继续兜底)
```

## 采购清单

| 项 | 选型 | 价格 |
|---|---|---|
| origin | 腾讯云国内轻量 通用型 **2C4G / 90G盘 / 6M**（盘要装下 5.5G DB） | ~80 元/月（年付约 83 折） |
| CDN | 腾讯云 **EdgeOne 国内免费版**（实名领取，大陆节点+DDoS） | 0 元 |
| 备案 | 腾讯云 ICP 个人备案（身份证） | 0 元，7–20 工作日 |
| 合计 | | **≈80 元/月（~$11），CDN 全免** |

## 关键现状事实（迁移必读）

- DB：`data/radar.db` 为主，`data/` 共 **5.5G**。首次全量传输不可忽略。
- 平台：**macOS(launchd) → Linux(systemd)**，`install.sh` 与服务定义要适配重写。
- 外部依赖：`DEEPSEEK_API_KEY`、ARK（火山方舟）、Mp2RSS 微信、图片代理——均国内可达；**X/nitter 境外源国内被墙**（据记录已基本失效，损失可接受）。
- 现有调度（`deploy/`、crontab）：pipeline 每 15 分钟、performance-probe 每小时。

## 分步计划（备案与搭建并行）

| 阶段 | 动作 | 可回滚 |
|---|---|---|
| 0. 采购 | 买国内轻量 + 领 EdgeOne 免费版 + 提交备案（**用户执行**） | — |
| 1. 备案等待 | 审核 1–3 周，期间做 2–4 | — |
| 2. 搭建 | 国内机装 Python3.12+uv、迁代码、Linux systemd 服务、配 key | 上海不动 |
| 3. 迁数据 | 首次全量传 5.5G radar.db，起 serve 只读验证 | 上海不动 |
| 4. DB 同步 | 上海 pipeline 每轮 WAL checkpoint 后 rsync 增量推国内 | — |
| 5. 切流量 | 备案通过后 DNS 从 CF Tunnel 切 EdgeOne→国内 origin，灰度验证 HIT/延迟 | **DNS 切回 CF Tunnel 即回滚** |
| 6. 观察 | 跑一周确认延迟/HIT/同步，再考虑关上海 serve | 上海 serve 保留 |

**全程上海 Mac 原样保留，任一步出问题 DNS 切回即恢复现状，零不可逆。**

## 采购进度

- [x] **服务器已购**（2026-07-19）：腾讯云轻量 `Ubuntu-a1GK`，**上海**，2核4G/70GB SSD，Ubuntu 24.04 LTS，新客专享型（首年 360 元/年 4 折，续费约 900），到期 2027-07-19。**公网 IP `111.229.134.9`**。登录方式=自动生成密码（站内信发放）。
- [x] **阶段 2/3 搭建大部分完成**（2026-07-19）：
  - SSH 免密（`ubuntu@111.229.134.9`，公钥已装 `id_rsa`；登录用户是 `ubuntu` 非 root）。
  - 环境：Ubuntu 24.04.4 + 自带 Python 3.12.3 + uv 0.11.29；apt 装了 sqlite3/pip/venv。**GitHub 不通、astral.sh 通**（代码走 rsync 推、uv 官方脚本可用）。
  - 代码：rsync 本机→服务器 `~/ai-radar`（排除 .venv/.git/data/logs/wewe-rss），`uv sync` 39s 装好依赖。
  - DB：`data/radar.db` 实为 **2.0G**（原 5.5G 是 data/ 里的 .bak 撑大的），rsync 2 分钟传完，服务器校验 **items 31399 / curation_runs 5667**。
  - serve：`ai-radar-serve.service`（systemd，`uv run … serve --host 127.0.0.1 --port 8000`，enable+自启+崩溃重启），本地 healthz 200、首页/wechat/curated 均 200。冷启动 startup 慢（lifespan 对 2G 库预热 COUNT 缓存，约 30–40s，未 OOM，内存余 3.0G）。
- [x] **公网暴露 nginx**（2026-07-19）：`/etc/nginx/sites-available/ai-radar` 反代 80→127.0.0.1:8000，透传 serve 的 Cache-Control，自启。公网 80 可达（healthz 29ms、首页 1.45s）。⚠️ origin IP 已暴露公网，EdgeOne 接入后需加源站保护（只允许 EdgeOne 回源）。
- [x] **DB 增量同步机制**（2026-07-19，验证通过）：脚本 `plans/20260719-tencent-migration/sync-db-to-tencent.sh`（上海 Mac 跑）+ 服务器 `~/ai-radar-ops/apply-db-update.sh`。链路：本机 `.backup` 一致快照 → 本地校验(结构+FTS5) → **scp** 传输 → 服务器 **rebuild FTS5** → 校验+原子替换+重启 serve。端到端测通：搜索 200/FTS5 OK。**几个血泪坑（已解决，勿重踩）**：
  - `VACUUM INTO` 会破坏 FTS5 inverted index（malformed）→ 改用 `.backup`。
  - macOS 自带 **openrsync**(proto 29) 的 `-z` 与服务器 rsync 3.x(proto 31) 偶发不兼容、损坏 FTS5 字节 → 改用 **scp**。
  - 本机 sqlite **3.51** 写的 FTS5 格式，服务器 **3.45** 读作 malformed（跨版本）→ apply 里用服务器版 `rebuild` 修复。
  - 直接 rsync live 库会被 pipeline autocheckpoint 改到不一致 → 必须先 `.backup` 静态快照。
  - **成本**：每次同步 scp 全传 ~2G + rebuild FTS5 ~2.5min（serve 运行时后台做、不停机）+ 替换重启预热 ~40s（唯一停机窗口）。
- [ ] DB 同步挂自动化：暂未挂 cron（备案未过、服务器未对外、数据静止无害）。切换前定频率（资讯站可接受几小时延迟，避高峰）。优化方向见下。
- [ ] 领 EdgeOne 国内免费版套餐（需个人实名；接入大陆节点需备案通过）。
- [~] **ICP 备案已启动**（2026-07-19，关键路径最长 1–3 周）：腾讯云备案控制台已授权 Beian linked role、`aiplanet.live` 识别为未备案、进入"新增备案"到"个人信息收集同意"弹窗。**停在此处交用户本人接手**（隐私/人脸不可代办）：同意隐私协议→填主体身份证/姓名/住址/手机→证件上传+人脸活体→关联备案服务器（上海轻量 111.229.134.9，可免费领备案服务码）→真实性承诺→提交→初审 1-2 工作日→短信核验→管局审核。网站信息等非隐私项 agent 可代填。**未知风险**：aiplanet.live 若在境外注册商/无中国实名，域名核验可能卡住（提交时才暴露）。
- [ ] EdgeOne 接入 + DNS 从 CF Tunnel 切到 EdgeOne→上海 origin，灰度验证 HIT/延迟。

## DB 瘦身机会（2026-07-19 分析，独立后续任务，不阻塞迁移）

radar.db 2.1G，核心内容（items 237MB + FTS 222MB ≈460MB）合理，但 curation/eval 历史占 67%，含 ~900MB 可消除冗余：
- **curated_items.summary_json 7 倍冗余**（600MB）：summary 是 item 级内容却按 (run,item) 存，同一 item 平均重复 7 次。应改存 items 表 + curated_items 引用。
- **curation_runs.input_eval_ids 审计快照**（330MB）：每行 61KB 的全量输入 ID JSON，价值低。可移除/清空。
- 三档优化：VACUUM（113MB，零风险）/ 裁剪旧 run 历史（数百 MB，需定保留策略）/ summary 去重+移除 input_eval_ids（~900MB，需改 pipeline/web 代码+测试）。
- 优化后 2.1G→~700MB-1G，DB 同步与服务器空间都更轻。建议单开瘦身 plan。

## 待续 Open Issues
- serve 冷启动预热 2G 库 COUNT 缓存约 30–40s（systemd 重启期间短暂不可用）。非阻塞，若要治理可让 prewarm 异步/惰性。
- origin 首页/curated 本地 ~1s（2C4G，比 M4 慢），在预算内且 CDN 缓存后用户无感；如需可查首页查询重点。
- DB 同步成本高（每次 scp 全传 ~2G + rebuild FTS5 ~2.5min）。优化：①服务器 libsqlite3 升级 ≥3.51 免 rebuild（直接读本机 3.51 的 FTS5 格式，需连带 Python sqlite）；②DB 瘦身（见「DB 瘦身机会」，减传输量）；③增量传输（brew 装 GNU rsync 替代 openrsync + 服务器新 sqlite，恢复 rsync 增量）。三者组合可把单次同步从 ~5-8min 降到分钟内。

## 待办（用户行动项）

1. ~~买服务器~~ ✓ 已完成。
2. 领 EdgeOne 国内免费版套餐（需个人实名）。
3. 提交 aiplanet.live 的 ICP 备案。
4. 提供服务器登录密码（站内信）或授权配置 SSH 密钥，进入阶段 2 搭建。
