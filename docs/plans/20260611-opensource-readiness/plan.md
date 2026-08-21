> **Archive status**: 已归档，执行完成（`state.md` 中 TASK-001..012 全部 done）。执行过程产物 `state.md` / `journal.md` 按长任务协议不入档。
> 开源就绪的当前结果入口：仓库根 `README.md`（fork 部署路径）与项目 `CLAUDE.md`「Verification Notes」记录的 `opensource-baseline` 不可变 tag（before/current 对比基线）；ai-assistant 集成契约见 [references/ai-assistant-contract.md](../../references/ai-assistant-contract.md)。以下为原 plan 正文，未修改。

# Plan — AI Radar 开源就绪

> ⚠️ **Long-task mode** — 本 plan 处于长任务模式
> - 进度状态：`./state.md`
> - 决策日志：`./journal.md`
> - 协议详情：`~/.claude/references/long-task-protocol.md`
>
> 实施时（含 compact 之后）必须先读 state.md 和 journal.md 再决定下一步动作。
> 声称任务完成前必须实际跑本 plan 的 verify 步骤并贴出输出。

---

## 输入与背景

- **任务**：将 ai-radar 改造为开源就绪——配置清晰自包含、不含维护者个人数据/凭据/库外路径依赖、外部依赖默认 disable 显式 enable；同时**不破坏维护者现有 aiplanet.live 部署**。
- **本 plan 是 review/实施唯一入口**，无上游 spec。L1/L2/L3 取舍均已在 deep-discuss 中与用户对齐（见 §取舍 / §Defaulted Decisions）。
- **现状体检（已确认的可观察事实）**：
  - ✅ 已达标：MIT LICENSE；tracked 文件**无真实密钥**（已全量正则扫描）；`.env.example` 完整；`.gitignore` 覆盖 `.env`/DB/deploy 真配置/logs；`data/` 仅 track `sources.toml`；deploy launchd 走 `.example` + `ensure_plist` 把 `/path/to/ai-radar` sed 替换为 `$REPO_ROOT`。
  - ⚠️ 待修（下文 L3 逐项）：
    - **A** `src/airadar/interpret/runner.py:17` 硬编码 `DEFAULT_AI_ASSISTANT_ROOT = /Users/lindong/research/ai-assistant`，`:18` `DEFAULT_USER = "dong_lin"`；`pipeline.sh:66` 无条件 `run_stage interpret`，无显式开关（当前靠"路径存在即启用"的 `_preflight`）。这是唯一的**库外代码依赖**（不开源的 ai-assistant repo 的 `agents/summary-agent/{summarize,run}.sh`）。
    - **B** 个人身份+部署域名硬编码：`web/static/about.html`（维护者 lindong、repo `lindong28/ai-radar`、X `@lindong28`、VISION.md 链接，行 70/91-94，**裸静态 FileResponse**，由 `src/airadar/web/app.py:339-341` `/about` 路由 serve）；`src/airadar/web/cors.py:10` `allow_origins=["https://aiplanet.live"]`（**功能性**，forker 不改跨域失败）；`src/airadar/fetcher/http_client.py:12` `USER_AGENT` 含 `aiplanet.live`；`deploy/lib/services.sh` 服务标签 `live.aiplanet.ai-radar.*`（cosmetic，见 TODO）。**注意** `src/airadar/web/app.py:357` `app.mount("/", StaticFiles(directory=STATIC_DIR, html=True))`——`STATIC_DIR` 即仓库根 `web/static/`，`html=True` 会让 `web/static/about.html` 经 `/about.html`（甚至 `/about` 目录式解析）继续被 serve（见 TASK-003 + R2）。
    - **C** `data/sources.toml` 42 源含 **22 个 `nitter.net` X 源**（已失效，生态崩溃，见 memory `airadar-x-source-nitter-single-point`）；另有 23 个 `kind="x"` 中 1 个是**活的** Mastodon RSS（`fedi.simonwillison.net`，非 nitter，保留）。
    - **D** `docs/plans/`（16 目录，tracked）+ `deploy/wewe-rss/`（已迁 Mp2RSS 过时）属内部产物/过时件；`AGENTS.md` 保留但需 sanitize。
    - **E** `deploy/cron/ai-radar-pipeline:3` verbatim 硬编码 `/Users/lindong/research/ai-radar/pipeline.sh`（被 `install_pipeline` 原样写进 crontab）；`deploy/launchd/ai-radar-pipeline.plist.example` 是废弃文件（pipeline 走 cron 非 launchd）且含硬编码路径。
    - **F** 测试断言个人路径/身份：`tests/test_service_contract.py:59-60`、`tests/test_frontend_static_contract.py:51-53`、`tests/test_wechat_interpretation.py:1102`。
    - **G** git **历史**散布 `/Users/lindong` 路径与内部规划文档（密钥历史干净）。仅 untrack 不清历史。
    - **H** `README.md` 含 clone URL `lindong28/ai-radar`、`AI_ASSISTANT_ROOT` 个人默认路径；`docs/operations/wechat-ingestion.md` 含 `/Users/lindong/research/ai-assistant` 等。

