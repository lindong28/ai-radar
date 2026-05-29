# Plan: SSR Preload 让 aiplanet.live 首屏即时显示

> **Long-task mode**: 实施过程遵循 `~/.claude/references/long-task-protocol.md`。
> - 启动前读 `state.md`、`journal.md`。
> - 每完成一个步骤更新 `state.md`，在 `journal.md` 追加 entry。
> - 交付前完成本 plan §6 的 verify 步骤。

## 1. Context（为什么做）

`https://aiplanet.live/` 当前是纯 CSR：HTML 是空壳，JS 解析后才发 `/api/v1/curated` 请求，浏览器经历"白屏 → 正在加载 → 内容"的两段等待，首屏可见耗时约 3 秒，spinner 中位数 1295ms。

对比 `https://aihot.virxact.com/all`：HTML 内联了完整数据（61KB 的 inline script），浏览器收到 HTML 立刻渲染，**首屏没有 spinner**。aihot 用 Next.js + RSC 实现，我们用更轻量的方案达到等价的首屏体验。

前序工作已完成：`/api/v1/curated` 已通过 commit `1498195` 预计算到 ~17ms。本 plan 是延续——把这个 17ms 通过 SSR 让用户**真正感受到**。

**1.5s FCP acceptance 锚定**：SSR 服务端处理 ~17ms（curated）/ ~13ms（timeline，§9 实测）+ Cloudflare tunnel 800-1200ms（独立网络问题，本 plan 不解决）+ HTML 解析/DOM 构建 100-300ms ≈ 1.5s。aihot 实测 FCP ~3s 但**无 spinner**，我们目标是 FCP 低于 aihot 且无 spinner——延迟绝对值受限于 tunnel 现状，但用户感知差异主要来自是否有 spinner。

## 2. 三层产物（L1 → L2 → L3）

### L1 — 交付的东西

- **使用者**：访问 aiplanet.live 的最终用户（浏览器）。
- **使用形态**：HTTP 响应——2 个页面路由（`/`、`/all`）返回带数据的 HTML（SSR）；`/daily`、`/about` 维持现状（前者 API 已 < 2ms，后者无数据需求）。
- **使用方式**：用户打开/刷新页面，期待立即看到内容（不再有"正在加载"二段等待）。带 query param 的 deep link（如 `/?category=ai-models`、`/?q=keyword`、`/all?channel=news`）也应直接渲染对应数据。
- **范围**：
  - `/`（精选）和 `/all`（时间线）做 SSR + preload。
  - `/daily`、`/about` 不动（前者 API 已 < 2ms 无可感延迟，后者纯静态）。
  - 后续交互（搜索、分类切换、分页）仍走客户端 fetch API——不刷屏。
  - 首页 query param（`?category=xxx`、`?q=xxx`）服务端识别并预填充对应数据。
- **不做**：不引入 Next.js 等前端框架；不动 daily/about；timeline 预计算（实测 §9 timeline 当前 ~13ms 足够，预防性优化无必要）、缓存 header（Cache-Control + ETag）、base.html 抽取——以上全部作为 follow-up 单独 plan。

### L2 — 用户视角 verify（implementer-executable）

