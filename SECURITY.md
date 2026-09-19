# Security Policy

## Reporting a vulnerability

Please do not open a public issue for security problems. Use GitHub's private
vulnerability reporting on this repository ("Security" tab → "Report a
vulnerability"), or contact the maintainer through the address on the GitHub
profile. Include the affected route, script or component, reproduction steps
and the impact you observed. You will get an acknowledgement within a week.

## Scope

- The FastAPI web application under `src/airadar/web/`.
- The ingestion pipeline (`src/airadar/fetcher/`, `wechat_discovery/`,
  `interpret/`), which processes untrusted feed content.
- Deployment material under `deploy/` and the root scripts.

The public site is read-only: there are no accounts, sessions or write
endpoints. The operator surface (`/admin*`, `/api/v1/admin/*`) is protected by
a shared token (`AI_RADAR_ADMIN_TOKEN`); a bypass of that guard is in scope.

## Supported versions

Only the `main` branch is supported. Dependencies are pinned in `uv.lock` and
deployed with `uv sync --locked`; Dependabot alerts and security updates are
enabled on the repository.
