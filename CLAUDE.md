# Repository Guidelines

## Project Overview

AI Radar is a Python 3.12 FastAPI application for collecting AI-related RSS, X-compatible RSS, and WeChat sources, scoring and curating items, and serving a public read-only web UI.

## Working Rules

- Keep runtime secrets and local deployment paths out of git. Use `.env`, environment variables, or gitignored generated config files.
- Prefer configuration with neutral defaults over hardcoded maintainer identity, domains, or local filesystem paths.
- Optional external integrations must fail closed or skip cleanly when disabled or unconfigured.
- Use `uv run` for Python commands so tools run inside the project environment.
- When running focused pytest commands that touch the database, set `AI_RADAR_DB` to a temporary path to avoid collisions with local services.

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
