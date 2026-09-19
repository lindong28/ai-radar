from __future__ import annotations

import hmac
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response

from ...admin.metrics import collect_metrics
from ...admin.performance import collect_performance_status
from ...admin.usage import collect_usage
from ...runtime_env import read_value
from ..envelope import ok

router = APIRouter()
LOCAL_ADMIN_HOSTS = {"127.0.0.1", "::1", "localhost"}
ADMIN_ALLOW_LOCAL_ENV = "AI_RADAR_ADMIN_ALLOW_LOCAL"
# Shared origin secret for the operator surface. The previous guard accepted
# any non-empty `Cf-Access-Jwt-Assertion` header on the assumption that a
# Cloudflare Access edge sat in front of the origin; production moved to
# EdgeOne (ADR-039) and never had that edge, so the header was spoofable by
# anyone. The token is compared in constant time and the guard fails closed
# when nothing is configured.
ADMIN_TOKEN_ENV = "AI_RADAR_ADMIN_TOKEN"
ADMIN_TOKEN_HEADER = "X-Admin-Token"
ADMIN_TOKEN_MIN_LENGTH = 16
# Every admin response is per-operator and carries the token in the request,
# so it must never be stored by a shared cache: a cached 200 would hand the
# page to the next anonymous visitor. Defence in depth behind the edge rule.
ADMIN_CACHE_CONTROL = "private, no-store"
TRUTHY_ENV_VALUES = {"1", "true", "yes"}


def _allow_local_admin_bypass() -> bool:
    return os.environ.get(ADMIN_ALLOW_LOCAL_ENV, "").strip().lower() in TRUTHY_ENV_VALUES


def _is_local_request(request: Request) -> bool:
    if not _allow_local_admin_bypass():
        return False
    client = request.client
    return bool(client and client.host in LOCAL_ADMIN_HOSTS)


def _configured_admin_token() -> str:
    token = read_value(ADMIN_TOKEN_ENV).strip()
    if len(token) < ADMIN_TOKEN_MIN_LENGTH:
        return ""
    return token


def _presented_admin_token(request: Request) -> str:
    header = request.headers.get(ADMIN_TOKEN_HEADER, "").strip()
    if header:
        return header
    scheme, _, credentials = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() == "bearer":
        return credentials.strip()
    return ""


def require_admin_access(request: Request) -> None:
    if _is_local_request(request):
        return
    expected = _configured_admin_token()
    presented = _presented_admin_token(request)
    if expected and presented and hmac.compare_digest(expected.encode("utf-8"), presented.encode("utf-8")):
        return
    raise HTTPException(status_code=403, detail="admin access denied")


def collect_admin_metrics(request: Request) -> dict[str, object]:
    pipeline_log_dir = getattr(request.app.state, "pipeline_log_dir", None)
    access_log_paths = getattr(request.app.state, "access_log_paths", None)
    return collect_metrics(
        db_path=getattr(request.app.state, "db_path", None),
        pipeline_log_dir=Path(pipeline_log_dir) if pipeline_log_dir is not None else None,
        access_log_paths=access_log_paths,
        alert_state_path=getattr(request.app.state, "alert_state_path", None),
    )


def collect_admin_usage(request: Request) -> dict[str, object]:
    return collect_usage(
        db_path=getattr(request.app.state, "db_path", None),
        usage_db_path=getattr(request.app.state, "usage_db_path", None),
    )


@router.get("/admin/metrics", include_in_schema=False)
def admin_metrics(request: Request, response: Response) -> dict[str, object]:
    require_admin_access(request)
    response.headers["Cache-Control"] = ADMIN_CACHE_CONTROL
    return ok(collect_admin_metrics(request))


@router.get("/admin/usage", include_in_schema=False)
def admin_usage(request: Request, response: Response) -> dict[str, object]:
    require_admin_access(request)
    response.headers["Cache-Control"] = ADMIN_CACHE_CONTROL
    return ok(collect_admin_usage(request))


@router.get("/admin/performance", include_in_schema=False)
def admin_performance(request: Request, response: Response) -> dict[str, object]:
    require_admin_access(request)
    response.headers["Cache-Control"] = ADMIN_CACHE_CONTROL
    return ok(collect_performance_status())