---

## L1 — 最终产物 + 使用方式

- **产物**：开源就绪的 ai-radar 代码库 + 一份在其上清洗过历史的**公开 repo**。
- **真实使用者（两类，都要满足）**：
  1. **Forker / 社区用户**：`git clone` 公开 repo → 按 README 配置自己的 `.env`（LLM key / 自己的域名 / 自己的身份）→ `./install.sh` → 得到可用站点 + 默认可用信源。**可选外部能力（微信解读）默认 OFF**，显式 enable + 自带外部脚本才启用。
  2. **维护者（owner）**：`git pull` 这些代码改动到工作 repo → 把新 config 旋钮设为自己的值（`AI_RADAR_ENABLE_INTERPRET=true` + `AI_ASSISTANT_ROOT` + 身份/域名变量）→ **aiplanet.live 行为与观感与改动前一致**。
- **使用方式/成功定义**：
  - forker：fresh clone 按文档可端到端部署出一个站点；tracked 文件无任何维护者个人路径/身份/凭据；默认信源 clone 即能拉到内容。
  - owner：现有部署零回归（站点观感、CORS、User-Agent、微信解读全部照旧）。
- **范围边界**：本任务**不**新增产品功能、不重构无关代码、不引入 CONTRIBUTING/issue 模板（用户未要求）；身份/域名**泛化为占位符默认 + config 驱动**（不是删除身份，是把维护者真值移出代码、放进 owner 的 gitignored `.env`）。

---

## 取舍偏好（deep-discuss 已对齐）

| 维度 | 取向 |
|---|---|
| owner 现有部署稳定性 vs 改动幅度 | **owner 零回归是硬约束**，宁可多加 config 旋钮也不改变 owner 可感知行为 |
| forker 开箱即用 vs 维护者真实配置保真 | 两者兼顾：默认值中立可用，owner 真值走 `.env` |
| 外部依赖：默认安全 vs 默认便利 | **默认 disable**（缺失/未启用都安静 skip），显式 enable |
| 历史清洁 vs 保留演进痕迹 | 保留历史 + filter-repo 清洗（用户已选） |
| 交付质量 vs 速度 | 质量优先；重机械活委派 Codex、Claude 验证证据 |

---

## L2 — 用户视角 verify（交付 gate，implementer-executable）

> 这是**交付 gate**。声称完成前必须实跑并贴可观察证据。两个视角都要过。
>
> **基线钉死（必做，TASK-001 开工前）**：`git tag opensource-baseline HEAD`。所有"改动前/现值"对照一律用 `git show opensource-baseline:<path>`，**绝不用 `HEAD~`**——本 plan 跨 TASK-001..008 多次 commit，verify 在 TASK-009 执行时 `HEAD~` 早已漂移（甚至已是改后值），会让"零回归"断言退化成"改后==改后"恒真、无法在真回归时 fail。

### V-FORKER（模拟 forker fresh clone）
1. **无个人信息（tracked 快照）**：在最终工作树执行
   `git grep -nE '/Users/lindong|lindong28|aiplanet\.live|dong_lin|/research/ai-assistant' -- . ':(exclude)*.lock' ':(exclude)plans/' ':(exclude)docs/plans/'`
   → **零命中**（身份/域名只应作为占位符默认出现，且占位符不含上述真值）。
