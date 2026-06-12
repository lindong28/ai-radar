from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..site_config import site_origin

LOCALHOST_ORIGIN_REGEX = r"^http://(localhost|127\.0\.0\.1)(:\d+)?$"


def allowed_origins() -> list[str]:
    origin = site_origin()
    return [origin] if origin else []


def configure_cors(app: FastAPI) -> None:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins(),
        allow_origin_regex=LOCALHOST_ORIGIN_REGEX,
        allow_methods=["GET", "HEAD", "OPTIONS"],
        allow_headers=["*"],
        allow_credentials=False,
    )
