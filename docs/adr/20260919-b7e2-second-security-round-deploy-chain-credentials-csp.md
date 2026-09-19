# 第二轮安全审查：部署链验签与根路径保护、凭据面收窄、读时 URL 门、CSP 迁移

- Status: accepted
- Date: 2026-09-19
- Supersedes: [ADR-003](003-dual-dotenv-loader.md) 的「加载全部键」语义（部分）

## Context

[20260919-a3c1](20260919-a3c1-harden-public-surface-after-security-review.md) 之后的第二轮审查覆盖第一轮没碰的面（CLI 与 admin 子命令、Mac 侧自动化脚本、前端全文、DB 同步与代码部署链的 Python 实现、X / interpret / 微信正文三条数据流）。经复核的承重发现：

- **部署链**：一次 `git push` 到服务器 bare repo 即以 `ubuntu`（免密 sudo）执行候选树代码——`verify_candidate` 在候选树里 `uv sync --locked`、`import airadar.cli`、跑候选树自己的 `schema_gate.py`。`_is_runtime_owned` 只守 `data/` 前缀不守 `data` 本身，reviewer 本地真实复现：track 一个 `data -> /etc` symlink 的 commit 让 `checkout-index -f` 删掉两份生产库，git-only 回滚无法恢复。`quarantine/` 已 13 GB 无保留策略。
- **凭据面**：`cli.main()` 把 `~/.claude/.env` 全部 126 个键加载进 `os.environ`（实测阳性对照：GITHUB_TOKEN、CLOUDFLARE_API_TOKEN 等 7 个探针键全部进入），再原样传给 interpret / wechat_kb 的外部子进程与局域网暴露的 serve 进程。
- **interpret**：外部 summarizer 返回的 `slug`、`batch_dir`、`summary_file_path` 未限定目录，可读任意 `*_summary.md` 发布到公网、写任意 `*_meta.json`；子进程无超时；`error` 列存原始 stderr。
- **渲染**：模板与 `app.js` 只做实体转义不查 scheme；入库前白名单不回扫历史行（库里有一条 `about:blank`）；头像 URL 只校 scheme 不校 host；`sources.toml` 的 URL 经 `os.path.expandvars` 整段展开，一条配置就能把任意环境变量拼进出站请求；CSP 仍是 Report-Only 且带 `'unsafe-inline'`。

## Options Considered

### 部署链：验签 vs 只堵历史改写
- **A. pre-receive 验签（选定）**：SSH commit 签名 + 服务器 `allowed_signers`，hook 拒绝未签名 / 非快进 / 非 main / 删 ref；`receive.denyNonFastForwards` / `denyDeletes` / `fsckObjects`。防的是部署 key 单独泄露；不防 Mac 整机被控（签名 key 也在 Mac）。代价：每个推到 `tencent` 的 commit 必须签名。
- B. 只加 deny* 与 fsck：不改工作流，但 push 即执行代码不变。否决。
- C. 把候选验证挪到沙箱用户：正确的长期方向，但要迁移文件归属与 sudoers，另立任务。

### 凭据面：allowlist vs 逐键 `read_value`
- **A. `load_runtime_env` 按声明 allowlist 加载（选定）**：`AI_RADAR_*` 前缀 + `.env.example` 声明的非前缀键；测试断言 `.env.example` 每个键都在名单里，避免漂移。`read_value(key)` 保持可读任意键（调用方显式点名）。
- B. 全部改 `read_value`：改动面大（几十个读取点），收益相同。否决。

### 渲染：入库回扫 vs 读时门
- **A. 读时 `public_url()`（选定）**：`item_summary` 与 `/wechat` 的 `url` / `source_homepage_url` 出口一处归一，覆盖全部 sink；历史行不必回扫。
- B. 一次性 UPDATE 历史行：已对本地库做（那条 `about:blank` 已删并备份），但不能替代读时门——它只管今天的库。

### CSP：hash vs nonce vs 外置
- 公开页在 EdgeOne 缓存 90 秒，nonce 不可用。14 个 HTML 的内联 module 引导块改成 `<script src>` + `<body data-init>`，`app.js` 末尾按 `data-init` 分发；`onload`/`onerror` 属性改 document 级捕获监听（init 时补扫 `img.complete`）；hot 页自动重载搬进 `app.js`；主题 FOUC 脚本 14 处字节相同，用一个 sha256 放行。新策略先以第二条 Report-Only 双发一周，无违规后改头名即强制。