2. **sanitizer PASS**：跑 `opensource-sanitizer` agent（20+ 正则扫密钥/PII/内部引用/危险文件）→ 报告 **PASS / PASS-WITH-WARNINGS**（无 FAIL）。
3. **fresh-clone 部署冒烟**：在 `/tmp` clone 一份（或 `git worktree`），仅放最小 `.env`（一个 DeepSeek key + 默认 sources）→
   - `./run.sh fetch` 退出 0，stderr 无"removed source"类错误、无 `nitter.net` 请求；
   - `./run.sh serve` 起站，`curl -s localhost:PORT/about` 渲染成功且**显示占位符身份**（`your-org`/占位维护者，**不含 lindong28**）；
   - **`curl -s localhost:PORT/about.html`**（R2 旧静态路径）**不返回含 `lindong28` 的旧内容**（404 或重定向/占位符）；
   - `./run.sh interpret` 输出 `interpret skipped=true message=...disabled...`（flag 默认 OFF，**不报错、不引用任何 /Users 路径**）。
4. **默认信源干净 + 数量达标 + 保留集精确**（expected-vs-actual，非 existence）：`grep -c nitter.net data/sources.toml` → **0**；裁剪后块数 **== 20**（= 裁剪前 42 − nitter 22，implementer 动态派生对照）；**且 ≥ 20**（ux-contract AB-1 地板线）；**保留集全集断言**——从 baseline 动态导出"原 42 源中非 nitter 的 url 集合"，断言其 **== 裁剪后 url 集合**（防止误删某个未抽到的活源同时少删一 nitter 凑回 20）。

### V-OWNER（模拟 owner 部署回归）
5a. **[agent 可自主]** owner-config 模拟（注入 owner env + **mock 一个 ai-assistant 目录结构**满足 preflight）：
   - `curl -s localhost:PORT/about` 渲染出**维护者 lindong / repo lindong28/ai-radar / X @lindong28**，与改动前语义一致（用 `git show opensource-baseline:web/static/about.html` 对照关键字段）；
   - 进程内验证 `src/airadar/web/cors.py` 解析出的 `allow_origins` 含 `https://aiplanet.live`；`http_client.USER_AGENT` **字节等于字面常量** `ai-radar/0.1 (+https://aiplanet.live)`（零回归是硬约束，substring 不够；用字面常量断言，不依赖任何 git ref）；
   - **mock summarizer 脚本走通 runner 全解析链**：mock 的 `summarize.sh`/`run.sh` 吐符合 TASK-002 契约的 stdout JSON（含 `summary_file_path`/`slug`）+ 写一个含 `### 文章概况` / `推荐等级：必读` 的 summary md + index.json，断言 `run_interpret` 正常解析回读（abstract/推荐/tags 落库）、非 skipped——把"脚本 I/O 契约不匹配"这一失败模式下沉到 agent 自主层。
5b. **[需 owner 真机 + 凭据]** 真机端到端：在 ai-assistant 真实存在的机器上 `./run.sh interpret` 走正常路径（processed/errors，非 skipped）。flag/skip/preflight 分支 + **脚本 I/O 契约解析**已被 5a + TASK-001 单测预筛；5b 仅验证**真实 summarizer 的实际产出**这一无法 mock 的部分，作最后人工确认。
5c. **[agent 可自主]** canonical 信源健康：owner 工作树 `data/sources.toml` 裁剪后**有效源 ≥ 20**（ux-contract AB-1；裁掉的是已死 nitter，对 owner 实际内容零影响）。
6. **全量测试 [agent 可自主]**：`./test.sh`（或 `pytest`）**全绿**。

### V-HISTORY（公开 repo 历史清洗，Codex 执行 + Claude 验证）
7. 在 filter-repo 产出的**公开 repo clone** 上：
   `git log -p --all | grep -nE '/Users/lindong|/research/ai-assistant|dong_lin'` → **零命中**；
   `git log --all --oneline -- 'docs/plans/*' 'deploy/wewe-rss/*'` → **零提交**（这些路径在全历史中已不存在）。
