from __future__ import annotations

import html
import json
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from urllib.request import ProxyHandler, Request, build_opener

from .models import AccountConfig, DiscoveryArticle

APPMSGPUBLISH_URL = "https://mp.weixin.qq.com/cgi-bin/appmsgpublish"
SEARCHBIZ_URL = "https://mp.weixin.qq.com/cgi-bin/searchbiz"
DEFAULT_SESSION_PATH = Path(__file__).resolve().parents[3] / "data" / "wechat-discovery-session.json"
_BIZ_PATTERNS = (
    re.compile(r"\bvar\s+biz\s*=\s*['\"]([^'\"]+)['\"]"),
    re.compile(r"(?:[?&]|&amp;)__biz=([^&#'\"\s]+)"),
)


class DiscoveryError(RuntimeError):
    """Base class whose messages are safe to persist and display."""


class DiscoveryPlatformResultError(DiscoveryError):
    def __init__(self, message: str, *, platform_error_ret: int) -> None:
        super().__init__(message)
        self.platform_error_ret = platform_error_ret


class DiscoveryAuthRequired(DiscoveryPlatformResultError):
    pass


class DiscoveryRateLimited(DiscoveryPlatformResultError):
    pass


class DiscoveryPlatformRejected(DiscoveryPlatformResultError):
    pass


class DiscoveryRequestFailed(DiscoveryError):
    pass


class DiscoveryResponseInvalid(DiscoveryError):
    pass


class DiscoveryIdentityUnverified(DiscoveryError):
    pass


class DiscoveryIdentityNoMatch(DiscoveryIdentityUnverified):
    pass


class DiscoveryIdentityAmbiguous(DiscoveryIdentityUnverified):
    pass


class DiscoveryIdentityMismatch(DiscoveryIdentityUnverified):
    pass


@dataclass(frozen=True, repr=False)
class DiscoveryCredentials:
    token: str
    cookie_header: str

    def __repr__(self) -> str:
        return "DiscoveryCredentials(token=<redacted>, cookie_header=<redacted>)"

    __str__ = __repr__


@dataclass(frozen=True, repr=False)
class SearchBizCandidate:
    account_name: str
    fakeid: str

    def __repr__(self) -> str:
        return (
            "SearchBizCandidate(account_name="
            f"{self.account_name!r}, fakeid=<redacted>)"
        )


@dataclass(frozen=True, repr=False)
class ProvisionalIdentity:
    account_name: str
    fakeid: str

    def __repr__(self) -> str:
        return (
            "ProvisionalIdentity(account_name="
            f"{self.account_name!r}, fakeid=<redacted>)"
        )


