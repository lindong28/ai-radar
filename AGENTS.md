# Repository Guidelines

## Project Overview

AI Radar is a Python 3.12 FastAPI application for collecting AI-related RSS, X-compatible RSS, and WeChat sources, scoring and curating items, and serving a public read-only web UI.

## Working Rules

- Keep runtime secrets and local deployment paths out of git. Use `.env`, environment variables, or gitignored generated config files.
- Prefer configuration with neutral defaults over hardcoded maintainer identity, domains, or local filesystem paths.
- Optional external integrations must fail closed or skip cleanly when disabled or unconfigured.
- Use `uv run` for Python commands so tools run inside the project environment.
- When running focused pytest commands that touch the database, set `AI_RADAR_DB` to a temporary path to avoid collisions with local services.

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
