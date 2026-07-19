> ⚠️ **Long-task mode** — 本 plan 处于长任务模式
> - 进度状态：`./state.md`
> - 决策日志：`./journal.md`
> - 协议详情：`~/.claude/references/long-task-protocol.md`
>
> 实施时（含 compact 之后）必须先读 state.md 和 journal.md 再决定下一步动作。
> 声称任务完成前必须实际跑本 plan 的 verify 步骤并贴出输出。

# execute-ux-contract test plan — 精选页改为累积归档 + 分页

## 来源契约

- ux-contract：`docs/contracts/ux-contract.md`
- 本轮聚焦 L2：**HP-1（精选加载展示）**、**HP-8（分页·累积归档，新增）**、HP-3/HP-4（分类筛选/搜索在归档+分页下仍成立）
- 触发：用户要求精选页 `/` 从"最新一轮 top-40 单页"改为"**所有曾被精选的不同条目的累积归档**，按发布时间倒序、用与 `/all` 一致的隐式数字页码分页"。**不加显式总数文本**。

## 设计（已与用户锁定，不要再问）

| 维度 | 决策 |
|---|---|
| 范围 | 所有曾被选入精选的不同条目（跨历次 curation run 去重，每条一次），**不**按当前阈值二次过滤——历史精选档案 |
| 去重 | 每个 item_id 只出现一次，用其**最近一次**被精选的 curated 元数据（分数/rank/理由） |
| 排序 | **按 published_at 倒序**（与 /all、/wechat、/daily 一致；第 1 页=最新精选） |
| 分页 | 与 `/all` **完全一致**的隐式数字页码（首末页固定 + 当前页±2 + … 省略 + 上/下一页箭头 + 可点任一页直跳），40 条/页 → 当前 ~45 页 |
| 筛选/搜索 | 保留分类筛选 + 搜索，在归档范围内生效；分页 total/末页随筛选变化（同 /all） |
| 与 /all 区别 | `/` = AI 精选 best-of（~1792 去重）；`/all` = 全量相关池子（~6417）。归档是 /all 的优选子集 |
| 显式总数 | **不加**"共 X 条/页"文本（用户已明确） |

## 侦察发现（supervisor 实测，fix 必读——影响实现正确性与性能）

1. **summary_json 只覆盖 ~30%**：`curated_items` 73213 行里仅 21593 有预计算 `summary_json`。**归档不能依赖 `_load_precomputed`**（会丢 70% 卡片数据）。正确做法：对**每页 40 条现算 `item_summary`**（分页后每请求只 40 条，便宜），curated 分数/rank/reason 从该 item 最近一次精选取。
2. **去重查询慢，必须优化**：`distinct curated item` GROUP BY 73k 行，冷查 count **639ms**、第1页 165ms。**必须**：(a) 加索引（如 `curated_items(item_id, run_id)`）；(b) 计数走缓存（复用 timeline 那套 LRU + 数据版本 key）。优化点：`run_id` 形如 `20260605T023455Z-xxxx`，**按时间字典序**，所以 `MAX(run_id)` 即"最近一次精选"，免 join curation_runs。目标 TTFB：warm 与 /all 同量级（~25-50ms）。
3. **孤儿 0**：所有 curated item_id 都有对应 items 行，无需处理删除。
4. **distinct = 1792**（≈45 页 @40/页），会随新精选增长。

## 产品访问 + 部署/缓存约束

- 生产入口 `https://your-domain.example`，精选页 `/`。本地 serve `http://127.0.0.1:8000`（launchd，跑在本 checkout，StaticFiles 即时生效）。
- `/app.js` StaticFiles 即时生效；`/` 走 Jinja `index.html`（auto_reload）。**改完 app.js 必须 bump 所有引用 app.js 模板/静态 HTML 的 `?v=` token**（现 `20260604-pagination1` → 如 `20260604-curated-archive1`），否则 Cloudflare 边缘喂旧 JS。
- **后端改 Python（curated 路由 / app.py）需重启 serve**（launchd KeepAlive，`launchctl kickstart -k gui/$UID/com.example.ai-radar.serve`）；注意 pipeline 可能持 DB 锁（fetch/prefilter），重启失败先查锁。

## 当前实现现状（对照用）

