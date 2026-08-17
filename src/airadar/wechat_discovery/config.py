from __future__ import annotations

import re
import tomllib
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from .models import AccountConfig, DiscoveryConfig, IdentityProof

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "data" / "wechat-discovery.toml"
_BIZ_RE = re.compile(r"^[A-Za-z0-9+/]{4,}={0,2}$")
_ROOT_KEYS = frozenset(
    {"version", "manual_backend_requests_enabled", "refresh_interval_minutes", "account"}
)
_ACCOUNT_KEYS = frozenset({"name", "public_biz", "seed_urls", "identity"})
_IDENTITY_KEYS = frozenset(
    {"seed_url", "observed_name", "observed_public_biz", "observed_at"}
)


def _seed_url(value: object, *, account_name: str) -> str:
    url = str(value).strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname != "mp.weixin.qq.com":
        raise ValueError(f"invalid seed URL for {account_name}")
    if not parsed.path.startswith("/s"):
        raise ValueError(f"invalid seed URL for {account_name}")
    return url


def load_discovery_config(path: str | Path = DEFAULT_CONFIG_PATH) -> DiscoveryConfig:
    config_path = Path(path)
    raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    unknown_root_keys = set(raw) - _ROOT_KEYS
    if unknown_root_keys:
        raise ValueError(f"unknown WeChat discovery config key: {sorted(unknown_root_keys)[0]}")
    version = raw.get("version")
    if version != 3:
        raise ValueError("unsupported WeChat discovery config version")
    manual_backend_requests_enabled = raw.get("manual_backend_requests_enabled")
    if not isinstance(manual_backend_requests_enabled, bool):
        raise ValueError(
            "WeChat discovery manual_backend_requests_enabled must be true or false"
        )
    interval = raw.get("refresh_interval_minutes")
    if not isinstance(interval, int) or interval < 60:
        raise ValueError("WeChat discovery refresh_interval_minutes must be at least 60")
    raw_accounts = raw.get("account")
    if not isinstance(raw_accounts, list) or not raw_accounts:
        raise ValueError("WeChat discovery config must contain at least one account")

    accounts: list[AccountConfig] = []
    names: set[str] = set()
    businesses: set[str] = set()
    for raw_account in raw_accounts:
        if not isinstance(raw_account, dict):
            raise ValueError("WeChat discovery account must be a table")
        unknown_account_keys = set(raw_account) - _ACCOUNT_KEYS
        if unknown_account_keys:
            raise ValueError(
                f"unknown WeChat discovery account key: {sorted(unknown_account_keys)[0]}"
            )
        name = str(raw_account.get("name", "")).strip()
        public_biz = str(raw_account.get("public_biz", "")).strip()
        if not name:
            raise ValueError("WeChat discovery account name must not be empty")
        if not _BIZ_RE.fullmatch(public_biz):
            raise ValueError(f"invalid public_biz for {name}")
        if name in names:
            raise ValueError(f"duplicate account name: {name}")
        if public_biz in businesses:
            raise ValueError(f"duplicate public_biz: {public_biz}")
        raw_seeds = raw_account.get("seed_urls", [])
        if not isinstance(raw_seeds, list):
            raise ValueError(f"seed_urls for {name} must be an array")
        seeds = tuple(_seed_url(seed, account_name=name) for seed in raw_seeds)
        raw_identity = raw_account.get("identity")
        identity: IdentityProof | None = None
        if raw_identity is not None:
            if not isinstance(raw_identity, dict) or set(raw_identity) != _IDENTITY_KEYS:
                raise ValueError(f"identity proof for {name} has invalid fields")
            identity_seed = _seed_url(raw_identity["seed_url"], account_name=name)
            observed_name = str(raw_identity["observed_name"]).strip()
            observed_public_biz = str(raw_identity["observed_public_biz"]).strip()
            observed_at = str(raw_identity["observed_at"]).strip()
            try:
                observed_at_value = datetime.fromisoformat(observed_at)
            except ValueError as exc:
                raise ValueError(f"identity observed_at for {name} is invalid") from exc
            today = date.today()
            observed_date = observed_at_value.date()
            if observed_date > today:
                raise ValueError(f"identity observed_at for {name} is in the future")
            if identity_seed not in seeds or observed_public_biz != public_biz:
                raise ValueError(
                    f"identity proof for {name} does not match its seed and public_biz"
                )
            if re.sub(r"\s+", "", observed_name) != re.sub(r"\s+", "", name):
                raise ValueError(f"identity proof for {name} does not match its name")
            identity = IdentityProof(
                identity_seed, observed_name, observed_public_biz, observed_at
            )
        accounts.append(
            AccountConfig(
                name=name,
                public_biz=public_biz,
                seed_urls=seeds,
                identity_proof=identity,
            )
        )
        names.add(name)
        businesses.add(public_biz)
    return DiscoveryConfig(
        version=version,
        manual_backend_requests_enabled=manual_backend_requests_enabled,
        refresh_interval=timedelta(minutes=interval),
        accounts=tuple(accounts),
    )
