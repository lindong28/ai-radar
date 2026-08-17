# Frontend 经验

> Append-only. 前端开发相关的坑点和 pattern.

## 2026-05-24 日期分组与时间显示的时区必须一致

- Problem: 前端 date bucket 分组使用 `Asia/Shanghai` 时区，但条目的可见时间（`HH:mm`）使用浏览器本地时区渲染。当用户不在 `Asia/Shanghai` 时区时，条目的显示时间与所在的日期分组不一致（例如一条 23:50 CST 的条目在 UTC 时区显示为次日，但仍在前一天的分组里）。
- Solution: `web/static/app.js` 中的 `timeKey()` 格式化时统一使用 `Asia/Shanghai` 时区，使渲染时间与日期分组对齐。
- Applies when: 修改前端日期/时间显示逻辑时——所有时间格式化必须使用与分组相同的时区（`Asia/Shanghai`），不能依赖浏览器默认时区。

## 2026-08-03 动态生成的标识符在源码里不以字面量存在——grep 判"是否被使用"必然漏

- Problem: 判定某个 class / DOM 属性是否真被使用时，`grep` 源码会给出错误答案。本轮踩到四次：`data-theme-mode` 在源码里 0 命中（实现是 `r.dataset.themeMode`，camelCase 只在 DOM 里才变连字符）；`hot-rank-number-1` 在模板里 0 命中（Jinja `hot-rank-number-{{ loop.index }}`）；`hot-topics-rank-rest` 在 app.js 里 0 命中（模板字符串 `${index < 3 ? index + 1 : "rest"}`）；`cl-p` 在 `web/` 下 0 命中（由 `src/airadar/web/app.py` 的 markdown token 渲染器赋值）。
- Solution: 凡是"某标识符是否被使用 / 某语义是否存在"的判定，一律对**运行时产物**做——Playwright 遍历路由收集 `document.querySelectorAll('*')` 的 classList，或直接读 `getComputedStyle`。清理死 CSS 时尤其危险：按源码 grep 的结果去删，会删掉真在用的规则。
- Applies when: 清理死 CSS、审查 provenance、断言"某元素/属性不存在"、判断某 token 是否有消费方。扫描面还必须覆盖**全部**产出方（Jinja 模板、`app.js`、Python 渲染器），不能只扫最像的那几个文件。

## 2026-08-03 静态资源改动即时生效，Python 改动才需重启——验证跑必须与实施串行

- Problem: 在一个 Codex implementer 正在改文件时跑了全套 Playwright，得到 23 failed（基线 20），差点当成回归。实际是服务直接从磁盘服务 `style.css` / `app.js` / 模板，这些改动**无需重启即时生效**，于是测到了一棵"CSS 已落、模板未落"的半成品树——其中 `test_mobile_closed_sidebar_is_not_in_tab_order` 失败恰恰是因为实施方按指令删掉了汉堡抽屉。
- Solution: 8011 上的任何验证动作（Playwright 全套、截图采集）只在没有 implementer 在跑时执行。
- Applies when: 委派前端改动给后台 agent 期间。反直觉点在于不对称——Python 改动要重启才生效，容易给人"不重启就看不到改动"的错觉，静态资源恰恰相反。收敛期的对照采集尤其要守这条，那时报告会被当成交付证据。

## 2026-08-03 「最新」在本项目有三个不同定义，混用即 bug

- Problem: 日报报头渲染出 `VOL.2026.08.04`（未来日期）下面挂着今天的文章。根因是三个语义被混用：feed 的 `published_at` 不受信任、可以是未来，而"日报最近一期"必须 `<= today`。归档聚合没有上限，前端又忽略了响应里的 `data.date`（服务端已钳制），于是未来桶成了 `archiveDays[0]`。
- Solution: 归档 SQL 加 `date(...) <= date(datetime('now','+08:00'))` 界；前端以响应的 `data.date` 为 authority 渲染报头/归档选中态/URL。三个语义的分工：**列表最新条目日期**（`max(published_at)`，可以是未来——首页副标题日期前缀有意要这个）／**日报最近一期**（归档中 `<= today` 的最大日期）／**响应生成时刻**（`generated_at`，`/hot` 的 `event_time` 用它钳制）。
- Applies when: 写任何"取最新"的代码或测试断言前，先确定是哪一个语义。一个 Playwright fixture 曾用第一个语义去断言第二个语义而失败。

## 2026-08-03 一致性断言对"两处都错得一样"是盲的

- Problem: 验收日报时我断言"VOL 日期与报头 `datetime` 属性一致"——通过了，而两处都是同一个未来日期。一致性成立、语义荒谬（最近一期怎么会是明天），缺陷一路过关，直到独立 reviewer 问"这个日期合理吗"。
- Solution: 断言要覆盖**合理性**而非只有**自洽性**：日期类断言加绝对边界（`<= today`），计数类断言加独立重算（如从 DOM 自己数一遍 CJK 字符再套公式，而不是信页面显示的数）。
- Applies when: 写验收断言时。写完先问一句"如果实现是错的、但错得整齐，这个断言会不会仍然绿"。

