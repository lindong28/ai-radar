> **Archive status**: 已归档。本 plan 是一次性的 UX 契约端到端测试脚本，不是产品契约本身；执行过程产物 `state.md` / `journal.md` 按长任务协议不入档。
> 正文测的「数字页码 + 省略号」形态已被后续改版取代：列表页当前是无限滚动（搜索态仍走 page 分页），当前权威见 [contracts/ux-contract.md](../../contracts/ux-contract.md) HP-8、TL-4、WX-4，真实计数与分页的实现口径见 [architecture.md](../../architecture.md)「Web Layer」的「真实计数与分页」节。以下为原 plan 正文，未修改。

> ⚠️ **Long-task mode** — 本 plan 处于长任务模式
> - 进度状态：`./state.md`
> - 决策日志：`./journal.md`
> - 协议详情：`~/.claude/references/long-task-protocol.md`
>
> 实施时（含 compact 之后）必须先读 state.md 和 journal.md 再决定下一步动作。
> 声称任务完成前必须实际跑本 plan 的 verify 步骤并贴出输出。

# execute-ux-contract test plan — 分页升级（数字页码 + 省略号）

## 来源契约

- ux-contract：`docs/contracts/ux-contract.md`
- 本轮聚焦的 L2：**TL-4（时间线分页）**、**WX-4（微信解读列表分页与上下文保留）**
- 本轮触发因：用户诉求——分页除上一页/下一页外，要显示数字页码，首末页一眼可见，中间页码太多时用 … 省略，可直接点多个页码跳转。contract 已据此更新（TL-4 / WX-4 / 页面与功能段 / Defaulted Decisions「分页控件形态」），形态 = **经典窗口 ±2**。

## 产品访问信息

| 项 | 值 |
|---|---|
| 生产入口 | https://your-domain.example |
| 分页页面 | `/all`（时间线）、`/wechat`（微信解读列表） |
| 本地 serve | `http://127.0.0.1:8000`（launchd KeepAlive，跑在本 checkout `~/research/ai-radar`） |
| 认证 | 公开只读，无需登录 |
| 工具 | Web → agent-browser（真实浏览器访问生产 URL） |

## 部署/缓存约束（fix 必读）

- `/app.js` 由 `StaticFiles(web/static)` 每请求从磁盘读 → 改 `web/static/app.js` 即时生效，无需重启 serve。
- `/all` `/wechat` 走 Jinja 模板（`web/templates/`，Jinja `auto_reload=True`）→ 改模板即时生效。
- **唯一缓存关口**：模板里硬编码的 `?v=20260602-wechat-uxfix1` cache-bust token（浏览器 + Cloudflare 边缘）。**fix 改完 app.js 必须在所有引用 app.js 的模板/静态 HTML 里 bump 这个 token**（如 `?v=20260604-pagination1`），否则边缘缓存喂旧 JS、retest 看不到新行为。

## 本轮数据量（决定测试面，重要）

实测（`/api/v1/timeline` `/api/v1/wechat`）：
- `/all`：total≈41，limit=40 → **2 页**（`1 2`，永远不触发省略号）。
- `/wechat`：total=182，limit=50 → **4 页**（`1 2 3 4`，±2 窗口覆盖全 4 页，也不触发省略号）。

**推论**：省略号 + ±2 窗口在当前线上数据量下**不会显示**——这是正常的，不是 bug。
- 省略号/窗口逻辑必须靠**确定性单元测试**（合成大 totalPages）验证，不能靠 live 数据。
- live e2e 只验当前数据量下的真实可见改进：`/wechat` 从「只显示当前页数字」变为「4 页全部可点」；`/all` 显示 `1 2`；首/末页箭头边界；点页码跳转加载正确内容。

## 当前实现现状（test 阶段对照用，非给 fix 的指令）

- `web/static/app.js`：
  - `renderPagination`（/all）：已是数字页码 + 省略号，但窗口 `paginationPages` 仅 current±1（`{1, totalPages, current-1, current, current+1}`）。
  - `renderWechatPagination`（/wechat）：**只有** `‹上一页 [当前页] 下一页›`，无页码列表、无首末页、无省略号 ← 主缺口。
- 共享 CSS：`.pagination` / `.pagination-link` / `.pagination-gap` / `.pagination-link-active`（style.css）。
- 既有测试：`tests/playwright/test_phase2.py`（/all next、/wechat 上下文保留）、`test_phase5_boundaries.py`（/all?page=9999 clamp→末页）、`tests/test_frontend_static_contract.py`（白盒断言 app.js）。