def load_credentials(path: str | Path = DEFAULT_SESSION_PATH) -> DiscoveryCredentials:
    credential_path = Path(path)
    mode = credential_path.stat().st_mode & 0o777
    if mode & 0o077:
        raise PermissionError("WeChat discovery session file must have private permissions (0600)")
    try:
        raw = json.loads(credential_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("WeChat discovery session file is unreadable or invalid") from exc
    if not isinstance(raw, dict):
        raise ValueError("WeChat discovery session file is invalid")
    unknown_keys = set(raw) - {"version", "token", "cookies"}
    if unknown_keys:
        raise ValueError("WeChat discovery session file has unknown fields")
    if raw.get("version") != 1:
        raise ValueError("unsupported WeChat discovery session version")
    token = str(raw.get("token", "")).strip()
    raw_cookies = raw.get("cookies")
    if not token or not isinstance(raw_cookies, list):
        raise ValueError("WeChat discovery session file is missing required fields")
    cookies: list[str] = []
    for cookie in raw_cookies:
        if not isinstance(cookie, dict):
            continue
        domain = str(cookie.get("domain", "")).lstrip(".").lower()
        name = str(cookie.get("name", "")).strip()
        value = str(cookie.get("value", "")).strip()
        if (domain == "weixin.qq.com" or domain.endswith(".weixin.qq.com")) and name and value:
            cookies.append(f"{name}={value}")
    if not cookies:
        raise ValueError("WeChat discovery session file has no applicable cookies")
    return DiscoveryCredentials(token=token, cookie_header="; ".join(cookies))


def extract_biz(page_html: str) -> str | None:
    decoded = html.unescape(page_html)
    for pattern in _BIZ_PATTERNS:
        match = pattern.search(decoded)
        if match:
            return match.group(1).strip()
    return None


def bootstrap_biz(seed_pages: list[str] | tuple[str, ...]) -> str:
    for page_html in seed_pages:
        biz = extract_biz(page_html)
        if biz:
            return biz
    raise DiscoveryResponseInvalid("no biz was found in the supplied public article pages")


def verify_account_identity(
    account: AccountConfig,
) -> None:
    proof = account.identity_proof
    if proof is None:
        raise DiscoveryIdentityUnverified("account has no reviewed public seed identity proof")
    if (
        proof.seed_url not in account.seed_urls
        or proof.observed_public_biz != account.public_biz
    ):
        raise DiscoveryIdentityUnverified("public seed identity proof does not match the account biz")
    if re.sub(r"\s+", "", proof.observed_name) != re.sub(r"\s+", "", account.name):
        raise DiscoveryIdentityUnverified("public seed identity proof does not match the account name")


def normalized_account_name(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).casefold()


def _platform_result(payload: dict[str, object]) -> None:
    base_resp = payload.get("base_resp")
    if not isinstance(base_resp, dict) or "ret" not in base_resp:
        raise DiscoveryResponseInvalid("WeChat response is missing base_resp.ret")
    raw_ret = base_resp["ret"]
    if not isinstance(raw_ret, int) or isinstance(raw_ret, bool):
        raise DiscoveryResponseInvalid("WeChat response has an invalid base_resp.ret")
    ret = raw_ret
    message = str(base_resp.get("err_msg", "")).lower()
    if ret == 0:
        return
    if ret in {200003, -3} or (ret == -1 and ("session" in message or "登录" in message)):
        raise DiscoveryAuthRequired(
            f"WeChat admin authentication is required (ret={ret})",
            platform_error_ret=ret,
        )
    if ret == 200013 or "freq" in message or "frequent" in message:
        raise DiscoveryRateLimited(
            f"WeChat admin request was rate limited (ret={ret})",
            platform_error_ret=ret,
        )
    raise DiscoveryPlatformRejected(
        f"WeChat admin rejected the request (ret={ret})",
        platform_error_ret=ret,
    )


def _nested_object(value: object, *, field: str) -> dict[str, object]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise DiscoveryResponseInvalid(f"WeChat response field {field} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise DiscoveryResponseInvalid(f"WeChat response field {field} is not an object")
    return value


def _article_url(value: object) -> str:
    parts = urlsplit(str(value).strip())
    path_parts = [part for part in parts.path.split("/") if part]
    valid_path = parts.path.rstrip("/") == "/s" or (
        len(path_parts) == 2 and path_parts[0] == "s"
    )
    try:
        port = parts.port
    except ValueError as exc:
        raise DiscoveryResponseInvalid(
            "WeChat response contains an invalid public article URL"
        ) from exc
    if (
        parts.scheme != "https"
        or parts.hostname != "mp.weixin.qq.com"
        or parts.username is not None
        or parts.password is not None
        or port not in {None, 443}
        or not valid_path
    ):
        raise DiscoveryResponseInvalid("WeChat response contains an invalid public article URL")
    return urlunsplit(("https", parts.netloc, parts.path, parts.query, ""))


def observed_article_biz(url: str) -> str:
    canonical_url = _article_url(url)
    values = parse_qs(urlsplit(canonical_url).query, keep_blank_values=True).get("__biz")
    if values is None or len(values) != 1 or not values[0]:
        raise DiscoveryIdentityUnverified(
            "WeChat article URL does not expose a unique observed account biz"
        )
    return values[0]


def _published_at(item: dict[str, object]) -> datetime:
    raw = item.get("update_time") or item.get("create_time")
    if not isinstance(raw, (str, int, float)):
        raise DiscoveryResponseInvalid("WeChat response contains an invalid publication time")
    try:
        timestamp = int(raw)
    except (TypeError, ValueError) as exc:
        raise DiscoveryResponseInvalid("WeChat response contains an invalid publication time") from exc
    try:
        return datetime.fromtimestamp(timestamp, UTC)
    except (OSError, OverflowError, ValueError) as exc:
        raise DiscoveryResponseInvalid("WeChat response contains an invalid publication time") from exc


def parse_appmsgpublish(
    payload: dict[str, object],
    *,
    account_name: str,
    expected_biz: str,
) -> list[DiscoveryArticle]:
    _platform_result(payload)
    if "publish_page" not in payload:
        raise DiscoveryResponseInvalid("WeChat response is missing publish_page")
    publish_page = _nested_object(payload["publish_page"], field="publish_page")
    publish_list = publish_page.get("publish_list")
    if not isinstance(publish_list, list):
        raise DiscoveryResponseInvalid("WeChat response publish_list is not an array")

    articles: list[DiscoveryArticle] = []
    for publish_item in publish_list:
        if not isinstance(publish_item, dict) or "publish_info" not in publish_item:
            raise DiscoveryResponseInvalid(
                "WeChat response publish_list item is missing publish_info"
            )
        publish_info = _nested_object(publish_item["publish_info"], field="publish_info")
        raw_articles = publish_info.get("appmsgex")
        if not isinstance(raw_articles, list):
            raise DiscoveryResponseInvalid(
                "WeChat response publish_info appmsgex is not an array"
            )
        for raw_article in raw_articles:
            if not isinstance(raw_article, dict):
                raise DiscoveryResponseInvalid("WeChat response contains a non-object article")
            title = str(raw_article.get("title", "")).strip()
            if not title:
                raise DiscoveryResponseInvalid("WeChat response contains an article without a title")
            url = _article_url(raw_article.get("link") or raw_article.get("url"))
            observed_biz = observed_article_biz(url)
            if observed_biz != expected_biz:
                raise DiscoveryIdentityMismatch(
                    "WeChat article URL biz does not match the configured account identity"
                )
            articles.append(
                DiscoveryArticle(
                    account_name=account_name,
                    biz=observed_biz,
                    title=title,
                    url=url,
                    author=str(raw_article.get("author") or account_name).strip(),
                    published_at=_published_at(raw_article),
                )
            )
    if publish_list and not articles:
        raise DiscoveryResponseInvalid("WeChat publish_list contained no usable articles")
    deduplicated: dict[str, DiscoveryArticle] = {}
    for article in articles:
        if article.url in deduplicated:
            raise DiscoveryResponseInvalid(
                "WeChat response contains a duplicate article URL"
            )
        deduplicated[article.url] = article
    return list(deduplicated.values())


def parse_searchbiz(payload: dict[str, object]) -> list[SearchBizCandidate]:
    _platform_result(payload)
    raw_accounts = payload.get("list")
    if not isinstance(raw_accounts, list):
        raise DiscoveryResponseInvalid("WeChat searchbiz response list is not an array")
    raw_total = payload.get("total")
    if isinstance(raw_total, bool) or not isinstance(raw_total, (str, int, float)):
        raise DiscoveryResponseInvalid("WeChat searchbiz response total is invalid")
    try:
        total = int(raw_total)
    except (TypeError, ValueError) as exc:
        raise DiscoveryResponseInvalid("WeChat searchbiz response total is invalid") from exc
    if total < 0 or total != len(raw_accounts):
        raise DiscoveryIdentityAmbiguous(
            "WeChat searchbiz response is not complete enough to prove a unique identity"
        )
    accounts: list[SearchBizCandidate] = []
    for raw_account in raw_accounts:
        if not isinstance(raw_account, dict):
            raise DiscoveryResponseInvalid("WeChat searchbiz response contains a non-object account")
        account_name = str(raw_account.get("nickname", "")).strip()
        fakeid = str(raw_account.get("fakeid", "")).strip()
        if not account_name or not fakeid:
            raise DiscoveryResponseInvalid(
                "WeChat searchbiz response contains an account without required candidate fields"
            )
        accounts.append(SearchBizCandidate(account_name=account_name, fakeid=fakeid))
    return accounts


def select_unique_searchbiz_candidate(
    account: AccountConfig, candidates: list[SearchBizCandidate]
) -> ProvisionalIdentity:
    expected_name = normalized_account_name(account.name)
    matches = [
        (candidate.account_name, candidate.fakeid)
        for candidate in candidates
        if normalized_account_name(candidate.account_name) == expected_name
    ]
    if not matches:
        raise DiscoveryIdentityNoMatch(
            "searchbiz returned no account matching the configured normalized name"
        )
    if len(matches) != 1:
        raise DiscoveryIdentityAmbiguous(
            "searchbiz returned multiple accounts matching the configured normalized name"
        )
    account_name, fakeid = matches[0]
    return ProvisionalIdentity(account_name=account_name, fakeid=fakeid)


RequestJson = Callable[[Request, float], dict[str, object]]


def _default_request_json(request: Request, timeout: float) -> dict[str, object]:
    opener = build_opener(ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:  # noqa: S310 - fixed HTTPS endpoint
        try:
            payload = json.loads(response.read().decode("utf-8", "replace"))
        except json.JSONDecodeError as exc:
            raise DiscoveryResponseInvalid("WeChat admin response is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise DiscoveryResponseInvalid("WeChat admin response is not a JSON object")
    return payload


class WeChatAdminClient:
    def __init__(
        self,
        credentials: DiscoveryCredentials,
        *,
        request_json: RequestJson = _default_request_json,
        timeout_seconds: float = 20,
    ) -> None:
        self._credentials = credentials
        self._request_json = request_json
        self._timeout_seconds = timeout_seconds

    def __repr__(self) -> str:
        return f"WeChatAdminClient(timeout_seconds={self._timeout_seconds})"

    def _request(self, url: str) -> dict[str, object]:
        request = Request(
            url,
            headers={
                "Accept": "application/json, text/plain, */*",
                "Cookie": self._credentials.cookie_header,
                "Origin": "https://mp.weixin.qq.com",
                "Referer": "https://mp.weixin.qq.com/",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            },
        )
        try:
            return self._request_json(request, self._timeout_seconds)
        except DiscoveryError:
            raise
        except Exception as exc:
            raise DiscoveryRequestFailed("WeChat admin request failed before a valid response") from exc

    def search_accounts(
        self, *, account_name: str, count: int = 50
    ) -> list[SearchBizCandidate]:
        if not 1 <= count <= 50:
            raise ValueError("WeChat searchbiz count must be between 1 and 50")
        query = urlencode(
            {
                "action": "search_biz",
                "begin": 0,
                "count": count,
                "query": account_name,
                "token": self._credentials.token,
                "lang": "zh_CN",
                "f": "json",
                "ajax": 1,
            }
        )
        return parse_searchbiz(self._request(f"{SEARCHBIZ_URL}?{query}"))

    def fetch_latest(
        self, *, account_name: str, biz: str, fakeid: str, count: int = 5
    ) -> list[DiscoveryArticle]:
        if not 1 <= count <= 20:
            raise ValueError("WeChat discovery count must be between 1 and 20")
        if not fakeid.strip():
            raise ValueError("WeChat discovery fakeid must not be empty")
        query = urlencode(
            {
                "sub": "list",
                "sub_action": "list_ex",
                "search_field": "null",
                "begin": 0,
                "count": count,
                "query": "",
                "fakeid": fakeid,
                "type": "101_1",
                "free_publish_type": 1,
                "token": self._credentials.token,
                "lang": "zh_CN",
                "f": "json",
                "ajax": 1,
            }
        )
        payload = self._request(f"{APPMSGPUBLISH_URL}?{query}")
        return parse_appmsgpublish(payload, account_name=account_name, expected_biz=biz)
