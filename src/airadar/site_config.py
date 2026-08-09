from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

DEFAULT_REPO_URL = "https://github.com/your-org/ai-radar"
DEFAULT_MAINTAINER = "your-name"
VISION_ANCHOR = "#4-%E6%A0%B8%E5%BF%83%E5%8E%9F%E5%88%99-binding"


BEIAN_REGISTRY_URL = "https://beian.miit.gov.cn/"


@dataclass(frozen=True)
class SiteConfig:
    domain: str
    repo_url: str
    maintainer: str
    maintainer_url: str
    x_url: str
    # Mainland China requires a domestically hosted site to display its ICP
    # record number. Kept as configuration rather than a constant so a fork
    # never inherits this deployment's filing; empty means render nothing.
    icp_beian: str = ""

    @property
    def beian_registry_url(self) -> str:
        return BEIAN_REGISTRY_URL

    @property
    def repo_label(self) -> str:
        return self.repo_url.removeprefix("https://").removeprefix("http://").rstrip("/")

    @property
    def vision_url(self) -> str:
        return f"{self.repo_url.rstrip('/')}/blob/main/docs/prd/VISION.md{VISION_ANCHOR}"

    @property
    def x_label(self) -> str:
        path = urlparse(self.x_url).path.strip("/")
        if path:
            return f"@{path.split('/')[-1].lstrip('@')}"
        return self.x_url


def _env_text(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return value.strip()


def site_domain() -> str:
    raw = _env_text("AI_RADAR_SITE_DOMAIN")
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    domain = parsed.netloc or parsed.path
    return domain.strip().strip("/")


def site_origin() -> str | None:
    domain = site_domain()
    return f"https://{domain}" if domain else None


def site_user_agent() -> str:
    origin = site_origin()
    return f"ai-radar/0.1 (+{origin})" if origin else "ai-radar/0.1"


def get_site_config() -> SiteConfig:
    return SiteConfig(
        domain=site_domain(),
        repo_url=_env_text("AI_RADAR_SITE_REPO_URL", DEFAULT_REPO_URL),
        maintainer=_env_text("AI_RADAR_SITE_MAINTAINER", DEFAULT_MAINTAINER),
        maintainer_url=_env_text("AI_RADAR_SITE_MAINTAINER_URL"),
        x_url=_env_text("AI_RADAR_SITE_X_URL"),
        icp_beian=_env_text("AI_RADAR_ICP_BEIAN"),
    )
