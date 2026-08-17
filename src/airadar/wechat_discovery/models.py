from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class DiscoveryState(StrEnum):
    DISABLED = "disabled"
    UNCONFIGURED = "unconfigured"
    READY = "ready"
    RESERVED = "reserved"
    AUTH_REQUIRED = "auth_required"
    IDENTITY_UNVERIFIED = "identity_unverified"
    IDENTITY_MISMATCH = "identity_mismatch"
    RATE_LIMITED = "rate_limited"
    PLATFORM_REJECTED = "platform_rejected"
    REQUEST_FAILED = "request_failed"
    RESPONSE_INVALID = "response_invalid"
    SUCCESS = "success"
    SUCCESS_NO_NEW_SHADOW_CANDIDATES = "success_no_new_shadow_candidates"
    SUCCESS_WITH_NEW_SHADOW_CANDIDATES = "success_with_new_shadow_candidates"


class DiscoveryGateState(StrEnum):
    DISABLED = "disabled"
    UNCONFIGURED = "unconfigured"
    REQUEST_OUTCOME_UNKNOWN = "request_outcome_unknown"
    COOLDOWN = "cooldown"
    READY_TO_PROBE = "ready_to_probe"
    READY_TO_RESOLVE = "ready_to_resolve"


FAILURE_STATES = frozenset(
    {
        DiscoveryState.AUTH_REQUIRED,
        DiscoveryState.IDENTITY_UNVERIFIED,
        DiscoveryState.IDENTITY_MISMATCH,
        DiscoveryState.RATE_LIMITED,
        DiscoveryState.PLATFORM_REJECTED,
        DiscoveryState.REQUEST_FAILED,
        DiscoveryState.RESPONSE_INVALID,
    }
)


@dataclass(frozen=True)
class IdentityProof:
    seed_url: str
    observed_name: str
    observed_public_biz: str
    observed_at: str


@dataclass(frozen=True)
class AccountConfig:
    name: str
    public_biz: str
    seed_urls: tuple[str, ...] = ()
    identity_proof: IdentityProof | None = None


@dataclass(frozen=True)
class DiscoveryConfig:
    version: int
    manual_backend_requests_enabled: bool
    refresh_interval: timedelta
    accounts: tuple[AccountConfig, ...]


@dataclass(frozen=True)
class DiscoveryArticle:
    account_name: str
    biz: str
    title: str
    url: str
    author: str
    published_at: datetime


@dataclass(frozen=True)
class AccountResult:
    account_name: str
    biz: str
    state: DiscoveryState


class AttemptKind(StrEnum):
    PROBE = "probe"


class IdentityResolutionState(StrEnum):
    RESERVED = "reserved"
    PROVISIONAL_MATCH = "provisional_match"
    LEGACY_NAME_AND_BIZ_MATCH = "legacy_name_and_biz_match"
    NO_MATCH = "no_match"
    AMBIGUOUS_MATCH = "ambiguous_match"
    AUTH_REQUIRED = "auth_required"
    RATE_LIMITED = "rate_limited"
    PLATFORM_REJECTED = "platform_rejected"
    REQUEST_FAILED = "request_failed"
    RESPONSE_INVALID = "response_invalid"


@dataclass(frozen=True)
class IdentityResolution:
    id: int
    started_at: datetime
    finished_at: datetime | None
    configured_account_name: str
    configured_public_biz: str
    state: IdentityResolutionState
    observed_account_name: str | None
    provisional_match_origin: str
    fakeid: str | None
    assigned_at: datetime | None
    assigned_probe_attempt_id: int | None
    invalidated_at: datetime | None
    invalidation_reason: str | None
    superseding_resolution_id: int | None
    platform_error_ret: int | None = None
    platform_error_ret_origin: str = "not_applicable"

@dataclass(frozen=True)
class BackendRequest:
    id: int
    kind: str
    started_at: datetime
    finished_at: datetime | None
    state: str
    account_name: str
    platform_error_ret: int | None = None
    platform_error_ret_origin: str = "not_applicable"


@dataclass(frozen=True)
class ProbeReservation:
    attempt_id: int
    identity_resolution_id: int
    fakeid: str


@dataclass(frozen=True)
class ProbeCompletion:
    attempt_id: int
    state: DiscoveryState
    target_identity_evidence: TargetIdentityEvidence
    returned_article_count: int
    new_candidate_count: int


class TargetIdentityEvidence(StrEnum):
    PENDING = "pending"
    NOT_OBSERVED = "not_observed"
    EMPTY_ARTICLE_LIST = "empty_article_list"
    ARTICLE_URL_PUBLIC_BIZ_UNAVAILABLE = "article_url_public_biz_unavailable"
    ARTICLE_URL_PUBLIC_BIZ_VERIFIED = "article_url_public_biz_verified"
    ARTICLE_URL_PUBLIC_BIZ_MISMATCH = "article_url_public_biz_mismatch"
    PREDATES_V7_VERIFICATION = "predates_v7_verification"


@dataclass(frozen=True)
class DiscoveryAttempt:
    started_at: datetime
    finished_at: datetime | None
    state: DiscoveryState
    kind: AttemptKind
    account_results: tuple[AccountResult, ...]
    candidate_snapshot: tuple[DiscoveryArticle, ...]
    requested_page_size: int | None
    requested_page_size_origin: str
    identity_resolution_id: int | None
    identity_resolution_origin: str
    target_identity_evidence: TargetIdentityEvidence
    platform_error_ret: int | None = None
    platform_error_ret_origin: str = "not_applicable"

    @property
    def returned_article_count(self) -> int:
        return len(self.candidate_snapshot)

    @property
    def candidate_urls(self) -> tuple[str, ...]:
        return tuple(article.url for article in self.candidate_snapshot)


@dataclass(frozen=True)
class EffectiveStatus:
    state: DiscoveryGateState
    account_count: int
    next_request_at: datetime | None = None
    latest_attempt: DiscoveryAttempt | None = None
    latest_request: BackendRequest | None = None
    resolved_ready_count: int = 0
    assigned_count: int = 0
    invalidated_count: int = 0
    unresolved_count: int = 0
    ready_accounts: tuple[tuple[str, int], ...] = ()