| # | 维度 | 实施步骤 | 通过条件 |
|---|------|---------|---------|
| L2-1 | 首屏 spinner 消失 | Playwright 脚本打开 `https://aiplanet.live/`，在 navigation start 后 200ms 内检测 `text=正在加载` selector，5 次测量。（注：脚本无法测"持续时长"，只测"是否出现"；本地必须 0 出现，生产允许 ≤ 1 次出现以容忍网络抖动。） | 本地 0/5 出现；生产 ≤ 1/5 出现。 |
| L2-2 | 首屏内容即时可见（DOM-available 时间） | Playwright 测量 navigation start → 首个 `article.item-row` 出现的时间，5 次取中位数。**注**：SSR 模式下该 selector 在 HTML 到达就匹配（接近 TTFB），CSR 模式要等 JS 渲染——两个模式数字差距正是改造收益。 | `/` 中位数 ≤ 1.5s；`/all` 同。 |
| L2-3 | SSR 页面生效 | curl `http://127.0.0.1:8000/` 和 `/all` 各一次，grep HTML 内是否含 `id="__PRELOAD__"` 且 JSON 含 ≥1 item。 | 2 个页面都有 preload 且解析后 `items.length ≥ 1`。 |
| L2-4 | Deep link 即时显示 | Playwright 打开 3 个 deep link：`/?category=ai-models`、`/?q=openai`、`/all?channel=news`。每个测 spinner 出现次数 + 首屏内容时间，2 次取中位数。 | 各 URL spinner ≤ 1 次出现 ≤ 200ms；内容显示 ≤ 1.5s。 |
| L2-5 | 后续交互不刷屏 + 走 API（preservation） | Playwright 在 `/` 已加载状态下：a) 点击"模型"分类按钮、b) 在搜索框输入文字触发查询；在 `/all` 已加载状态下：c) 点击下一页。监听 `request.resource_type == "document"` + `page.on('request')` 捕获 URL。注：`framenavigated` 会被 `history.pushState()` 触发，不能代表整页 reload。 | 三项操作均无 document navigation；且每个操作触发至少一个 fetch 请求匹配 `/api/v1/(curated\|timeline)`。 |
| L2-6 | preload items 数量符合契约 | curl `/` 和 `/all` 解析 preload JSON 看 items 数量。 | `/` 返回的 items 数量在 [30, 60] 之间（curated 默认 40 项，允许波动）；`/all` 在 [40, 60] 之间（timeline 默认 50 项）。 |
| L2-7 | 视觉无回归 | Playwright 截图 `https://aiplanet.live/`、`/all`、`/daily`、`/about` 各一张，与 `plans/20260528-ssr-preload/baseline-screenshots/` 下的改造前截图对比。**对比姿势**：两个浏览器窗口并排，宽度 ≥ 1280px，同一 viewport。按下表 4×4 评分。 | 16 格全部 PASS。任一格 FAIL 或 ≥ 2 格 borderline → 整体 FAIL。 |
| L2-8 | preservation（不动的部分确实没动） | curl `/daily` 和 `/about` 各一次，断言 200 + HTML 含原有 marker；断言 `web/static/{index,all}.html` 文件仍存在；显式跑 `pytest tests/test_frontend_static_contract.py -v`；`git diff --stat HEAD -- src/airadar/web/routes/timeline.py src/airadar/web/routes/daily.py` 应为空；`curl -sI http://127.0.0.1:8000/` 应无 `Cache-Control` header。 | 4 个路由/文件均正常；contract 测试通过；timeline.py/daily.py 无 diff；响应头无 cache-control。 |

**L2-7 评分 rubric**（4 页面 × 4 维度 = 16 格）：

| 页面 / 维度 | 布局 | 字体 | 配色 | 卡片样式 |
|------------|------|------|------|---------|
| `/` | ☐ PASS / ☐ FAIL / ☐ borderline + 注释 | ... | ... | ... |
| `/all` | ... | ... | ... | ... |
| `/daily` | ... | ... | ... | ... |
| `/about` | ... | ... | ... | ... |

L2-7 是人工 gate；L2-1 ~ L2-6, L2-8 均 agent 可独立完成。L2-7 之前已用 L2-3/L2-5/L2-8 兜底"功能/路由没坏"，人工主要负责"视觉没坏"。

### L3 — 设计决策 + 内部 verify

#### L3-1：模板引擎选 Jinja2

FastAPI 官方推荐，starlette 自带依赖。可继承现有 HTML 结构，未来抽公共 base.html（follow-up）方便。

**内部 verify**：`.venv/bin/python -c "import jinja2; print(jinja2.__version__)"` 确认可用。

#### L3-2：路由改造

`src/airadar/web/app.py` 当前用 `FileResponse(STATIC_DIR / "index.html")`。改为：

```python
@app.get("/")
def index_page(request: Request, category: str | None = None, q: str | None = None):
    data = curated(request, category=category, q=q)  # 复用 endpoint 函数
    return templates.TemplateResponse("index.html", {"request": request, "preload": data})
```