- `src/airadar/web/routes/curated.py`：`curated()` 不带 run_id 时取**最新一轮**（`ORDER BY created_at DESC LIMIT 1`），只返回该 run 条目；响应无 page/limit/total。
- `src/airadar/web/app.py` `index_page()`：SSR preload 走 `_preload_context(curated payload)`——preload 需变成**page-aware**（带 page/total，首屏第 1 页）。
- `web/static/app.js` `initCurated()`：**无分页**——请求 `/api/v1/curated` 不带 page/limit，单页渲染。需接 `/all` 同款分页（复用已导出的 `paginationPages`/`renderPagination` 共享组件，URL 用 `/` + `?page=`）。
- 共享分页组件 + CSS 已存在（上一轮 /all+/wechat 做的）。

---

## Test Steps

### TS-001 — 精选页 `/` 累积归档 + 数字页码分页（对照 HP-8 + HP-1）
访问 `https://your-domain.example/`：
1. 底部出现数字页码分页控件（与 /all 一致：数字页码、首末页固定、当前页±2、… 省略、上/下一页箭头、可点任一页直跳），总页数 ≫1（当前 ~45 页）。
2. 第 1 页展示最新精选（按 published_at 倒序），卡片字段完整（HP-1：标题/摘要/信源/理由/精选标记/分数/标签/时间）。
3. 点中间某页（如第 20 页）→ `‹上一页 1 … 18 19 [20] 21 22 … 45 下一页›`，gaps=2，内容变化不重复。
4. 第 1 页无上一页箭头；末页无下一页箭头。
5. `/?page=9999` clamp 到末页，不显误导空态。
- L3：`/api/v1/curated?page=N` 响应含 page/limit/total；total 与去重后 distinct 一致。

### TS-002 — 分类筛选/搜索在归档+分页下成立（HP-3/HP-4）
1. 选某分类 → 列表与分页 total **随筛选变化**，页码重置，结果均属该分类。
2. 搜索关键词 → 结果相关、分页 total 随之变化；清空恢复。

### TS-003 — 移动端（~390px）精选分页可用（RS-2）
分页控件可见、可点、换行不溢出。

### TS-004 — 性能：归档分页 TTFB 不退化
`/api/v1/curated?page=1` 与 `?page=20` warm TTFB 与 /all 同量级（目标 ~<100ms，理想 ~25-50ms）；贴 curl 计时。基线（现单页 curated）warm ~25-55ms。

---

## 给 fix 的实现方向（supervisor 注入）
- 后端 `curated.py`：无 run_id（默认归档模式）时返回**跨 run 去重的累积归档**，分页（page/limit/total），按 published_at 倒序；每页现算 item_summary，curated 元数据取最近一次精选（`MAX(run_id)`）。保留 category/search/`deduped_item_clause`。**真实 total + 缓存**（复用 timeline LRU + 数据版本 key），加索引。越界 page clamp 到末页。
  - **⚠️ 回归风险（必读）**：`/daily` 依赖同一端点 `/api/v1/curated`——`initDaily` 用**无参** `api("/api/v1/curated")`（app.js:1179）探测"最新可用日期"（读 `data.items[0]` 与 `data.date`），用 `?date=X`（app.js:1115/1186）取**当天 digest**。改造时：(a) **`?date=X` 路径必须原样保留**（返回当天精选，不是归档）；(b) 无参默认变归档后，归档第 1 页是 published_at 倒序，`data.items[0]` 仍是最新条目、`data.date` 要给合理的最新日期，**保证 /daily 的最新日期探测与日期导航不坏**。fix 完成必须**实测 /daily 仍正常**（最新日期、前/后一日、当天内容）。其他调用方（cli.py 仅按 run 读 curated_items、不经此函数）无影响。
- 前端 `initCurated()`：接共享分页（`renderPagination` + `paginationPages`，URL `/?page=N`），消费 page/limit/total，popstate/preload 处理同 initTimeline。
- `app.py` index_page：SSR preload 带 page（默认 1）+ total。
- 测试：更新 curated 测试；加 `/` 分页的 pytest + playwright（数字页码、点非相邻页、首末箭头、筛选改 total）。
- **bump `?v=` token**（所有引用 app.js 的模板/静态 HTML）。
- 内部 verify：跑全量测试 + lint/类型 + TTFB 实测，全部贴输出。
- 不改 ux-contract / plan.md / state.md；journal.md 只 append。
