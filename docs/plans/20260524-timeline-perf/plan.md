# Timeline API 性能优化

> ⚠️ **Long-task mode** — 本 plan 处于长任务模式
> - 进度状态：`./state.md`
> - 决策日志：`./journal.md`
> - 协议详情：`~/.claude/references/long-task-protocol.md`
>
> 实施时（含 compact 之后）必须先读 state.md 和 journal.md 再决定下一步动作。
> 声称任务完成前必须实际跑本 plan 的 verify 步骤并贴出输出。

## 输入

- **诊断报告**：当前 session 的性能分析，详见本 plan「现状」段
- **本 plan 聚焦 L3**：L1（产物 = 更快的 /all 页面）和 L2（用户视角 verify = API 响应时间 < 1s）已在当前会话对齐

## 现状（可观察事实）

| 指标 | 当前值 | 来源 |
|------|--------|------|
| `/api/v1/timeline` TTFB | **14s** | `curl -w` 实测 |
| 响应体大小 | 128KB (50 items) | 同上 |
| 数据库 | SQLite 3.51, WAL, 244MB | `data/radar.db` |
| items 表行数 | 7,303 | `SELECT COUNT(*)` |
| item_evaluations 行数 | 10,320 | 同上 |
| 实际重复 URL items | 14 条（7 对） | 查询验证 |

### 瓶颈拆解

| # | 瓶颈 | 耗时 | 根因 | 影响范围 |
|---|------|------|------|---------|
| B1 | 去重子查询 `deduped_item_clause()` | ~3.5s | `lower(rtrim(url,'/'))` 函数无法走索引，对每个候选行做全表扫描 | timeline + curated |
| B2 | COUNT(*) 全表扫描 | ~4s | 与主查询相同的 WHERE（含 B1 去重）再执行一次，只为算 total | timeline only |
| B3 | N+1 enrichment 查询 | ~50 次查询/请求 | `item_summary()` → `latest_enrichment()` 对每个 item 独立查 `item_evaluations` + Pydantic 反序列化 | timeline + curated + items |
| B4 | 排序无索引 | 次要 | `ORDER BY published_at DESC, fetched_at DESC, id DESC` 需 temp B-TREE | timeline |

### 实测 A/B（本地数据库验证）

| 优化 | 前 | 后 |
|------|-----|-----|
| 加表达式索引解决 B1 | dedup 3.5s | **0.02s** (175x) |
| 加排序索引解决 B4 | temp sort | 索引排序 |
| 主查询 + COUNT 合计 | ~7s | **~0.26s** (27x) |
| B3 (N+1→JOIN) | 未独立实测 | 基于瓶颈拆解推算：14s - 7s(SQL) ≈ 7s 在 Python 层（N+1 查询 + JSON 解析）；JOIN 合并后预估 <0.5s |

**B3 fallback**：若 enrich LEFT JOIN 子查询性能不如预期（`MAX(id)` 在 item_evaluations 上较慢），fallback 方案为在 `item_evaluations` 上增加覆盖索引 `(item_id, stage, error, id DESC)` 加速原有 N+1 路径，而非 JOIN 方案。

## 使用方式

implementer 完成后，`/all` 页面加载时间从 14s 降到 <1s。用户直接感知到的改变是页面"秒开"。无功能变化，纯性能优化。

## 取舍

- **正确性 > 性能**：优化不能改变任何现有功能行为。所有现有测试必须继续通过。
- **最小改动面**：只改必要的文件，不做功能重构。
- **N+1 优化范围**：仅对 `timeline` 路由做 enrichment JOIN 优化（50 items/请求，收益最大）。`curated` 路由（通常 <30 items）和 `items` 路由（单条）暂不改，但 `item_summary` 接口保持向后兼容，后续可复用。

## 实施步骤

### Phase 1: 数据库索引（解决 B1 + B4）

**文件**：新建 `src/airadar/migrations/005_perf_indexes.sql`

创建两个索引：

```sql
-- 表达式索引：让 deduped_item_clause 的 lower(rtrim(url,'/')) 走索引
CREATE INDEX IF NOT EXISTS idx_items_source_url_norm
ON items(source_id, lower(rtrim(url, '/')));

-- 排序索引：消除 ORDER BY 的 temp B-TREE
CREATE INDEX IF NOT EXISTS idx_items_published_fetched_id
ON items(published_at DESC, fetched_at DESC, id DESC);
```

**Migration 执行方式**：项目在 `db.migrate()` 中自动扫描 `src/airadar/migrations/*.sql` 并按文件名排序执行（`CREATE INDEX IF NOT EXISTS` 保证幂等）。Web server 启动时调用 `db.migrate(db_path)`（见 `app.py`），所以只需重启服务即可应用。本地开发中也可手动执行 `sqlite3 data/radar.db < src/airadar/migrations/005_perf_indexes.sql` 验证。