## 2026-08-17 静态资源漏 bump `?v=` 会静默失效 20 小时，且本地与 curl 都看不出来

- Problem: AIHOT 源对齐（`ab9442d`）改了 `web/static/app.js` 却没 bump 各 HTML 的 `?v=`。EdgeOne 按 ADR-039 对 `/app.js` `/style.css` 强制节点缓存 7 天，于是部分边缘节点持续 20.7 小时（`age: 74582`）吐部署前的旧 JS，线上 `/about` 显示了本该消失的 34 个 `enabled=0` 停用源（旧 JS 走 `/api/v1/sources`，该端点按契约保留停用行；新 JS 走 `/api/v2/sources` 只返回启用源）。诊断难点在于同一 URL 连打两次给出不同结果——第一次 `eo-cache-status: HIT` 含旧内容，第二次 `MISS` 是新的，取决于命中哪个边缘节点。
- Solution: 改这两个文件时，bump **引用它的全部 HTML**（`web/templates/*.html` + `web/static/*.html`），不按"哪些页面用到本次改的 CSS 类"收窄——那条旧规则（`docs/plans/20260607-wechat-read-original-link/plan.md:98`）成形于 EdgeOne 接入之前，且判据不可执行：style.css 的 286 个 class token 里有 **64 个**（`hot-topics-*`/`empty-state`/`detail-card` 等）只由 app.js 运行时生成，在全部 20 个 HTML（`bump_frontend_assets.html_files()` 的口径：12 个模板 + 4 个 partial + 4 个 `web/static/*.html`）源码里 grep 为 0，按该判据会得出"bump 0 个文件"。找法用 `git grep -l "app.js?v=" -- 'web/**/*.html'`，**pathspec 不能省**——不加会命中 `docs/architecture.md` 的示例、plan 归档，以及 `tests/test_frontend_static_contract.py` 的 `?v=one`/`?v=two` 、`tests/test_admin_access_log.py` 的日志样本与 `tests/playwright/test_aihot_parity_journey.py` 的 `?v=gap67-test`，那些是故意的 fixture，照"零残留"去清会破坏测试。`web/templates/wechat.html` 不在结果里且**不是遗漏**：它经 tracked symlink `_wechat_inline_style.css` 把整份 style.css 内联进 SSR HTML，刷新走 ADR-039「决策」节里那条内联 CSS 契约（symlink 语义 + ~120 秒 stale-while-revalidate 窗口 + 必须在窗口后从真实公网 `/wechat` 验证内联内容）。
- Applies when: 任何改动 `web/static/app.js` 或 `web/static/style.css` 的任务。三个易错点：(1) 约束的边界是**发布单元**不是本地 commit——按 ADR-042 生产部署 commit `D` 是在 `tencent/main` 上复放出来的，复放时挑漏 HTML 则本地 commit 再完整也破契约；(2) 已经漏 bump 且改动已上线时的处置：**默认走补 bump + 重新部署**，这条 agent 自己能执行——新版本串就是新 URL，边缘无副本必然回源，旧 URL 缓存里还有什么已不再有人引用；仍持有旧 HTML 的访问者分三档：`/` 与 `/wechat` 有约 120 秒尾巴（`src/airadar/web/app.py:47,50` 只对这两条路径发 `max-age=90, stale-while-revalidate=30`）；`/all`、`/about`、`/hot` 等 Jinja 渲染页不发 Cache-Control 也无验证器，即时恢复；`/daily` 与 `/item.html` 由 StaticFiles 服务，**带 `Last-Modified` 与 `ETag` 而无 `Cache-Control`**，会触发浏览器启发式缓存（RFC 9111 §4.2.2，常见实现取 `(now − Last-Modified)` 的 10%），文件越久没改窗口越长。EdgeOne 精确清除旧 query URL 只在"不能等下一次部署"时才用；配了 `EDGEONE_*` 凭据后由 `./run.sh admin edgeone purge --url ...` 执行（未配置时仍须转交用户去控制台），且 ADR-039「决策」节把它定位为发布前的替代方案而非事后补救；(3) 别手改版本串，跑 `uv run python scripts/bump_frontend_assets.py`（改 tag 用 `--label 20260817-<短标签>`，`--check` 是只读模式、即测试断言的那件事）。版本串格式是 `<label>-<sha8>`，**后 8 位必须等于资源内容的 sha256 前 8 位**——这条不是洁癖，是守卫唯一真正生效的地方：设计过程中先后有两版被证伪，纯一致性断言在"全都没 bump"时仍然一致（本文件 2026-08-03「一致性断言对'两处都错得一样'是盲的」），加了内容 pin 之后评审者仍实测出绕过路径（改资源 + 只更新 pin 的 sha + 零个 `?v=` 被 bump → 全绿，而那恰是失败消息把新 sha 递到手里后阻力最小的一条）。把版本串派生自内容才让"改了资源却留着旧版本串"在数学上不可能。教训一般化：**变异对照要照着"修复者会怎么偷懒"设计，不是照着"什么都不改"设计**。