8. 公开 repo `git log --oneline | wc -l` > 1（**历史保留**，非 squash）；HEAD tree 与工作 repo 经 A–F 后的 tree 内容一致（`git diff --stat <public-HEAD> <work-HEAD>` 仅差被有意排除的文件）。

---

## L3 — 设计决策 + 内部 verify（按任务）

### TASK-001 — A：interpret 显式开关 + 去硬编码路径
- **改**：`src/airadar/interpret/runner.py`、`src/airadar/cli.py`、`.env.example`。
- **设计**：
  - 新增 env `AI_RADAR_ENABLE_INTERPRET`（默认 `false`）。`run_interpret` 入口先判此开关：未启用 → 返回 `InterpretSummary(skipped=True, message="interpret disabled (set AI_RADAR_ENABLE_INTERPRET=true)")`，**不触碰任何路径**。
  - 删除 `DEFAULT_AI_ASSISTANT_ROOT` 的个人路径默认。启用后 `_assistant_root` 解析顺序：CLI `--assistant-root` > env `AI_ASSISTANT_ROOT` > **无默认**；缺失 → skip 并提示"enabled 但 AI_ASSISTANT_ROOT 未设"。
  - `DEFAULT_USER = "dong_lin"` → 改为 env `AI_RADAR_INTERPRET_USER`（默认中立值如 `"default"`）。
  - `.env.example` 增 `# AI_RADAR_ENABLE_INTERPRET=false` / `# AI_ASSISTANT_ROOT=` / `# AI_RADAR_INTERPRET_USER=default` 段，注明这是可选的库外集成。
- **内部 verify**：新增/改单测——(a) flag 缺省时 `run_interpret` 返回 skipped 且**不读文件系统**（可 monkeypatch 路径解析断言未调用）；(b) flag on 但 `AI_ASSISTANT_ROOT` 未设 → skipped 带正确 message；(c) flag on + root 有效 → 走原 preflight。`ruff check` + `mypy` 干净。

### TASK-002 — A 文档：ai-assistant 集成契约
- **改/建**：`docs/operations/ai-assistant-integration.md`（新）+ 更新 `docs/operations/wechat-ingestion.md`、`README.md` §微信文章解读。
- **设计**：从 `runner.py` 提取并文档化契约，让 forker 能自带实现：
  - 调用：在 `$AI_ASSISTANT_ROOT/agents/summary-agent/` 下执行 `summarize.sh`/`run.sh`（cwd=root，env 去除 `VIRTUAL_ENV`）。
  - 输入：每篇写临时 `{id}.md`，内容 `# {title}\n\n{content}\n`。
  - 输出：脚本 **stdout 返回 JSON dict**（schema：`summary_file_path`/`summary_file`、`slug`、`exists`/`found`，可嵌 `dedup`/`result`/`data`）。
  - 回读：summary markdown 需含 `### 文章概况` 段（取首段做 abstract）、`推荐等级：必读|值得一看|可跳过`、tags；索引 `$AI_ASSISTANT_ROOT/data/summary_agent/{user}/index.json`（entry.output.summary_file_path）。
  - 文档显著标注：**该集成默认 OFF**，是可选能力。
  - 把 `wechat-ingestion.md` / `README` 里的 `/Users/lindong/research/ai-assistant` 改为 `$AI_ASSISTANT_ROOT` 占位 + 指向新契约文档。
- **内部 verify**：文档自检——契约字段与 `runner.py` 实际解析逐一对应（reviewer 可对读）；无残留个人绝对路径（`grep -n /Users docs/operations/*.md` 零命中）。