---

## Test Steps

### TS-001 — `/all` 数字页码分页形态与交互（对照 TL-4）

访问 `https://your-domain.example/all`，观察底部 `#pagination`（`.pagination`）。判据：
1. 显示数字页码链接（`.pagination-link` 含数字），而非仅"上一页/下一页"。
2. 第 1 页和最后一页页码都可见。
3. 当前页高亮（`.pagination-link-active` / `aria-current="page"`），其左右各最多 2 个相邻页码（当前数据 2 页时即 `1 2`）。
4. 当首/末页与当前页窗口存在跳号（间隔 >1）时出现 `…`（`.pagination-gap`）——**当前 2 页不触发，记为 N/A，不算 fail**。
5. 点击某个非当前页码 → URL `?page=` 变更、列表加载该页内容、与原页不重复。
6. 处于第 1 页时无"上一页"箭头（`rel="prev"`）；处于最后一页时无"下一页"箭头（`rel="next"`）。
- L3：DOM 中 `.pagination` 子节点序列；network `/api/v1/timeline?...page=N` 命中。

### TS-002 — `/wechat` 数字页码分页 + 上下文保留 + 越界（对照 WX-4）

访问 `https://your-domain.example/wechat`，观察底部分页。判据：
1. **显示数字页码列表**（当前 4 页 → `1 2 3 4` 均为 `.pagination-link`），而非仅显示当前页数字 + 上/下一页（这是本轮主修复点）。
2. 第 1 页与最后一页可见；当前页高亮；当前页±2 相邻页可见；跳号处 `…`（4 页不触发，N/A）。
3. 点击任一可见页码（如从第 1 页点第 3 页）→ 直接跳该页、内容变化、不与上页重复、可返回上一页。
4. 第 1 页无"上一页"箭头；第 4 页无"下一页"箭头。
5. 越界 `https://your-domain.example/wechat?page=9999` → 回到最后一页（第 4 页），**不**显示"暂无微信文章解读"空态。
6. 从第 N 页点开某卡片详情，详情页"‹ 返回列表"回到第 N 页（`/wechat?page=N`）。
- L3：DOM `.pagination` 结构；`/api/v1/wechat?page=N` 响应 `page` 字段。

### TS-003 — 移动端分页可用性（对照 RS-2：移动端分页可用）

在 ~390px 视口下访问 `/all` 与 `/wechat`：分页控件可见、页码可点、换行不溢出视口、点击页码可跳转。

### TS-004 — 省略号 + ±2 窗口的确定性验证（L3，合成数据；live 触发不了）

因线上数据量只有 2/4 页触发不了省略号，本步验证分页**纯逻辑**（升级后应覆盖 /all 与 /wechat 共用）：对合成 `(current, totalPages)` 断言可见页码序列与省略号位置：
- `(8, 42)` → 页码 `[1, 6, 7, 8, 9, 10, 42]`，且 `1↔6` 与 `10↔42` 之间各有一个 `…`。
- `(1, 42)` → `[1, 2, 3, 42]`，无"上一页"箭头。
- `(42, 42)` → `[1, 38, 39, 40, 41, 42]`，无"下一页"箭头。
- `(3, 5)` → `[1, 2, 3, 4, 5]`，无 `…`（无跳号）。
- `totalPages <= 1` → 不渲染控件。

验证方式由 fix 提供的确定性测试承载（见下）。pass 证据 = 该测试实跑通过的输出。

---

## 给 fix 的实现方向（test→fix 之间由 supervisor 注入，非 test 阶段读）

> 写在 plan 里供留痕；fix prompt 会引用对应 issue。
- 统一两页分页：把 `/wechat` 的 `renderWechatPagination` 升级为与 `/all` 同形态（数字页码 + 首末页 + … + ±2 窗口 + 上/下一页箭头），复用同一套页码计算。
- 窗口由 ±1 放宽到 **±2**：`paginationPages` 改为 `{1, totalPages, current-2..current+2}` 过滤越界后排序。
- 导出纯函数（如 `paginationPages`）以支持 TS-004 确定性单测。
- 新增确定性测试覆盖 TS-004 的合成用例（node `--test` 验导出的纯函数，或等效）。
- 扩展 Playwright：`/wechat` 出现数字页码且可点非相邻页、首/末页箭头边界。
- **bump `?v=` token**（所有引用 app.js 的模板/静态 HTML）。
- 不动 ux-contract / plan.md / state.md / journal.md。
