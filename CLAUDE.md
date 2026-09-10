# Repository Guidelines

## Project Overview

AI Radar is a Python 3.12 FastAPI application for collecting AI-related RSS, X-compatible RSS, and WeChat sources, scoring and curating items, and serving a public read-only web UI.

## Working Rules

- Keep runtime secrets and local deployment paths out of git. Use `.env`, environment variables, or gitignored generated config files.
- Prefer configuration with neutral defaults over hardcoded maintainer identity, domains, or local filesystem paths.
- Optional external integrations must fail closed or skip cleanly when disabled or unconfigured.
- Use `uv run` for Python commands so tools run inside the project environment.
- When running focused pytest commands that touch the database, set `AI_RADAR_DB` to a temporary path to avoid collisions with local services.

## 对齐 AIHOT：达标线与迭代机制 (BINDING)

本仓的长期目标之一是**在用户可见的指标上足够接近 AIHOT**。这条工作有一套已定的判据与回路，
**接手它之前先读** [docs/references/aihot-approximation-metrics.md](docs/references/aihot-approximation-metrics.md)——
达标线、两个指标家族、数据数量达标的样本量表、以及外层归因回路都在那里，此处只放不可省的三条：

- **达标线**（用户 2026-09-10 裁定）= **逐类占比落进 AIHOT 该类的 95% CI**，不是整页 TV
  （TV 的绝对值**指不出是哪一类**——相互抵消的偏差在它上面看不见）。权威口径是**生产深度**（用户看到的就是那 40 条，
  且它的 n 是对齐深度的 2.6 倍、判据在这里才有分辨力）：
  `uv run python scripts/eval/measure_curated_composition.py --depth ours --labels off --record`。
  **看 `P(5/5)` 那一行再看 `k/5`**：判据自己的零假设，生产深度 0.949、对齐深度只有 0.602——
  后者下 `3/5` 与「完美页面」区分不开（p=0.102），前者下 `3/5` 是真信号（p=0.002）。
- **每完成一轮迭代，给那次读数补一个 `--record`**：它追加到 `scripts/eval/composition-history.jsonl`，
  那是"随迭代逐步逼近"这条期望**唯一**的观测面。不 record，趋势就不存在——探索性对照别 record。
- **归因先于干预**：动 prompt / 权重 / 系数之前，先在
  [docs/issues/aihot-fit-eval.md](docs/issues/aihot-fit-eval.md) 写下带
  `differential_prediction` 的假设（取数**之前**写），再取对照读数。本仓已有多次"先取数后解释"
  导致结论自撤的记录，那一节列着。

**CI 会随数据积累变窄，所以判据会变严**：同一个系统的某一类可能从 IN 变成 OUT。那不是回归，
是分辨力提高——读到这种翻转先看 `n_reference` 有没有变大。

## Frontend Asset Cache Busting (BINDING)

改完 `web/static/app.js` 或 `web/static/style.css`，**跑一次** `uv run python scripts/bump_frontend_assets.py`——它按内容摘要重算 `?v=` 版本串、改写全部 HTML 引用并更新 `web/asset-pins.json`。EdgeOne 对这两个精确路径强制节点缓存 7 天（[ADR-039](docs/adr/039-route-news-through-edgeone-dns-only-cname.md)「决策」节），漏 bump 就是**部署了但线上不生效**。

- 版本串必须与资源内容一起进**同一发布单元**——不是本地 commit：按 [ADR-042](docs/adr/042-isolate-production-deploy-commit-from-local-main.md) 生产部署 commit 是在 `tencent/main` 上复放出来的，复放时挑漏 HTML 则本地 commit 再完整也破契约。
- `uv run pytest tests/test_frontend_asset_versions.py` 只保证**仓内**一致；**已上线的边缘陈旧只能从真实公网观测**——origin 已是新代码而部分边缘节点仍吐旧副本，本地与 curl 都可能看不出来。
- 改 style.css 时 `/wechat` **要多做一件事、不是少做**：它把 style.css 内联进 SSR HTML，故没有 `style.css?v=` 可 bump，改为按 ADR-039「决策」节里那条内联契约，在约 120 秒缓存窗口后从真实公网 `/wechat` 验证内联内容。（它照常引用 `app.js?v=`，改 app.js 时不例外。）

- 部署前跑 `./run.sh admin edgeone check`：强制缓存规则住在腾讯云控制台，是仓外权威，控制台多出一条路径时仓内测试全绿也看不见。**exit 2 表示未核实、不等于通过**（0=无漂移，1=有漂移，2=未核实）。

bump 范围为何是"全部 HTML"、以及已上线后的补救，见 [docs/experiences/frontend.md](docs/experiences/frontend.md)。

## Useful Commands

```bash
uv sync
./run.sh admin db migrate
./run.sh admin sources reload
./run.sh fetch
./run.sh serve --host 127.0.0.1 --port 8000
uv run pytest
uv run ruff check src tests
uv run mypy src
```

## Verification Notes

- For source-snapshot checks, use `git grep` rather than broad filesystem scans so generated, ignored, and local-only files do not produce false positives.
- For open-source readiness comparisons, use the immutable `opensource-baseline` tag when a before/current comparison is required.
- Do not rewrite repository history from this checkout; any history cleansing must happen in a disposable clone.