**已验证**（§9）：`curated()` 和 `timeline()` 函数签名均接受所需 query params。

**内部 verify**：扩展 `tests/test_web_routes.py` 中 `test_static_clean_routes_and_curated_redirect`，增加断言 `/` 和 `/all` HTML 含 `id="__PRELOAD__"`。

#### L3-3：模板组织

`web/templates/`（新目录）：
- `index.html` — 精选页（从 `web/static/index.html` 复制 + 加 preload slot）
- `all.html` — 时间线页（从 `web/static/all.html` 复制 + 加 preload slot）
- `_prepaint_list.html` — 首屏前 12 条 `article.item-row` 的服务端 prepaint partial

不动 `daily.html`、`about.html` 静态文件。

**迁移策略**：1:1 复制（先保证字节等价），再注入 preload script；生产验证后追加 server prepaint，因为 JSON-only preload 仍需等待 JS/字体/CSS/隧道传输后才有 `article.item-row`。

**内部 verify**：Step 1 阶段模板 diff 仅 preload script 行；最终生产版本额外包含 `_prepaint_list.html`、modulepreload 与非阻塞字体 preload，这些差异由 route contract 和 Playwright smoke 覆盖。

#### L3-4：preload 数据格式 + server prepaint + app.js 调整

HTML 中插入：

```html
<section id="list" class="timeline" aria-live="polite">
  {% include "_prepaint_list.html" %}
</section>
<script id="__PRELOAD__" type="application/json">
{{ preload | tojson | safe }}
</script>
```

`web/static/app.js` 顶层加：

```javascript
function readPreload() {
  const el = document.querySelector("#__PRELOAD__");
  if (!el) return null;
  try { return JSON.parse(el.textContent); } catch { return null; }
}
```

`initCurated()`、`initTimeline()` 各自开头改为优先读 preload，无 preload 才 fetch（这是渐进升级，旧静态 HTML fallback 仍工作）。`initDaily()`、`initAbout()` 不动。`_prepaint_list.html` 只负责首屏 DOM 先出现；`app.js` 用同一份 preload 做权威渲染和交互绑定。`/all` SSR 首屏 payload 使用 40 items，和客户端分页大小一致，仍满足 L2-6 `[40, 60]` 契约。

**内部 verify**：
- 单元测试构造 mock DOM 含 preload，断言 `initCurated` 不发起 fetch（用 string-match 或简单 jsdom）。
- 无 preload 时 fallback 到 fetch（现有 web 集成测试已覆盖）。

#### L3-5：服务端复用 endpoint 函数

直接调用 endpoint 函数（如 `curated(request, ...)`），不通过 HTTP 自调。避免一次 localhost 往返（~5ms），且返回的 dict 直接 jsonify 进模板。

**内部 verify**：模板渲染测试断言传入 `preload` 与 `client.get("/api/v1/curated").json()["data"]` 等价。

#### L3-6：兼容性 / 渐进迁移

- `web/static/{index,all}.html` 保留不删（失败回滚路径）。
- `app.mount("/", StaticFiles(...))` 仍挂在末尾，FastAPI 路由匹配顺序优先动态路由 → 不冲突。

**内部 verify**：现有 `tests/test_frontend_static_contract.py`（测 app.js selector 契约）必须仍通过（见 L2-8）。

## 3. 横切取舍

- **首屏速度 ≫ 实现简洁性**：愿意引入 Jinja2 模板系统、改路由签名，换首屏 0 spinner。
- **CSR 兼容 ≫ 完全 SSR**：保留客户端 fetch 用于后续交互；不追求"无 JS 也能用"。
- **复用 ≫ 重写**：复用现有 endpoint 函数、复用 app.js 渲染逻辑，只加 preload 短路。
- **最小可交付 ≫ 一次到位**：timeline 预计算（§9 实测当前 ~13ms 够用）、缓存 header、base.html 抽取均拆到 follow-up plan。本 plan 聚焦"消除可感知 spinner"这一 stated goal。

