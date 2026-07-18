from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from ...admin.metrics import collect_metrics
from ...admin.performance import collect_performance_status
from ...admin.usage import collect_usage
from ..envelope import ok

router = APIRouter()
LOCAL_ADMIN_HOSTS = {"127.0.0.1", "::1", "localhost"}
ADMIN_ALLOW_LOCAL_ENV = "AI_RADAR_ADMIN_ALLOW_LOCAL"
TRUTHY_ENV_VALUES = {"1", "true", "yes"}


def _allow_local_admin_bypass() -> bool:
    return os.environ.get(ADMIN_ALLOW_LOCAL_ENV, "").strip().lower() in TRUTHY_ENV_VALUES


def _is_local_request(request: Request) -> bool:
    if not _allow_local_admin_bypass():
        return False
    client = request.client
    return bool(client and client.host in LOCAL_ADMIN_HOSTS)


def require_admin_access(request: Request) -> None:
    if _is_local_request(request):
        return
    if request.headers.get("Cf-Access-Jwt-Assertion"):
        return
    raise HTTPException(status_code=403, detail="Cloudflare Access required")


def collect_admin_metrics(request: Request) -> dict[str, object]:
    pipeline_log_dir = getattr(request.app.state, "pipeline_log_dir", None)
    access_log_paths = getattr(request.app.state, "access_log_paths", None)
    return collect_metrics(
        db_path=getattr(request.app.state, "db_path", None),
        pipeline_log_dir=Path(pipeline_log_dir) if pipeline_log_dir is not None else None,
        access_log_paths=access_log_paths,
    )


def collect_admin_usage(request: Request) -> dict[str, object]:
    return collect_usage(
        db_path=getattr(request.app.state, "db_path", None),
        usage_db_path=getattr(request.app.state, "usage_db_path", None),
    )


@router.get("/admin/metrics")
def admin_metrics(request: Request) -> dict[str, object]:
    require_admin_access(request)
    return ok(collect_admin_metrics(request))


@router.get("/admin/usage")
def admin_usage(request: Request) -> dict[str, object]:
    require_admin_access(request)
    return ok(collect_admin_usage(request))


@router.get("/admin/performance")
def admin_performance(request: Request) -> dict[str, object]:
    require_admin_access(request)
    return ok(collect_performance_status())
