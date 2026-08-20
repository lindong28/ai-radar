> **Archive status**: 已归档。本 plan 是一次性的 UX 契约端到端测试脚本，不是产品契约本身；执行过程产物 `state.md` / `journal.md` 按长任务协议不入档。
> 被测契约的当前权威是 [contracts/ux-contract.md](../../contracts/ux-contract.md)「微信文章解读页（`/wechat`）」「解读详情页（`/wechat/<slug>`）」两节与 WX-1～WX-9，正文中的验收措辞可能已被后续演进取代。以下为原 plan 正文，未修改。

# UX Contract Test Plan — 微信文章解读

> ⚠️ **Long-task mode** — 本 plan 处于长任务模式
> - 进度状态：`./state.md`
> - 决策日志：`./journal.md`
> - 协议详情：`~/.claude/references/long-task-protocol.md`
>
> 实施时（含 compact 之后）必须先读 state.md 和 journal.md 再决定下一步动作。
> 声称任务完成前必须实际跑本 plan 的 verify 步骤并贴出可观察证据。

## 来源 contract

`docs/contracts/ux-contract.md` —— §微信文章解读页（L1）+ WX-1~WX-9（L2）。

## 产品访问

- **入口**：生产 https://your-domain.example/wechat ；本地等价 http://127.0.0.1:8000/wechat（同代码、只读同一 radar.db）。本地等价实例延迟低、适合做密集断言；至少在 1-2 个核心 step 上用生产 URL 确认真实部署一致。
- 公开只读，无需认证。读操作无副作用。当前"值得阅读列表" ~153 篇（随 15 分钟 cron 增长——所有篇数为示意，执行时实测）。
- **工具**：agent-browser 做浏览器交互（open / snapshot -i / click）；**截图一律用 Playwright**（`PYTHONPATH=src uv run python` + `from playwright.sync_api import sync_playwright`，`page.screenshot(full_page=True)`）——agent-browser `screenshot` 在本机会挂起并 wedge daemon（见 ai-agent-config HARNESS-006），禁用。

## 范围

本轮只测 ux-contract 的「微信文章解读」段（WX-1~WX-9）。精选 / 时间线 / 日报 / 关于（HP/TL/DY/AB/RS/FH）为既有未变内容，不在本轮范围。**解读内容质量按 contract 验收侧重 de-scoped**（WX-6 只验渲染正确，不判断解读忠实/有用/值得读判定是否合理）。

---

## Test Steps

### TS-001 ← WX-1 侧栏入口与导航
- **操作**：依次打开 `/`、`/all`、`/daily`、`/about`、`/wechat`；在每页查侧栏是否含「微信文章解读」链接；从其中任一页点击该链接；在 `/wechat` 查该链接是否高亮（active）。
- **观测**：5 个页面侧栏均有该链接；点击导航到 `/wechat`；`/wechat` 上该链接有 active 态。
- **pass**：5/5 页面含链接 + 点击可达 `/wechat` + `/wechat` active 态存在。
- **L3**：HTML 含 `side-link-active` 类在 `/wechat` 的该链接上。

### TS-002 ← WX-2 列表加载与卡片
- **操作**：打开 `/wechat`，观察卡片。
- **观测**：≥10 张卡片；每张可见 公众号头像+名称、原文标题、摘要、话题标签(≥1)、推荐等级徽标(必读/值得一看/可跳过)、发布时间；按发布时间从新到旧；整张卡片可点击进详情。
- **pass**：卡片数 ≥10 + 六类字段齐全 + 倒序 + 点卡片主体（非标题）也进详情。
- **L3**：`/api/v1/wechat` 字段齐全；`published_at` 倒序；卡片 `data-detail-url`/role=link。

### TS-003 ← WX-3 列表收录口径
- **操作**：确认列表展示"值得阅读列表"文章；确认列表中可出现推荐等级"可跳过"的卡片（偏召回）；取一个未进列表的文章 slug 直接访问详情。
- **观测**：列表非空；可见个别"可跳过"徽标卡片；未进列表文章详情显示 404。
- **pass**：列表有内容 + 偏召回（可含可跳过）+ 未收录文章 `/wechat/<slug>` 显示 404。本条**不**对"是否真的值得读"做质量判断。
- **L3**：API recommendation 分布（必读/值得一看/可跳过）。