## 4. 实施步骤

| # | 步骤 | 内部 verify |
|---|------|------------|
| 0 | 截图 baseline：Playwright 打开生产 `https://aiplanet.live/`、`/all`、`/daily`、`/about` 各截一张到 `plans/20260528-ssr-preload/baseline-screenshots/` | 4 个 PNG 存在 |
| 1 | 新建 `web/templates/`，把 `web/static/{index,all}.html` 复制为 `web/templates/{index,all}.html`，在 `</body>` 前注入 `<script id="__PRELOAD__">{{ preload \| tojson \| safe }}</script>` | `diff <(strip preload web/templates/index.html) web/static/index.html` 仅 preload 行差异。**trigger response**：若 diff 出现非 preload 行差异 → 回到 1:1 复制重做（不要现场修模板，避免不可逆漂移） |
| 2 | 改 `src/airadar/web/app.py`：引入 `Jinja2Templates`，`/` 和 `/all` 路由从 FileResponse 改 TemplateResponse 调用对应 endpoint 函数获取数据。其余路由（`/daily`、`/about`）不动 | `pytest tests/test_web_routes.py -k 'static_clean_routes' -v` 通过 |
| 3 | 改 `web/static/app.js`：加 `readPreload()`；`initCurated`、`initTimeline` 各自开头判断 preload；`initDaily`、`initAbout` 不动 | 现有 `tests/test_frontend_static_contract.py` 通过；新加 mock DOM 测试断言 preload 命中不 fetch |
| 4 | 扩展 `tests/test_web_routes.py`：加 SSR preload 测试（不新建文件），覆盖 `/` 和 `/all` 的 preload JSON 存在 + items 数量 + ?q deep link + ?category deep link | `pytest tests/test_web_routes.py` 全通过 |
| 5 | 更新 docs：在 `docs/` 中前端架构说明（如有 `docs/architecture/` 或类似目录则就近，否则在根 README 的 Layout/Services 段加一行）记录 (a) `/` 和 `/all` 现走 Jinja2 SSR + preload，(b) 新增 SSR 页面如何写 preload slot，(c) `web/static/{index,all}.html` 暂保留但已 deprecated | docs 文件存在且包含上述 3 点 |
| 6 | 本地 Playwright 烟测：在 `127.0.0.1:8000` 跑 plan §6 的 L2-1 + L2-2 + L2-8 等价脚本 | 本地 spinner=0；FCP < 500ms（本地无网络往返）；preservation 全过 |
| 7 | 部署 + 生产验证：`launchctl kickstart -k gui/$UID/live.aiplanet.ai-radar.serve`，跑 §6 的 L2-1/L2-2/L2-4 Playwright 生产实测 | spinner ≤ 1/5 且 < 200ms；FCP 中位数 ≤ 1.5s |

## 5. 关键文件

| 文件 | 改动类型 | 用途 |
|------|---------|------|
| `web/templates/index.html` | 新建 | 精选页 Jinja 模板 |
| `web/templates/all.html` | 新建 | 时间线页 Jinja 模板 |
| `web/templates/_prepaint_list.html` | 新建 | SSR 首屏前 12 条 item-row prepaint |
| `src/airadar/web/app.py` | 修改 | 路由改 TemplateResponse（仅 / 和 /all） |
| `web/static/app.js` | 修改 | 加 `readPreload()` 短路逻辑（initCurated + initTimeline） |
| `web/static/{daily,about}.html` | 不动 | 维持 FileResponse |
| `src/airadar/web/routes/curated.py` | 不动 | 已优化（commit 1498195） |
| `src/airadar/web/routes/timeline.py` | 不动 | §9 实测 ~13ms 够用 |
| `src/airadar/web/routes/daily.py` | 不动 | 已 < 2ms |
| `tests/test_web_routes.py` | 扩展 | SSR preload + deep link + preservation 断言 |
| `docs/` 相关文件 | 修改/新建 | 前端架构说明 |
| `plans/20260528-ssr-preload/baseline-screenshots/` | 新建 | L2-7 视觉对比基线 |

