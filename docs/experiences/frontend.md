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