### TASK-003 — B：站点身份 + 部署域名 config 化（占位符默认）
- **改**：`web/static/about.html` → 迁为 `web/templates/about.html`（Jinja，复用现有 `Jinja2Templates` 设施，仓库根 `web/templates/` 已存在）+ **`git rm web/static/about.html`（必须删，见下 R2）**；`src/airadar/web/app.py` `/about` 路由改 `TemplateResponse`；`src/airadar/web/cors.py`；`src/airadar/fetcher/http_client.py`；`.env.example`；`README.md`。（`deploy/lib/services.sh` 服务标签**不在本任务**，降级为 TODO，见 §Risks/TODO。）
- **设计**：新增"站点配置"env，**默认占位符**，owner 在 `.env` 填真值：
  - `AI_RADAR_SITE_DOMAIN`（默认空）→ `cors.py` allow_origins：设了则 `["https://{domain}"]`（开发追加 localhost），**未设则仅 localhost，绝不 `*`**；`http_client.USER_AGENT` 设了用该域名，**未设用中立 `ai-radar/0.1`**。owner 设 `AI_RADAR_SITE_DOMAIN=aiplanet.live` 后 UA 须与现值 `ai-radar/0.1 (+https://aiplanet.live)` **字节一致**（V-OWNER#5b 守门）。
  - `AI_RADAR_SITE_REPO_URL`（默认 `https://github.com/your-org/ai-radar`）、`AI_RADAR_SITE_MAINTAINER`（默认 `your-name`）、`AI_RADAR_SITE_MAINTAINER_URL`、`AI_RADAR_SITE_X_URL`（默认空 → 模板**条件渲染**，空则隐藏 X 链接）。
  - about 模板从集中读取的 site-config 注入：**优先复用现有 `db`/settings 的 env 读取路径**（cors.py/http_client.py 本就直接读 env），**仅当现有路径无法干净承载这 5 个变量时**才新增 `src/airadar/web/site_config.py`——避免为 5 个只读 env 变量引入单用途模块；VISION.md 链接用 `AI_RADAR_SITE_REPO_URL` 拼。
  - 致谢/AIHOT 等非个人内容保留。
  - **R2 处置（trigger response，no-impact）**：因 `app.py:357` `StaticFiles(html=True)` 会让残留的 `web/static/about.html` 经 `/about.html` 绕过新模板路由暴露旧硬编码身份——迁移时**必须 `git rm web/static/about.html`**；若实施中发现仍可访问旧内容，备选 no-impact 动作：在 mount 前注册显式 `/about.html` → `/about` 重定向。implementer 可自主执行，无需问用户。
- **内部 verify**：单测——(a) 未设 env 时 `/about` 渲染含占位符、**不含** `lindong28`；(b) 设 owner env 时含真值；(c) `cors.py` 在 domain 未设/已设两种下 origins 正确且无 `*`；(d) USER_AGENT 未设域名为中立值、设 owner 域名时**字节等于字面常量** `ai-radar/0.1 (+https://aiplanet.live)`（不依赖 git ref）；(e) `web/static/about.html` 已删且 `/about.html` 不返回含 `lindong28` 的旧内容。`ruff`/`mypy` 干净。

### TASK-004 — C：裁剪失效信源
- **改**：`data/sources.toml`；如 about/docs 有"停用源"说明则同步。
- **设计**：移除**已失效**的源——即 22 个 `nitter.net` 的 `kind="x"` 条目（生态已崩溃，见 memory）。**保留所有可用源**，含可用非 X RSS（HuggingFace/OpenAI/IT之家/Tom Tunguz/Claude releases 等）**以及那个活的 `kind="x"` Mastodon 源 `fedi.simonwillison.net/@simon.rss`**（按用户"剔除失效"原则，活源保留——故裁剪后**不是"全非 X"**，而是"无失效源/无 nitter"）。**严格不新增**（用户已拍）：裁剪后**恰好剩 20 个**。x source kind 代码路径保留（仅清数据）。
- **内部 verify**（expected-vs-actual，非 existence）：
  - implementer 从真实文件动态派生：`裁剪前总块数(42) − nitter.net 块数(22) == 裁剪后块数`，断言**裁剪后块数 == 20**；
  - `grep -c nitter.net data/sources.toml` → 0；
  - 断言具名应保留源仍在（HuggingFace/OpenAI/IT之家/fedi.simonwillison.net 等抽样存在）；
  - **断言裁剪后源数 ≥ 20**（ux-contract AB-1 地板线，见 §UX 契约影响）；
  - `./run.sh fetch`（或解析校验）对剩余源无 schema 错误。
  - 任何意外缺口（少于 20 / 误删活源）算 defect 非 pass。