## 6. Verify（交付前必跑）

### Agent 可独立完成

```bash
# 6.1 单元 + 集成测试全通过
./test.sh
.venv/bin/python -m pytest tests/test_frontend_static_contract.py -v  # L2-8 显式跑

# 6.2 SSR 页面生效（仅 / 和 /all）
for p in / /all; do
  echo "=== $p ==="
  curl -s "http://127.0.0.1:8000$p" | grep -c '__PRELOAD__'
done
# 期望：2 行 "1"

# 6.3 Deep link SSR + items 数量
for url in '/?category=ai-models' '/?q=openai' '/all?channel=news'; do
  echo "=== $url ==="
  curl -s "http://127.0.0.1:8000$url" | python3 -c "
import sys, re, json
html = sys.stdin.read()
m = re.search(r'id=\"__PRELOAD__\"[^>]*>(.*?)</script>', html, re.S)
assert m, 'no __PRELOAD__ found'
data = json.loads(m.group(1))
print(f'items: {len(data[\"items\"])}')
assert len(data['items']) >= 1, 'expected ≥1 item'
"
done

# 6.4 L2-6 items 数量契约（/ 和 /all 都断言）
for p in / /all; do
  curl -s "http://127.0.0.1:8000$p" | python3 -c "
import sys, re, json
path = '$p'
html = sys.stdin.read()
data = json.loads(re.search(r'id=\"__PRELOAD__\"[^>]*>(.*?)</script>', html, re.S).group(1))
n = len(data['items'])
lo, hi = (30, 60) if path == '/' else (40, 60)
assert lo <= n <= hi, f'{path} items {n} out of [{lo}, {hi}]'
print(f'{path} items: {n} ✓')
"
done

# 6.5 L2-8 preservation：daily/about 路由 + 静态文件保留 + 不动的 .py 没改 + 无 cache header
for p in /daily /about; do
  code=$(curl -s -o /tmp/preserve.html -w '%{http_code}' "http://127.0.0.1:8000$p")
  echo "$p → HTTP $code"
  [[ "$code" == "200" ]] || { echo "FAIL: $p not 200"; exit 1; }
  grep -q 'AI Radar' /tmp/preserve.html || { echo "FAIL: $p missing brand marker"; exit 1; }
done
for f in web/static/index.html web/static/all.html; do
  test -f "$f" || { echo "FAIL: $f missing"; exit 1; }
  echo "$f ✓"
done
# timeline.py / daily.py 不应该被本 plan 修改
diff_lines=$(git diff --stat HEAD -- src/airadar/web/routes/timeline.py src/airadar/web/routes/daily.py | wc -l)
[[ "$diff_lines" -eq 0 ]] || { echo "FAIL: timeline.py/daily.py modified"; git diff --stat HEAD -- src/airadar/web/routes/timeline.py src/airadar/web/routes/daily.py; exit 1; }
echo "timeline.py + daily.py preservation ✓"
# 响应头不应含 Cache-Control（本 plan 不做缓存）
cc=$(curl -sI http://127.0.0.1:8000/ | grep -i '^cache-control:' || true)
[[ -z "$cc" ]] || { echo "FAIL: Cache-Control unexpectedly set: $cc"; exit 1; }
echo "no Cache-Control header ✓"

# 6.6 本地 Playwright 烟测（L2-1 + L2-2 等价）
.venv/bin/python -c "
from playwright.sync_api import sync_playwright
import time

with sync_playwright() as p:
    browser = p.chromium.launch()
    for path in ['/', '/all']:
        url = f'http://127.0.0.1:8000{path}'
        fcps = []
        spinners = 0
        for _ in range(5):
            ctx = browser.new_context()
            page = ctx.new_page()
            t0 = time.time()
            page.goto(url, wait_until='commit')
            try:
                page.wait_for_selector('text=正在加载', timeout=200)
                spinners += 1
            except: pass
            page.wait_for_selector('article.item-row', timeout=5000)
            fcps.append((time.time() - t0) * 1000)
            ctx.close()
        fcps.sort()
        print(f'{path}: spinner {spinners}/5, FCP median {fcps[2]:.0f}ms')
        assert spinners == 0, f'{path} spinner showed locally'
        assert fcps[2] < 500, f'{path} local FCP > 500ms'
    browser.close()
    print('local smoke PASS')
"

# 6.7 SPA preservation (L2-5 三项)：无 document navigation + fetch 走 API
.venv/bin/python -c "
import re
from playwright.sync_api import sync_playwright

API_RE = re.compile(r'/api/v1/(curated|timeline)')

def collect(page):
    doc_requests = []
    api_hits = []
    page.on('request', lambda r: doc_requests.append(r.url) if r.resource_type == 'document' else None)
    page.on('request', lambda r: api_hits.append(r.url) if API_RE.search(r.url) else None)
    return doc_requests, api_hits

with sync_playwright() as p:
    browser = p.chromium.launch()

    # L2-5 (a) + (b): / 上分类切换 + 搜索
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto('http://127.0.0.1:8000/', wait_until='networkidle')
    doc_requests, api_hits = collect(page)
    page.click('[data-category=\"model\"]', timeout=3000)
    page.wait_for_timeout(500)
    before_search = len(api_hits)
    page.fill('#search', 'openai')
    page.wait_for_timeout(500)
    assert not doc_requests, f'/ SPA broken: document requests {doc_requests}'
    assert len(api_hits) >= 2, f'/ expected ≥2 API hits (one per interaction), got {len(api_hits)}: {api_hits}'
    assert len(api_hits) > before_search, f'/ search did not trigger fetch'
    print(f'L2-5 (a)+(b) PASS  ({len(api_hits)} API hits: {api_hits})')
    ctx.close()

    # L2-5 (c): /all 上翻页
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto('http://127.0.0.1:8000/all', wait_until='networkidle')
    doc_requests, api_hits = collect(page)
    next_btn = page.locator('a[rel=\"next\"]').first
    next_btn.click(timeout=5000)
    page.wait_for_timeout(500)
    assert not doc_requests, f'/all SPA broken: document requests {doc_requests}'
    assert len(api_hits) >= 1, f'/all pagination expected ≥1 API hit, got 0'
    print(f'L2-5 (c) PASS  ({len(api_hits)} API hits: {api_hits})')
    ctx.close()

    browser.close()
"

# 6.8 部署后生产实测（L2-1 + L2-2 + L2-4）
.venv/bin/python -c "
from playwright.sync_api import sync_playwright
import time

URLS = [
    ('https://aiplanet.live/', 1500),
    ('https://aiplanet.live/all', 1500),
    ('https://aiplanet.live/?category=ai-models', 1500),
    ('https://aiplanet.live/?q=openai', 1500),
]

with sync_playwright() as p:
    browser = p.chromium.launch()
    all_pass = True
    for url, fcp_budget in URLS:
        spinners = 0
        fcps = []
        for _ in range(5):
            ctx = browser.new_context()
            page = ctx.new_page()
            t0 = time.time()
            page.goto(url, wait_until='commit')
            try:
                page.wait_for_selector('text=正在加载', timeout=200)
                spinners += 1
            except: pass
            page.wait_for_selector('article.item-row', timeout=10000)
            fcps.append((time.time() - t0) * 1000)
            ctx.close()
        fcps.sort()
        median = fcps[2]
        ok_spinner = spinners <= 1
        ok_fcp = median <= fcp_budget
        status = 'PASS' if (ok_spinner and ok_fcp) else 'FAIL'
        print(f'{status} {url}: spinner {spinners}/5, FCP median {median:.0f}ms')
        if not (ok_spinner and ok_fcp): all_pass = False
    browser.close()
    assert all_pass, 'production verify failed'
    print('production verify PASS')
"
```