### TS-004 ← WX-4 分页与上下文保留
- **操作**：翻页（下一页/上一页）；访问越界页码 `?page=<远超最后一页>`；从第 2/3 页点开一篇详情，点"‹ 返回列表"。
- **观测**：翻页加载新内容不重复、可返回上页；越界页码回到最后一页且**不**显示"暂无微信文章解读"空态；从第 N 页进的详情返回回到第 N 页。
- **pass**：分页正常 + 越界 clamp 无误导空态 + 返回保留分页上下文。
- **L3**：API `total`/`page`/`limit`。

### TS-005 ← WX-5 详情页渲染
- **操作**：点一张卡片进 `/wechat/<slug>`；查模块、返回链接、公众号+日期、标签；复制 URL 在新浏览器上下文直接打开。
- **观测**：解读分模块呈现，模块标题为结构化标题（非裸 markdown 源码），至少含 文章概况/独特亮点/可动手实践/可复用认知/关键词/价值判断（部分文章有额外模块）；含"‹ 返回列表"、公众号名+发布日期、话题标签；独立打开 URL 仍正常渲染。
- **pass**：模块渲染为结构化标题（如 `<h3>`）+ 三要素齐全 + URL 可独立分享渲染。
- **L3**：详情 HTML 含 `<h3>` 模块标题；含 `detail-back`。

### TS-006 ← WX-6 内容渲染正确性（只验渲染，不验质量）
- **操作**：扫多张卡片标题/摘要 + 1-2 篇详情正文。
- **观测**：标题/摘要/正文为可读中文，非乱码或纯英文原文；无外露转义字符（字面 `\n`）。
- **pass**：中文可读 + 无字面 `\n` + 非纯英文。**解读忠实/有用/值得读判定合理性本轮 de-scoped，不判断。**
- **L3**：API 标题 grep 字面 `\n` / 真实换行符（应为 0）。

### TS-007 ← WX-7 边界·未知 slug 404
- **操作**：访问 `/wechat/<不存在的 slug>`。
- **观测**：HTTP 404；页面在站点框架内——暗色主题 + 侧栏导航 + "返回列表"入口，非裸白页。
- **pass**：404 状态 + 站内框架（侧栏 + 返回链接 + 暗色）。

### TS-008 ← WX-8 响应式与风格一致
- **操作**：以 390px 移动视口打开 `/wechat` 列表 + 一篇有"可动手实践"表格的详情（如 pullfrog 文）；对比 `/wechat` 与 `/all` 的暗色风格/字体/卡片样式。
- **观测**：移动端正常渲染；暗色配色/字体/卡片样式与 `/all` 一致；详情内表格不被挤压（可横向滚动），正文不需横向滚动。
- **pass**：390px 渲染正常 + 风格与 `/all` 一致 + 表格横向滚动不挤压。**截图用 Playwright（list-mobile + detail-mobile-table）。**
- **L3**：style.css 含 `overflow-x: auto` 表格规则。

### TS-009 ← WX-9 空状态（可达性受限）
- **操作**：尝试经真实用户路径到达"值得阅读列表为空"的状态。
- **观测**：`/wechat` 无任何文章时显示友好空态（如"暂无微信文章解读"）。
- **pass / 可达性**：当前列表有 ~153 篇，**无真实用户路径**可让列表为空（v1 无筛选/搜索）。按 `plan-execution-principles.md` §4 + `ux-test-patterns.md` P11：**注入/置空只能诊断空态 markup 是否存在，不算用户视角 pass**。如确实不可经真实路径到达 → 据实记为"不可自然到达"，不写 pass，作为信息项交 handoff（非缺陷）。
- **L3**：确认空态文案 markup 存在（`web/static/app.js` 的空态分支）作为诊断证据。

---

## 备注

- 本 plan 多数 step 在 contract review 阶段已对线上做过点验，本轮是**系统化逐条 L2→test step 的正式验收记录**。
- 端到端结论必须来自真实部署入口（生产 / 本地等价实例），mock 仅作辅助诊断。