**内部 verify**：
- `sqlite3 data/radar.db ".indices items"` 确认两个新索引存在
- `EXPLAIN QUERY PLAN` 确认 dedup 子查询使用 `idx_items_source_url_norm`
- `EXPLAIN QUERY PLAN` 确认主查询排序使用索引而非 `USE TEMP B-TREE FOR ORDER BY`
- 所有现有测试通过：`python -m pytest tests/ -x`

### Phase 2: 消除 N+1 enrichment 查询（解决 B3）

**文件**：修改 `src/airadar/web/routes/timeline.py`

**目标**：把 `item_summary()` 内对每个 item 独立调用 `latest_enrichment()` 的模式，改为在主查询中 LEFT JOIN 一次性取出 enrich 数据。

**设计**：

1. 在 `timeline()` 主查询中追加 LEFT JOIN：
   ```sql
   LEFT JOIN item_evaluations enrich_eval ON enrich_eval.id = (
     SELECT MAX(latest_enrich.id)
     FROM item_evaluations latest_enrich
     WHERE latest_enrich.item_id = i.id
       AND latest_enrich.stage = 'enrich'
       AND latest_enrich.error IS NULL
   )
   ```
   SELECT 列追加 `enrich_eval.output_json AS enrich_output_json`

2. 在遍历 rows 构建 items 时，从 `row["enrich_output_json"]` 直接解析 `EnrichOutput`，传给一个新的 `item_summary` 重载或直接 inline 处理——避免再调 `latest_enrichment()`。

**具体改法**：

- 修改 `item_summary()` 签名，增加可选参数 `enrichment: EnrichOutput | None = None`
- 当 `enrichment` 参数已传入时，跳过 `latest_enrichment()` 调用
- 当 `enrichment` 参数为 None 时（`curated` 和 `items` 路由的兼容路径），行为不变
- 在 `timeline()` 中预解析 enrich_output_json，传入 `item_summary(row, ..., enrichment=parsed)`

**文件改动清单**：
| 文件 | 改动 |
|------|------|
| `src/airadar/web/routes/timeline.py` | 主查询追加 enrich LEFT JOIN；遍历时预解析并传入 enrichment |
| `src/airadar/web/routes/common.py` | `item_summary()` 增加 `enrichment` 可选参数；有值时跳过 `latest_enrichment()` |

**不改的文件**（向后兼容）：
- `curated.py` — 调用 `item_summary(row, preview_query, conn)` 不传 enrichment，走原路径
- `items.py` — 同上

**内部 verify**：
- `item_summary()` 的函数签名测试：传 enrichment=None 和传 enrichment=EnrichOutput(...) 两种情况
- 所有现有测试通过：`python -m pytest tests/ -x`
- 功能回归：`/api/v1/timeline` 返回的 item 结构与优化前一致（字段名、类型、值域）

### Phase 3: 消除多余 COUNT 查询（解决 B2）

**文件**：修改 `src/airadar/web/routes/timeline.py`

**目标**：去掉单独的 `SELECT COUNT(*)` 查询，用已有信息推算 total。

**设计**：

当前前端使用 `total` 来计算 `totalPages = Math.ceil(total / limit)` 以渲染分页。方案：

- **保留 total 字段**（前端兼容），但改为**惰性估算**：
  - 若当前页返回 < limit 条 → `total = (page - 1) * limit + len(items)`（精确）
  - 若当前页返回 = limit 条 → `total = page * limit + 1`（表示"至少还有下一页"）
- 这样分页 UI 仍能正确显示"上一页/下一页"，只是不再显示精确的总页码（尾部页码会动态调整）

**前端影响分析**：

`renderPagination()` (app.js:486) 用 `totalPages = Math.ceil(total / limit)` 算总页数。当 total 改为估算值时：
- 当前在非末页：`totalPages` 至少是 `page + 1`，分页显示"当前页 … 下一页"，用户可正常翻页
- 当前在末页：`totalPages` 精确等于 `page`，无"下一页"按钮
- 唯一视觉差异：不再显示精确的末尾页码数字（如原来显示 "1 2 ... 39"，现在中间页显示 "1 2 ... N+1"）

这对 /all 页面完全可接受——用户几乎不会直接跳到"第 39 页"。

**文件改动清单**：
| 文件 | 改动 |
|------|------|
| `src/airadar/web/routes/timeline.py` | 删除 COUNT 查询；用 LIMIT+1 结果推算 total |

**内部 verify**：
- 首页（page=1，有下一页）：total > limit
- 末页（返回 < limit 条）：total = (page-1)*limit + len(items)
- 单页数据（总条数 < limit）：total = len(items)，无分页 UI
- 所有现有测试通过：`python -m pytest tests/ -x`

### Phase 4: 端到端验证

**无新代码改动**。纯验证步骤。

## User-Facing Verify（L2，交付 gate）

