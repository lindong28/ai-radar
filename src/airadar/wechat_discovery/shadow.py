from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from urllib.parse import parse_qs, urlsplit

from .models import DiscoveryArticle, DiscoveryAttempt, TargetIdentityEvidence


@dataclass(frozen=True)
class ShadowComparison:
    account_name: str
    biz: str
    observed_at: datetime
    baseline_count: int
    missing_baseline_urls: tuple[str, ...]
    candidate_only_urls: tuple[str, ...]

    @property
    def covered(self) -> bool:
        return not self.missing_baseline_urls


class ShadowNotComparable(ValueError):
    pass


def canonical_wechat_article_key(url: str) -> tuple[str, ...] | None:
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.hostname != "mp.weixin.qq.com":
        return None
    path_parts = [part for part in parts.path.split("/") if part]
    if len(path_parts) == 2 and path_parts[0] == "s":
        return ("short", path_parts[1])
    if parts.path.rstrip("/") != "/s":
        return None
    query = parse_qs(parts.query, keep_blank_values=True)
    identity: list[str] = []
    for name in ("__biz", "mid", "idx", "sn"):
        values = query.get(name)
        if values is None or len(values) != 1 or not values[0]:
            return None
        identity.append(values[0])
    return ("legacy", *identity)


def _keyed_urls(articles: list[DiscoveryArticle]) -> dict[tuple[str, ...], str]:
    keyed: dict[tuple[str, ...], str] = {}
    for article in articles:
        key = canonical_wechat_article_key(article.url)
        if key is None:
            raise ShadowNotComparable("an article URL has no proven WeChat identity form")
        keyed.setdefault(key, article.url)
    return keyed


def compare_shadow(
    *,
    account_name: str,
    biz: str,
    candidates: list[DiscoveryArticle],
    baseline: list[DiscoveryArticle],
    observed_at: datetime,
) -> ShadowComparison:
    candidate_urls = _keyed_urls([article for article in candidates if article.biz == biz])
    baseline_urls = _keyed_urls([article for article in baseline if article.biz == biz])
    families = {key[0] for key in candidate_urls} | {key[0] for key in baseline_urls}
    if len(families) > 1:
        raise ShadowNotComparable("candidate and baseline URL identity forms differ")
    missing_keys = baseline_urls.keys() - candidate_urls.keys()
    candidate_only_keys = candidate_urls.keys() - baseline_urls.keys()
    return ShadowComparison(
        account_name=account_name,
        biz=biz,
        observed_at=observed_at,
        baseline_count=len(baseline_urls),
        missing_baseline_urls=tuple(sorted(baseline_urls[key] for key in missing_keys)),
        candidate_only_urls=tuple(sorted(candidate_urls[key] for key in candidate_only_keys)),
    )


def compare_shadow_window(
    *,
    account_name: str,
    biz: str,
    baseline: list[DiscoveryArticle],
    since: datetime,
    attempt: DiscoveryAttempt,
) -> ShadowComparison:
    candidates = list(attempt.candidate_snapshot)
    observed_at = attempt.finished_at
    if observed_at is None:
        raise ShadowNotComparable("the probe attempt has no terminal outcome")
    timestamps = [
        since,
        observed_at,
        *(article.published_at for article in candidates),
        *(article.published_at for article in baseline),
    ]
    if any(value.tzinfo is None or value.utcoffset() is None for value in timestamps):
        raise ShadowNotComparable("comparison timestamps must include a timezone")
    if attempt.identity_resolution_origin != "provisional_searchbiz_match":
        raise ShadowNotComparable("the attempt identity resolution origin is invalid")
    if attempt.identity_resolution_id is None:
        raise ShadowNotComparable("the attempt has no provisional searchbiz identity relation")
    if (
        attempt.target_identity_evidence
        is not TargetIdentityEvidence.ARTICLE_URL_PUBLIC_BIZ_VERIFIED
    ):
        raise ShadowNotComparable(
            "the attempt has no article-URL public-biz verification"
        )
    if attempt.requested_page_size_origin == "predates_persistence":
        raise ShadowNotComparable("the attempt predates persisted request page size")
    if attempt.requested_page_size_origin != "recorded":
        raise ShadowNotComparable("the attempt request page size origin is invalid")
    requested_page_size = attempt.requested_page_size
    if requested_page_size is None or not 1 <= requested_page_size <= 20:
        raise ShadowNotComparable("the attempt request page size is invalid")
    if since > observed_at:
        raise ShadowNotComparable("the requested window starts after the attempt finished")
    if not candidates:
        raise ShadowNotComparable("the successful attempt has no candidate snapshot")
    if len(candidates) > requested_page_size:
        raise ShadowNotComparable("the candidate snapshot exceeds the requested page size")
    if any(article.published_at > observed_at for article in candidates):
        raise ShadowNotComparable("a candidate publication time is after the attempt finished")
    if len(candidates) == requested_page_size and min(
        article.published_at for article in candidates
    ) > since:
        raise ShadowNotComparable("the page did not reach the start of the requested window")
    window_candidates = [
        article for article in candidates if since <= article.published_at <= observed_at
    ]
    window_baseline = [
        article for article in baseline if since <= article.published_at <= observed_at
    ]
    if not window_baseline:
        raise ShadowNotComparable("the Mp2RSS baseline is empty in the requested window")
    return compare_shadow(
        account_name=account_name,
        biz=biz,
        candidates=window_candidates,
        baseline=window_baseline,
        observed_at=observed_at,
    )