### TASK-005 — D：清理发布范围 + sanitize 保留件（工作树层面）
- **改**：`git rm -r --cached docs/plans/ deploy/wewe-rss/`（停止跟踪，工作区文件可留）；`.gitignore` 增 `docs/plans/`、`deploy/wewe-rss/`（或迁出）；sanitize `AGENTS.md`（去个人绝对路径/凭据线索，保留通用 agent 指引）；**sanitize 并保留 `docs/prd/VISION.md`、`docs/prd/PRD_v0.md`**（用户已拍：这两份是 forker 理解产品意图/架构/评分设计的高价值文档）。
- **设计**：
  - 保留 `README`/`CHANGELOG`/`docs/operations`/`docs/adr`/`docs/prd`/`AGENTS.md`。
  - **docs/prd sanitize**：`维护者：lindong` → 泛化占位（如"维护者：见仓库"或 `your-name`）；`aiplanet.live` 字面引用 → **全泛化**（如"公开站点 / 你的域名"，**不保留 aiplanet.live 字面**，与 V-FORKER#1 快照零命中一致；部署域名属可配置见 B）；含个人身份的 decision-log 条目（如 D2 提 summary-agent 个人判断）酌情泛化但保留技术决策实质。无密钥/无 /Users 路径（已确认）。
- **内部 verify**：`git ls-files | grep -E 'docs/plans/|deploy/wewe-rss/'` → 零；`AGENTS.md` 无 `/Users/lindong`；`grep -niE 'lindong|/Users/' docs/prd/*.md` → 零（或仅泛化占位）。

### TASK-006 — E：deploy 路径模板化
- **改**：`deploy/cron/ai-radar-pipeline`（占位符化为 `/path/to/ai-radar/pipeline.sh`）；`install.sh` `install_pipeline`（读取后 `sed "s|/path/to/ai-radar|$REPO_ROOT|g"` 再写 crontab，对齐 `ensure_plist` 模式）；删除或修正废弃的 `deploy/launchd/ai-radar-pipeline.plist.example`。
- **内部 verify**：`grep -n /Users/lindong deploy/cron/ai-radar-pipeline` → 零；模拟 `install_pipeline` 的 sed 替换后 crontab 行指向 `$REPO_ROOT`（用临时 REPO_ROOT 跑断言）。

### TASK-007 — F：测试同步去个人耦合
- **改**：`tests/test_service_contract.py`、`tests/test_frontend_static_contract.py`、`tests/test_wechat_interpretation.py`。
- **设计**：断言改为相对 `REPO_ROOT`/config 动态推导，或断言占位符默认 + owner-config 两种形态；frontend contract 断言改为"占位符默认下含 your-org，owner env 下含真值"，不再硬断 `lindong28`。
- **内部 verify**：这些测试在 fresh-clone（无 owner env）下通过；`pytest tests/test_service_contract.py tests/test_frontend_static_contract.py tests/test_wechat_interpretation.py` 绿。

### TASK-008 — README / .env.example 总装 + fresh-clone 设置指南
- **改**：`README.md`（clone URL 占位符化、新增"开源用户从零部署"路径、列全新 config 旋钮及默认/owner 值、微信解读标注可选默认 OFF 指向 TASK-002 文档）；确认 `.env.example` 汇总 TASK-001/003 新增项。
- **内部 verify**：README 无 `lindong28`（除非作为示例占位明确标注）；按 README 步骤可走通 V-FORKER#3。

### TASK-009 — 发布前审查 gate（sanitizer + 回归）
- **设计**：A–H 全部落地后，跑 `opensource-sanitizer` agent 做最终 PASS/FAIL gate；执行 L2 全部 V-FORKER + V-OWNER 步骤并贴证据。FAIL 则回流修复，不放行历史清洗。
- **owner 部署自检命令（交付物，治 R3）**：交付清单附一条 owner `git pull` 后可自跑的 observable 自检——`curl -s localhost:PORT/about | grep -q lindong28`（身份旋钮已设）+ 进程内/启动日志确认 `allow_origins` 含 `aiplanet.live`、UA == 字面常量、`interpret` 非 skipped。跑通才算 owner 旋钮设全、零回归成立；不靠"口头提醒"。