### 人工 gate

**L2-7 视觉对比 rubric**：
1. 准备：两个浏览器窗口并排，每个宽度 ≥ 1280px，同一缩放比例。
2. 左窗口打开 `plans/20260528-ssr-preload/baseline-screenshots/<page>.png`，右窗口打开生产 `https://aiplanet.live/<page>`。
3. 按下面 4×4 表格逐格打分（PASS / FAIL / borderline + 注释）：

| 页面 / 维度 | 布局 | 字体 | 配色 | 卡片样式 |
|------------|------|------|------|---------|
| `/` |  |  |  |  |
| `/all` |  |  |  |  |
| `/daily` |  |  |  |  |
| `/about` |  |  |  |  |

4. 通过条件：16 格全部 PASS。任一 FAIL 或 ≥ 2 格 borderline → 整体 FAIL，回到实施修复。

## 7. Risks

- **R1** — timeline 偶发延迟（实测大多数 ~13ms，但历史 cold cache / 写锁场景有 1.2s 记录）。**缓解**：本 plan 不预先做预计算（§9 evidence 表明常规延迟够用）；若 L2-2 在 `/all` 上不达标（中位数 > 1.5s 或多次超时），follow-up plan 加 timeline 预计算（仿照 curated/precompute.py 模式）。**trigger response**：L2-2 verify 失败 → 不部署，开 follow-up plan。
- **R2** — Jinja2 模板首次引入，模板结构错可能让 2 个 SSR 页面同时 broken。**缓解**：先 1:1 复制（保证字节等价），再在 `</body>` 前注入 preload。step 1 的 diff verify 兜底。**trigger response**：若 diff 出现非 preload 行差异 → 回到 1:1 复制重做，不要现场修模板（避免不可逆漂移）。
- **R3** — 静态 HTML mount 与动态路由优先级冲突。**缓解**：FastAPI 路由匹配是定义顺序，确保动态路由在 `app.mount("/", StaticFiles(...))` 之前定义（当前 app.py 已是此顺序）。**trigger response**：若部署后 `/` 返回 static index.html 而非 Jinja 渲染版本 → 检查 app.py 路由定义顺序，动态路由必须在 mount 之前。

