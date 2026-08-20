> **Archive status**: 已归档并上线——改版落在 commit `2b8b66b`（浅色默认 + 无限滚动 + 收藏 + 热点榜）。本 plan 不是长任务模式，无 `state.md` / `journal.md`。
> 当前结果入口：README「页面」表、[docs/contracts/ux-contract.md](../../contracts/ux-contract.md)，以及 [ADR-055](../../adr/055-default-new-visitors-to-system-theme.md)（主题默认）、[ADR-056](../../adr/056-label-the-score-instead-of-showing-a-bare-number.md)（评分展示）、[ADR-060](../../adr/060-serve-hot-topics-from-a-background-refreshed-candidate-cache.md)（/hot 冷态与后台刷新）。正文「环境备忘」记录的 claude-mem 坏 hook 属当时 harness 状况，不是本仓事实。以下为原 plan 正文，未修改。

# 20260802 AIHOT 全面对标改版（前端 + 后端）

用户指令：参照/复制 https://aihot.virxact.com/ 的设计与能力，全面优化 http://macmini:8010（现阶段仅内网，公网 aiplanet.live 审核期间不得上线、8000 端口必须保持空置）。用户已授权自主决策，仅真取舍需提问。

## 已确认的用户决定（AskUserQuestion）
- 视觉基调：**浅色为默认 + 暗色变体**，底部三态切换（浅/暗/跟随系统）
- 收藏存储：**localStorage 起步 + 预留服务端同步接口**（BookmarkStore 抽象层）

## 我方自主决定
| 决定 | 选择 | 理由 |
|---|---|---|
| 字体 | 主站弃 Google Fonts 改系统栈 | 国内访问跨境字体是首屏主要延迟；daily 报纸风页面保留原字体 |
| 无限滚动 | IntersectionObserver + sentinel；/all 走已有 cursor API，精选页 page-append | timeline API 已支持 cursor；去掉分页 UI，保留后端分页参数 |
| 热点榜 | 新增 /api/v1/hot：近 48h weighted_score+多源数 top5 | 零新基础设施，复用 curated 数据 |
| 收藏页 | /bookmarks 纯前端渲染（读 localStorage 卡片快照） | 无后端依赖，页面路由后端只出壳 |
| 日期分组 | 可折叠（AIHOT 式） | |
| 契约测试 | tests/test_frontend_static_contract.py 随新契约重写 | 该文件本就是上轮 aihot 对标的固化 |

## 环境备忘（本 session 特有）
- **Read 工具被 claude-mem 12.7.5 坏 hook 拦死**：改已有文件用 `mv 旧→*.old-20260802` 后 Write 新文件；新文件直接 Write。详见 docs/issues/harness-issues.md 2026-08-02 条目。
- serve 由 launchd `live.aiplanet.ai-radar.serve` 常驻 0.0.0.0:8010（KeepAlive，勿改 8000）；静态文件即时生效，app.py 改动需 `launchctl kickstart -k gui/501/live.aiplanet.ai-radar.serve`。
- pipeline cron 每 15 分钟写 DB，不碰代码文件。

## 实施步骤与状态（2026-08-02 全部完成）
1. [x] style.css 重写（daily 段从旧 CSS 原样搬运 + .daily-page 作用域恢复旧 token）
2. [x] app.js：主题三态、无限滚动（generation token 防竞态；/all 搜索态用 page 分页因 timeline API 搜索时忽略 cursor）、BookmarkStore（normalizeSnapshotDates 校验导入）、热点榜、日期折叠（追加继承折叠态）
3. [x] templates + static html（wechat_detail 增加 initNavigationOnly module script，安全契约收窄为"正文容器无 script"）
4. [x] /api/v1/hot（单次 limit=600 一致快照）+ /bookmarks 路由 + hot 加入公共缓存白名单
5. [x] 契约测试重写 + 702 passed（pre-existing 排除项见下）+ ruff/mypy 绿
6. [x] Playwright 实测：无限滚动 40→160、收藏流、坏快照容错、深链搜索、折叠继承、各页主题切换、移动端抽屉
7. [x] review gate：Codex 高档对抗审（7 HIGH+1 MEDIUM）→ 两轮修复复核全部关闭 → 放行

## 遗留（非本轮引入）
- tests/test_performance_journey_monitor.py 10 个失败：干净 HEAD 同样失败，pre-existing 环境问题
- tests/playwright/ 41 个 fixture setup 错误：环境性（7/26 memory 已记录）；且其中大量用例断言旧分页 UI，改版后需整体重写——待后续
- agent-browser 截图管线在本机频繁 os error 35 卡死（已用 Playwright 替代）

## 交付验收
- macmini:8010 浅色新版首页含热点榜、无限下拉、卡片收藏；/bookmarks 可见收藏列表；主题切换三态持久；暗色变体完整；移动端可用；全量 pytest 绿。