| # | 维度 | 方法 | 预期 | 人机 |
|---|------|------|------|------|
| V1 | API 响应时间 | `curl -o /dev/null -s -w "TTFB: %{time_starttransfer}s\nTotal: %{time_total}s" "https://your-domain.example/api/v1/timeline?limit=50&page=1"` | TTFB < 2s, Total < 3s（含网络；本地目标 <1s）| agent |
| V2 | 功能回归 — 首页 | 对比优化前后 `/api/v1/timeline?limit=5&page=1` 的 JSON 结构：字段名、字段类型、值域一致 | 结构一致，数据内容可能因时间推移不同 | agent |
| V3 | 功能回归 — 分类筛选 | `curl "https://your-domain.example/api/v1/timeline?category=ai-models&limit=5"` 返回的 items 全部含 `模型发布` tag | 全部命中 | agent |
| V4 | 功能回归 — 分页 | page=1 和 page=2 的 items 不重叠；末页无 next 按钮 | 无重叠 | agent |
| V5 | 功能回归 — 搜索 | `curl "https://your-domain.example/api/v1/timeline?q=OpenAI&limit=5"` 返回包含 OpenAI 的 items | 命中 | agent |
| V6 | 浏览器 E2E | 用 agent-browser 打开 `https://your-domain.example/all`，验证页面加载完成、列表渲染、分页可点击 | 页面 <3s 可交互 | agent |
| V7 | 现有测试 | `python -m pytest tests/ -x` | 全部通过 | agent |
| V8 | 保持性 — curated 路由 | `curl /api/v1/curated?limit=3` 返回 JSON 结构含 items 数组，每个 item 含 `title_zh`, `summary_zh`, `topic_tags`, `weighted_score` 字段 | 结构与优化前一致 | agent |
| V9 | 保持性 — items 路由 | `curl /api/v1/items/{已知item_id}` 返回 JSON 结构含 item 对象 + evaluations 数组 | 结构与优化前一致 | agent |

**注意**：V1 在开发环境（本地 SQLite）和生产环境（服务器）的绝对时间会不同。本地验证目标 <1s；生产验证需要部署后测量，目标 TTFB <2s。若本地已 <1s 但生产部署不在本 plan 范围，V1 生产测量标记为"部署后验证"。

## Internal Verify（L3，过程兜底）

| # | 检查 | 方法 | 预期 |
|---|------|------|------|
| I1 | 索引创建 | `sqlite3 data/radar.db ".indices items"` | 含 `idx_items_source_url_norm` 和 `idx_items_published_fetched_id` |
| I2 | dedup 查询计划 | `EXPLAIN QUERY PLAN` + dedup 子查询 | 使用 `idx_items_source_url_norm` |
| I3 | 排序查询计划 | `EXPLAIN QUERY PLAN` + 主查询 | 不含 `TEMP B-TREE FOR ORDER BY` |
| I4 | item_summary 兼容 | enrichment 参数 None vs 传值，输出结构一致 | 字段名和类型一致 |
| I5 | total 估算 | 边界 case 测试 | 首页/末页/单页/空结果 |
| I6 | 测试套件 | `python -m pytest tests/ -x` | 全部通过 |

## Defaulted Decisions（planner 自行拍板）

| 决策 | 默认选择 | 理由 |
|------|---------|------|
| N+1 优化仅做 timeline | 只改 timeline.py | curated ≤30 items、items 单条，收益不足以 justify 改动；item_summary 接口兼容，后续可做 |
| COUNT 改为估算而非 window function | 估算 | SQLite window function 的 COUNT(*) OVER() 在复杂 WHERE 下不一定更快；估算无额外查询成本，前端影响可忽略。**Fallback trigger**：若用户反馈分页体验不可接受（如强烈需要精确总页码），恢复 COUNT 查询或改用 `COUNT(*) OVER()` window function |
| 不加 HTTP 缓存层 | 本次不加 | 纯 SQL 优化已足够（14s → <1s）；加缓存是独立任务，复杂度高（cache invalidation）|
| 不改变 curated.py | 保持原样 | curated 路由的 item 数量少（通常 <30），N+1 开销可接受 |

## 引用索引

| 路径 | 用途 |
|------|------|
| `src/airadar/web/routes/timeline.py` | 主改动目标：timeline API handler |
| `src/airadar/web/routes/common.py` | item_summary / latest_enrichment 定义 |
| `src/airadar/web/routes/curated.py` | 需保持兼容的路由（不改） |
| `src/airadar/web/routes/items.py` | 需保持兼容的路由（不改） |
| `src/airadar/migrations/005_perf_indexes.sql` | 新建：性能索引 |
| `src/airadar/enrich/schema.py` | EnrichOutput 定义 |
| `web/static/app.js:486` | renderPagination 前端分页逻辑 |
| `tests/test_web_routes.py` | 现有路由测试 |