### TASK-010 — G：git 历史 filter-repo 清洗（**Codex 执行 / Claude supervise**）
- **前置**：TASK-001..009 全绿（工作树已干净）。
- **执行模式**：用 `codeagent-wrapper` 启动 **Codex** 做重机械活，**Claude 作 supervisor 只验证证据**（用户指定）。Claude 启动后台任务后按全局"Background Agent 巡检"协议盯活性。
- **设计/约束**：
  - **绝不在工作 repo 上重写历史**（aiplanet.live 跑在此 checkout）。在**独立 clone** 上 `git-filter-repo` 产出公开 repo。
  - 清洗目标：全历史中的 `/Users/lindong`、`/research/ai-assistant`、`dong_lin` 路径痕迹；从全历史移除 `docs/plans/`、`deploy/wewe-rss/` 路径。保留 commit 历史（非 squash）。
  - 身份/域名（lindong28/aiplanet.live）属公开 attribution，**是否从历史清洗**按 §Defaulted DD-7 处理（默认保留，因公开身份非隐私；路径/内部规划才是清洗重点）。
  - Codex 须交付：清洗脚本/命令 + 证据（V-HISTORY#7/#8 的命令输出）。
  - **trigger response（破坏性操作的失败回退）**：
    - *no-impact（implementer/supervisor 自主）*：公开 repo 在一次性 **throwaway clone** 上产出；filter-repo 跑挂或结果不符即**销毁该 clone 重跑**，绝不污染工作 repo（工作 repo 全程只读源，不执行任何 filter-repo）。
    - *tree 不一致诊断（V-HISTORY#8 失败时）*：`git diff --stat <public-HEAD> <work-HEAD>` 列出多/少文件，判定是 filter-repo 路径规则过宽/过窄，还是 A–F 在工作 repo 有残留个人信息（后者回 TASK-009 修）。
    - *stop-and-ask 升级*：同一规则多次重跑 tree 仍不一致、或清洗会误删非目标内容时，停下来问用户，不自行放宽/收紧规则蒙混。
- **verify（Claude 验证 Codex 证据）**：复跑 V-HISTORY#7/#8 并贴输出；抽查若干历史提交确认无个人路径；确认公开 repo HEAD tree == 工作 repo 经 A–F 后 tree（除有意排除项）。

---

## UX 契约影响

产品有 `docs/contracts/ux-contract.md`，它是 **canonical 产品（aiplanet.live，owner 部署）** 的用户验收契约。本 plan 触及契约描述的两个表面，逐一裁定：

1. **关于页 `/about`（契约 line 69/233 有 section）**：本 plan 改其**渲染机制**（静态→Jinja + config 注入）与**默认值**（占位符）。但 owner 配置下 canonical `/about` 可观察行为不变（V-OWNER#5a 用 `git show opensource-baseline` 对照守门：维护者/repo/X 链接语义一致）。契约该 section 的"维护者联系方式 / GitHub 链接"现由 `AI_RADAR_SITE_*` config 解析、默认 build 隐藏 X 链接——这属 **canonical 产品契约粒度以下**（owner 设真值后 canonical 表现不变）。→ **无 section delta**，理由：canonical 可观察行为不变。
2. **关于页信源池表格 + AB-1（契约 line 238 `信源数量 ≥20`，line 364 `≥20…20 为保守下限`，calibrate 于"当前约41信源"含已死 nitter 时）**：TASK-004 删 22 个**已死** nitter 源 → canonical 有效源 == 20，**仍满足 ≥20**（删的源本就零内容贡献，canonical 实际内容零变化）。→ **无需改契约**，但 **V-OWNER#5c + V-FORKER#4 显式断言 ≥20** 守住地板线（用户已拍"不新增、保留恰好 20"，零余量为已知可接受状态，见 R4）。
3. **产品概述钉死 `部署在 https://aiplanet.live`（契约 line 10/137/337/350）**：开源使产品从"单实例"变为"canonical 实例 + 可 fork 自部署"。裁定：**ux-contract 是 canonical 产品的验收契约，forker 自部署不在其声明 scope 内**（契约描述 aiplanet.live 这一 canonical 实例，依旧准确）。→ **conscious skip，无 delta**，理由成立（契约 scope = canonical 实例，未扩张到 forker 部署）。