## Decision

1. **部署链**（`deploy/sync/deploy_code.py`、`apply_db_update.py`、`schema_gate.py`、新 `deploy/server/pre-receive`、`install-server.sh`）：`_is_runtime_owned` 覆盖 `data` / `logs` / `.venv` 根与 `.env` / `.deployed-sha` / `.git-deploy-index`；候选树中落在这些根上的 symlink / gitlink、任何 gitlink、目标逃出仓根的 symlink 一律 REFUSED；`git read-tree` 校验先于 `_apply_deletions`；quarantine 只保留最近 `AI_RADAR_QUARANTINE_KEEP`（默认 2）份；`schema_gate` 表名须匹配标识符；pre-receive 验签（allowed_signers 路径取 bare repo 自己的 `git config ai-radar.allowedSignersFile`，不读环境变量），`install-server.sh` 安装 hook 与三条 `receive.*` 配置。
2. **凭据面**（`runtime_env.py`、`interpret/runner.py`、`admin/wechat_kb.py`、`egress.py`）：allowlist 加载；interpret / wechat_kb 子进程 env 只带进程基础变量、`AI_ASSISTANT_ROOT`、ARK breaker 与本项目自己的 LLM 提供商键（不带 X token、EdgeOne、Feishu、admin token、图片代理 URL）；`batch_dir` / `summary_file_path` 必须 resolve 到 `$AI_ASSISTANT_ROOT` 内，`result.slug` 须匹配 `[0-9A-Za-z一-鿿_-]{1,200}`；子进程 900 秒超时；`error` 列脱敏；`open_external_url` 只接受 http(s)。
3. **渲染与输入**（`presentation/media.py`、`summary.py`、`routes/wechat.py`、`fetcher/wechat.py`、`fetcher/x_api.py`、`sources/loader.py`、`routes/media.py`、`app.py`、`wechat_detail.html`）：`public_url()` 读时门；头像只接受 `qpic.cn` / `qlogo.cn`，`qlogo.cn` 一并走 `/img`；X 媒体只接受 `pbs.twimg.com` / `video.twimg.com`；`sources.toml` 只展开整 URL 即一个 `${VAR}` 的形态，嵌入式引用拒绝；`_CREDENTIALS` 正则去歧义；`/wechat/{slug}` 的 `q` / `page` 加边界；`published_at` 为空不再 500；KB 导入 slug 过 `wechat_slug_seed`，detail URL 百分号编码。
4. **CSP**：见上；nginx 双发第二条 Report-Only。

## Consequences

- 生产要三步才生效：部署代码（本次起须签名 push）、服务器安装 pre-receive 与 `allowed_signers`、nginx reload。上一轮的 systemd 沙箱指令本轮才真正装到线上（部署钩子不重装 unit，先在空闲 slot 验证，随下一次 DB 切换生效）。
- interpret 契约收紧（`docs/references/ai-assistant-contract.md`）：外部 summarizer 返回的路径若逃出 root，该文章记为 error；`--check-url` 命中的逃逸路径视同未命中。
- `sources.toml` 若将来需要在 URL 里嵌入变量，会被拒绝；whole-URL 占位符形态不变。
- CSP 强制头一周后再切；切换只改 nginx 一行。`/bookmarks` 导出（`blob:` 下载）在强制策略下未实测。
- 仍未做：serve 独立服务用户与 sudoers 收窄（`ubuntu` 无密码，收窄会锁死交互 sudo，且同主机 sjtu-aaa 部署链依赖它）；ubuntu 可写的 nginx upstream include 被 root nginx 读取（sudoers 收窄时要一并处理）；RSS 抓取响应大小与总 deadline；`performance-remediate` 的 prompt 注入面（当前未调度）；capture 脚本的 `eval`、im-notify 消息走 argv、healthcheck token 进 URL、`pipeline.sh` `source .env`、alert plist 内嵌 webhook（均 Low，见审查报告 05）。