## 8. Defaulted Decisions（planner 拍板，未问用户）

| 决策 | 默认值 | 理由 |
|------|--------|------|
| 模板引擎 | Jinja2 | FastAPI 官方推荐、starlette 自带、社区成熟 |
| daily / about 保持现状 | 不改 | daily API < 2ms 无 spinner；about 无数据需求 |
| 旧静态 HTML | `web/static/{index,all}.html` 保留不删 | 失败回滚路径；后续 commit 再清理 |
| Timeline 预计算 | 不本 plan 做 | §9 实测 ~13ms 够用；预防性优化无必要；若 L2-2 不达标则 follow-up |
| 缓存 header（Cache-Control + ETag） | 不本 plan 做 | 移到 follow-up；stated goal 是消除 spinner，单次 SSR 17ms 已达标 |
| base.html 公共部分抽取 | 不本 plan 做 | 移到 follow-up；与 stated goal 无关，是 DRY 重构 |
| 测试组织 | 扩展 `tests/test_web_routes.py`，不新建文件 | 复用 setup；与项目测试组织模式一致 |

## 9. Probe 结果记录（plan finalize 前已验证）

| Probe | 结果 |
|-------|------|
| `curated()` 函数签名是否含 `category`、`q` 参数 | ✓ `src/airadar/web/routes/curated.py:156-162` 签名为 `(request, run_id, date, category, q)` |
| `timeline()` 函数签名是否含 `channel`、`category`、`page`、`q` 参数 | ✓ `src/airadar/web/routes/timeline.py:23-31` 签名为 `(request, cursor, limit, page, channel, category, q)` |
| `timeline` 端点当前延迟（origin 本地，10 次采样） | 9-24ms（中位数 ~13ms）。历史曾见 1.2s 波动（cold cache / 写锁），常规请求快 |
| Cloudflare tunnel 是否透传 cache header | 当前 origin 未设 cache header；CF 返回 `cf-cache-status: DYNAMIC` 表明 origin 控制。CF 不主动剥离也不主动注入。本 plan 不加缓存所以风险不适用；follow-up plan 加缓存时需要再次验证 |