**给 execute-plan 的指令**：不新增 ux-contract section delta；用 V-OWNER#5a（/about 零回归）+ V-OWNER#5c/V-FORKER#4（≥20 地板线）守住契约描述的两个表面；若实施中发现 canonical `/about` 或信源数出现可观察变化，则升级为契约 delta 再处理。

## Defaulted Decisions（planner 自拍，reviewer 可审）

| ID | 决策 | 选择 | 理由 |
|---|---|---|---|
| DD-1 | interpret 开关名 | `AI_RADAR_ENABLE_INTERPRET`（默认 false） | 与现有 `AI_RADAR_*` 命名一致；默认 OFF 合"显式 enable"原则 |
| DD-2 | 站点身份注入机制 | about.html 迁 Jinja 模板 + config 注入 | 复用代码库已有 Jinja 设施（index/all/wechat 同模式），低反转 |
| DD-3 | CORS 默认（domain 未设） | 仅 localhost，**绝不 `*`** | 安全保守；forker 设域名后即放行其站点 |
| DD-4 | 站点 config env 命名 | `AI_RADAR_SITE_DOMAIN/REPO_URL/MAINTAINER/MAINTAINER_URL/X_URL` | 统一前缀；implementer 可微调 |
| DD-5 | B 范围含**部署域名**（非仅身份） | 域名同身份一并 config 化 | CORS 硬编码 aiplanet.live 会阻断 forker 站点，开源目标必需；同机制 |
| DD-6 | 公开 repo 形态 | 工作 repo 历史不动，filter-repo 在 clone 上产出公开 repo | 保护 aiplanet.live（跑在工作 checkout）；满足"保留历史+清洗" |
| DD-7 | **历史**是否清洗公开身份(lindong28/aiplanet.live) | **不清洗历史**（仅清历史中的私有路径/内部规划）。**注意：最新快照仍按 B 全泛化**（V-FORKER#1 要求快照零命中 lindong28/aiplanet.live） | 公开 attribution 非隐私，历史留存无害；快照泛化与历史清洗是两件事，不冲突 |

## Risks / TODO

- **R1**（破坏性历史重写，acceptance + trigger 见 TASK-010）：filter-repo 必须在 throwaway clone 上做、工作 repo 全程只读；失败回退/诊断/升级见 TASK-010 trigger response。
- **R2**（**已确证事实**，非待验证）：`src/airadar/web/app.py:357` `StaticFiles(html=True)` 使残留 `web/static/about.html` 经 `/about.html` 绕过新模板路由暴露旧个人身份。**处置已落 TASK-003**（迁移时 `git rm` 静态副本；no-impact 备选=注册 `/about.html` 重定向）+ **V-FORKER#3 断言** `/about.html` 不返回旧内容。
- **R3**：owner `.env` 新增旋钮若漏设，其站点 `/about` 退化为占位符身份、CORS 退化为仅 localhost（破坏零回归硬约束）。trigger response = **TASK-009 的 owner 部署后自检命令**（observable signal，owner 自跑确认旋钮设全），不靠口头提醒。
- **R4**（已知可接受）：用户选"不新增、保留恰好 20"，canonical 信源数 == AB-1 地板线 20、**零余量**；任一源死亡即跌破自身契约下限。本 plan 不扩源（尊重用户决策），V-OWNER#5c/V-FORKER#4 断言 ≥20 为守门；后续若频繁跌破，属运维层（A4 告警 / 补源）另议，不在本 plan scope。
- **TODO**：`deploy/lib/services.sh` 服务标签 `live.aiplanet.ai-radar.*` 泛化为低优 cosmetic，**不阻塞发布**；若动，注意它是 owner 已 load 的 launchd label——改名需 owner 重新 `bootout`+`bootstrap`，故默认留 TODO 不在 TASK-003 动。

## 执行模式说明

- TASK-001..009 走常规 implementer 流程（可 `/custom:execute-plan` 或新 session 实现）。
- **TASK-010** 按用户指定：`codeagent-wrapper` 启 Codex 执行历史清洗，Claude supervise + 验证证据；遵循全局 Background Agent 巡检协议。
