# AI Radar · UX Issues

> 配对 [`ux-contract.md`](../contracts/ux-contract.md) 的 issue ledger。装当前 product 已确认的 user-observable 问题。
>
> 协议：`~/.claude/references/ux-test-protocol.md` §3。
> 状态语义：`pending` 已发现未处理 / `in_progress` 正在修 / `done <date>` 已修复并验证 / `cancelled` 决定不修。

---

## Issues

- [done 2026-06-08] `/wechat` 搜索对空格敏感：`分享Claude Code`（库内无空格）能搜到，`分享 Claude Code`（带空格）搜不到
  - 背景：用户 2026-06-08 反馈带空格搜不到内容，体验不好。
  - 根因：`src/airadar/web/routes/common.py` 的 `expand_st_variants` / `like_patterns_for_query` 只 `strip()` 首尾、不归一化内部空格，被匹配列（title/author/abstract/tags）也不归一化；LIKE pattern `%分享 Claude Code%` 命中不了存库的 `分享Claude Code`。
  - 影响面：同一搜索通道也服务 timeline/curated，修一处全局受益。
  - 修复：搜索做空格不敏感——查询 pattern 与被匹配列两侧都剥除空白（含全角空格）后比对，并补 FTS 路径（timeline/curated 长查询走 trigram FTS）。加回归测试复现该用例；89 测试通过、ruff clean。**需重启 serve 生效**（纯 Python web 层改动）。

- [done 2026-06-08] `/wechat` 公众号「赛博禅心」头像缺失（回退显示首字"赛"，被读作头像不对）
  - 背景：用户 2026-06-08 反馈该公众号头像不对。
  - 根因：生产库 `data/radar.db` 中它是 15 个账号里唯一 `avatar_url` 为空者——2026-06-02 Playwright 抓 `round_head_img` 失败，落 7 天负缓存（`fetcher/runner.py` `WECHAT_AVATAR_NEGATIVE_CACHE_TTL=7d`），到期前不重试，页面回退显示首字。
  - 修复：新增 `admin wechat-avatar refresh --account <名>` CLI（清该账号缓存 + 实抓），对 赛博禅心 实跑 Playwright 重抓成功，`avatar_url` 填入有效 `mmbiz.qpic.cn` URL（非空头像 14→15，已生效无需重启）；抓取失败负缓存 TTL 由 7 天缩到 2 天让偶发失败更快自愈。手动覆盖未用到。加 CLI + TTL 回归测试。

- [cancelled] 微信文章解读：解读内容质量验收（用户 2026-06-02 决定不验）
  - 背景：create-ux-contract 时 de-scoped（功能流程 + 视觉交互优先）。execute-ux-contract round 1 的机制/功能/视觉验收（WX-1~9）全过后，用户决定**不再单独验内容质量**——渲染正确即足够；解读忠实/有用、"值得读"判定合理性、标签语义交由 summarizer（ai-assistant summarize-article）上游保证。
  - 原计划（不再执行）：抽样 `/wechat` 详情对照原文微信文章判断忠实/有用 + 值得读判定是否合理 + 标签语义。
  - 机制验收记录：`plans/20260602-2034-ux-contract-ux-test/`（TS-001~008 PASS，TS-009 不可自然到达）。
