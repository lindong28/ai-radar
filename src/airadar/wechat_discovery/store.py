from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .models import (
    FAILURE_STATES,
    AccountConfig,
    AccountResult,
    AttemptKind,
    BackendRequest,
    DiscoveryArticle,
    DiscoveryAttempt,
    DiscoveryConfig,
    DiscoveryState,
    IdentityResolution,
    IdentityResolutionState,
    ProbeCompletion,
    ProbeReservation,
    TargetIdentityEvidence,
)
from .protocol import (
    DiscoveryIdentityMismatch,
    DiscoveryIdentityNoMatch,
    DiscoveryIdentityUnverified,
    DiscoveryResponseInvalid,
    ProvisionalIdentity,
    normalized_account_name,
    observed_article_biz,
)
from .status import backend_request_blocked_until

DEFAULT_STATE_DB_PATH = Path(__file__).resolve().parents[3] / "data" / "wechat-discovery.db"
SCHEMA_VERSION = 10
_FAILURE_STATE_VALUES = tuple(state.value for state in FAILURE_STATES)
_LEGACY_FAILURE_STATE_VALUES = tuple(
    value for value in _FAILURE_STATE_VALUES if value != DiscoveryState.PLATFORM_REJECTED.value
)
_V6_PERSISTED_STATES = (
    DiscoveryState.RESERVED.value,
    *_LEGACY_FAILURE_STATE_VALUES,
    DiscoveryState.SUCCESS.value,
)
_V5_PERSISTED_STATES = (
    DiscoveryState.RESERVED.value,
    *_FAILURE_STATE_VALUES,
    DiscoveryState.SUCCESS_NO_NEW_SHADOW_CANDIDATES.value,
    DiscoveryState.SUCCESS_WITH_NEW_SHADOW_CANDIDATES.value,
)
_V6_STATE_SQL = ", ".join(f"'{state}'" for state in _V6_PERSISTED_STATES)
_V5_STATE_SQL = ", ".join(f"'{state}'" for state in _V5_PERSISTED_STATES)
_V6_IDENTITY_STATE_SQL = ", ".join(
    f"'{state}'"
    for state in (
        "reserved",
        "resolved",
        "no_match",
        "ambiguous_match",
        "auth_required",
        "rate_limited",
        "request_failed",
        "response_invalid",
    )
)
_V8_IDENTITY_STATE_SQL = ", ".join(
    f"'{state.value}'"
    for state in IdentityResolutionState
    if state is not IdentityResolutionState.PLATFORM_REJECTED
)
_IDENTITY_STATE_SQL = ", ".join(f"'{state.value}'" for state in IdentityResolutionState)
_TARGET_IDENTITY_EVIDENCE_SQL = ", ".join(
    f"'{evidence.value}'" for evidence in TargetIdentityEvidence
)

_SCHEMA_V6 = f"""
CREATE TABLE identity_resolution_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  configured_account_name TEXT NOT NULL,
  configured_public_biz TEXT NOT NULL,
  outcome TEXT NOT NULL CHECK (outcome IN ({_V6_IDENTITY_STATE_SQL})),
  verification_basis TEXT NOT NULL CHECK (
    verification_basis = 'normalized_account_name_and_public_biz'
  ),
  observed_account_name TEXT,
  public_biz_match_origin TEXT NOT NULL CHECK (
    public_biz_match_origin IN ('recorded', 'not_observed', 'predates_persistence')
  ),
  resolved_fakeid TEXT,
  invalidated_at TEXT,
  invalidation_reason TEXT,
  superseding_resolution_id INTEGER REFERENCES identity_resolution_attempts(id),
  CHECK ((outcome = 'reserved') = (finished_at IS NULL)),
  CHECK (
    (outcome = 'resolved'
      AND resolved_fakeid IS NOT NULL AND length(trim(resolved_fakeid)) > 0
      AND observed_account_name IS NOT NULL AND length(trim(observed_account_name)) > 0
      AND public_biz_match_origin IN ('recorded', 'predates_persistence'))
    OR
    (outcome != 'resolved'
      AND resolved_fakeid IS NULL
      AND observed_account_name IS NULL
      AND public_biz_match_origin = 'not_observed')
  ),
  CHECK ((invalidated_at IS NULL) = (invalidation_reason IS NULL)),
  CHECK (invalidation_reason IS NULL OR length(trim(invalidation_reason)) > 0),
  CHECK (invalidated_at IS NULL OR outcome = 'resolved'),
  CHECK (superseding_resolution_id IS NULL OR outcome = 'resolved'),
  CHECK (invalidated_at IS NULL OR superseding_resolution_id IS NULL)
);
CREATE TABLE discovery_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  outcome TEXT NOT NULL CHECK (outcome IN ({_V6_STATE_SQL})),
  legacy_target_account_name TEXT,
  legacy_target_public_biz TEXT,
  requested_page_size INTEGER CHECK (requested_page_size BETWEEN 1 AND 20),
  requested_page_size_origin TEXT NOT NULL CHECK (
    requested_page_size_origin IN ('recorded', 'predates_persistence')
  ),
  identity_resolution_id INTEGER REFERENCES identity_resolution_attempts(id),
  identity_resolution_origin TEXT NOT NULL CHECK (
    identity_resolution_origin IN ('verified_resolution', 'predates_resolution')
  ),
  CHECK ((outcome = 'reserved') = (finished_at IS NULL)),
  CHECK (
    (requested_page_size_origin = 'recorded' AND requested_page_size IS NOT NULL)
    OR
    (requested_page_size_origin = 'predates_persistence' AND requested_page_size IS NULL)
  ),
  CHECK (
    (identity_resolution_origin = 'verified_resolution'
      AND identity_resolution_id IS NOT NULL
      AND legacy_target_account_name IS NULL
      AND legacy_target_public_biz IS NULL)
    OR
    (identity_resolution_origin = 'predates_resolution'
      AND identity_resolution_id IS NULL
      AND legacy_target_account_name IS NOT NULL
      AND length(trim(legacy_target_account_name)) > 0
      AND legacy_target_public_biz IS NOT NULL
      AND length(trim(legacy_target_public_biz)) > 0)
  )
);
CREATE UNIQUE INDEX one_probe_per_identity_resolution
  ON discovery_attempts(identity_resolution_id)
  WHERE identity_resolution_id IS NOT NULL;
CREATE TABLE discovery_attempt_candidates (
  probe_attempt_id INTEGER NOT NULL REFERENCES discovery_attempts(id) ON DELETE CASCADE,
  url TEXT NOT NULL,
  title TEXT NOT NULL,
  author TEXT NOT NULL,
  published_at TEXT NOT NULL,
  PRIMARY KEY (probe_attempt_id, url)
);
PRAGMA user_version=6;
"""

_SCHEMA_V7 = f"""
CREATE TABLE identity_resolution_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  configured_account_name TEXT NOT NULL,
  configured_public_biz TEXT NOT NULL,
  outcome TEXT NOT NULL CHECK (outcome IN ({_V8_IDENTITY_STATE_SQL})),
  provisional_match_origin TEXT NOT NULL CHECK (
    provisional_match_origin IN (
      'not_established',
      'searchbiz_unique_normalized_name',
      'predates_unique_normalized_name_contract'
    )
  ),
  observed_account_name TEXT,
  provisional_fakeid TEXT,
  invalidated_at TEXT,
  invalidation_reason TEXT,
  superseding_resolution_id INTEGER REFERENCES identity_resolution_attempts(id),
  CHECK ((outcome = 'reserved') = (finished_at IS NULL)),
  CHECK (
    (outcome = 'provisional_match'
      AND provisional_match_origin = 'searchbiz_unique_normalized_name'
      AND observed_account_name IS NOT NULL
      AND length(trim(observed_account_name)) > 0
      AND provisional_fakeid IS NOT NULL
      AND length(trim(provisional_fakeid)) > 0)
    OR
    (outcome = 'legacy_name_and_biz_match'
      AND provisional_match_origin = 'predates_unique_normalized_name_contract'
      AND observed_account_name IS NOT NULL
      AND length(trim(observed_account_name)) > 0
      AND provisional_fakeid IS NOT NULL
      AND length(trim(provisional_fakeid)) > 0)
    OR
    (outcome NOT IN ('provisional_match', 'legacy_name_and_biz_match')
      AND provisional_match_origin = 'not_established'
      AND observed_account_name IS NULL
      AND provisional_fakeid IS NULL)
  ),
  CHECK ((invalidated_at IS NULL) = (invalidation_reason IS NULL)),
  CHECK (invalidation_reason IS NULL OR length(trim(invalidation_reason)) > 0),
  CHECK (
    invalidated_at IS NULL
    OR outcome IN ('provisional_match', 'legacy_name_and_biz_match')
  ),
  CHECK (
    superseding_resolution_id IS NULL
    OR outcome IN ('provisional_match', 'legacy_name_and_biz_match')
  ),
  CHECK (invalidated_at IS NULL OR superseding_resolution_id IS NULL)
);
CREATE TABLE discovery_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  outcome TEXT NOT NULL CHECK (outcome IN ({_V6_STATE_SQL})),
  legacy_target_account_name TEXT,
  legacy_target_public_biz TEXT,
  requested_page_size INTEGER CHECK (requested_page_size BETWEEN 1 AND 20),
  requested_page_size_origin TEXT NOT NULL CHECK (
    requested_page_size_origin IN ('recorded', 'predates_persistence')
  ),
  identity_resolution_id INTEGER REFERENCES identity_resolution_attempts(id),
  identity_resolution_origin TEXT NOT NULL CHECK (
    identity_resolution_origin IN (
      'provisional_searchbiz_match',
      'legacy_name_and_biz_match',
      'predates_resolution'
    )
  ),
  target_identity_evidence TEXT NOT NULL CHECK (
    target_identity_evidence IN ({_TARGET_IDENTITY_EVIDENCE_SQL})
  ),
  CHECK ((outcome = 'reserved') = (finished_at IS NULL)),
  CHECK (
    (requested_page_size_origin = 'recorded' AND requested_page_size IS NOT NULL)
    OR
    (requested_page_size_origin = 'predates_persistence' AND requested_page_size IS NULL)
  ),
  CHECK (
    (identity_resolution_origin IN (
        'provisional_searchbiz_match', 'legacy_name_and_biz_match'
      )
      AND identity_resolution_id IS NOT NULL
      AND legacy_target_account_name IS NULL
      AND legacy_target_public_biz IS NULL)
    OR
    (identity_resolution_origin = 'predates_resolution'
      AND identity_resolution_id IS NULL
      AND legacy_target_account_name IS NOT NULL
      AND length(trim(legacy_target_account_name)) > 0
      AND legacy_target_public_biz IS NOT NULL
      AND length(trim(legacy_target_public_biz)) > 0)
  ),
  CHECK (
    (outcome = 'reserved' AND target_identity_evidence = 'pending')
    OR
    (outcome = 'success' AND target_identity_evidence IN (
      'article_url_public_biz_verified', 'predates_v7_verification'
    ))
    OR
    (outcome = 'identity_unverified' AND target_identity_evidence IN (
      'empty_article_list', 'article_url_public_biz_unavailable',
      'predates_v7_verification'
    ))
    OR
    (outcome = 'identity_mismatch'
      AND target_identity_evidence IN (
        'article_url_public_biz_mismatch', 'predates_v7_verification'
      ))
    OR
    (outcome IN (
      'auth_required', 'rate_limited', 'request_failed', 'response_invalid'
    ) AND target_identity_evidence = 'not_observed')
  )
);
CREATE UNIQUE INDEX one_probe_per_identity_resolution
  ON discovery_attempts(identity_resolution_id)
  WHERE identity_resolution_id IS NOT NULL;
CREATE TABLE discovery_attempt_candidates (
  probe_attempt_id INTEGER NOT NULL REFERENCES discovery_attempts(id) ON DELETE CASCADE,
  url TEXT NOT NULL,
  title TEXT NOT NULL,
  author TEXT NOT NULL,
  published_at TEXT NOT NULL,
  PRIMARY KEY (probe_attempt_id, url)
);
CREATE TRIGGER validate_discovery_attempt_insert
BEFORE INSERT ON discovery_attempts
WHEN
  (NEW.identity_resolution_origin = 'provisional_searchbiz_match' AND NOT EXISTS (
    SELECT 1 FROM identity_resolution_attempts r
    WHERE r.id=NEW.identity_resolution_id
      AND r.outcome='provisional_match'
      AND r.provisional_match_origin='searchbiz_unique_normalized_name'
      AND r.invalidated_at IS NULL AND r.superseding_resolution_id IS NULL
  ))
  OR
  (NEW.identity_resolution_origin = 'legacy_name_and_biz_match' AND NOT EXISTS (
    SELECT 1 FROM identity_resolution_attempts r
    WHERE r.id=NEW.identity_resolution_id
      AND r.outcome='legacy_name_and_biz_match'
      AND r.provisional_match_origin='predates_unique_normalized_name_contract'
  ))
  OR
  (NEW.outcome='success'
    AND NEW.target_identity_evidence='article_url_public_biz_verified')
BEGIN
  SELECT RAISE(ABORT, 'invalid discovery attempt identity relation or evidence');
END;
CREATE TRIGGER validate_discovery_attempt_update
BEFORE UPDATE ON discovery_attempts
WHEN
  (NEW.identity_resolution_origin = 'provisional_searchbiz_match' AND NOT EXISTS (
    SELECT 1 FROM identity_resolution_attempts r
    WHERE r.id=NEW.identity_resolution_id
      AND r.outcome='provisional_match'
      AND r.provisional_match_origin='searchbiz_unique_normalized_name'
  ))
  OR
  (NEW.identity_resolution_origin = 'legacy_name_and_biz_match' AND NOT EXISTS (
    SELECT 1 FROM identity_resolution_attempts r
    WHERE r.id=NEW.identity_resolution_id
      AND r.outcome='legacy_name_and_biz_match'
      AND r.provisional_match_origin='predates_unique_normalized_name_contract'
  ))
  OR
  (NEW.outcome='success'
    AND NEW.target_identity_evidence='article_url_public_biz_verified'
    AND NOT EXISTS (
      SELECT 1 FROM discovery_attempt_candidates c WHERE c.probe_attempt_id=NEW.id
    ))
  OR
  (NEW.outcome NOT IN ('reserved', 'success') AND EXISTS (
    SELECT 1 FROM discovery_attempt_candidates c WHERE c.probe_attempt_id=NEW.id
  ))
BEGIN
  SELECT RAISE(ABORT, 'invalid discovery attempt completion relation');
END;
CREATE TRIGGER validate_discovery_candidate_insert
BEFORE INSERT ON discovery_attempt_candidates
WHEN NOT EXISTS (
  SELECT 1 FROM discovery_attempts a
  WHERE a.id=NEW.probe_attempt_id
    AND (
      (a.outcome='reserved' AND a.target_identity_evidence='pending')
      OR
      (a.outcome='success' AND a.target_identity_evidence IN (
        'article_url_public_biz_verified', 'predates_v7_verification'
      ))
    )
)
BEGIN
  SELECT RAISE(ABORT, 'candidate requires a pending or verified successful probe');
END;
CREATE TRIGGER validate_discovery_candidate_delete
BEFORE DELETE ON discovery_attempt_candidates
WHEN EXISTS (
  SELECT 1 FROM discovery_attempts a
  WHERE a.id=OLD.probe_attempt_id
    AND a.outcome='success'
    AND a.target_identity_evidence='article_url_public_biz_verified'
    AND (SELECT COUNT(*) FROM discovery_attempt_candidates c
         WHERE c.probe_attempt_id=OLD.probe_attempt_id) <= 1
)
BEGIN
  SELECT RAISE(ABORT, 'verified successful probe requires a stored candidate');
END;
PRAGMA user_version=7;
"""

_V7_TRIGGER_START = _SCHEMA_V7.index("CREATE TRIGGER validate_discovery_attempt_insert")
_V7_PRAGMA_START = _SCHEMA_V7.index("PRAGMA user_version=7;")
_SCHEMA_V8_TABLES = _SCHEMA_V7[:_V7_TRIGGER_START]
_V7_EVIDENCE_CHECK = """  CHECK (
    (outcome = 'reserved' AND target_identity_evidence = 'pending')
    OR
    (outcome = 'success' AND target_identity_evidence IN (
      'article_url_public_biz_verified', 'predates_v7_verification'
    ))
    OR
    (outcome = 'identity_unverified' AND target_identity_evidence IN (
      'empty_article_list', 'article_url_public_biz_unavailable',
      'predates_v7_verification'
    ))
    OR
    (outcome = 'identity_mismatch'
      AND target_identity_evidence IN (
        'article_url_public_biz_mismatch', 'predates_v7_verification'
      ))
    OR
    (outcome IN (
      'auth_required', 'rate_limited', 'request_failed', 'response_invalid'
    ) AND target_identity_evidence = 'not_observed')
  )
"""
if _SCHEMA_V8_TABLES.count(_V7_EVIDENCE_CHECK) != 1:
    raise RuntimeError("v7 discovery evidence check is not uniquely replaceable")
_SCHEMA_V8_TABLES = _SCHEMA_V8_TABLES.replace(
    _V7_EVIDENCE_CHECK,
    _V7_EVIDENCE_CHECK
    + """  ,CHECK (
    target_identity_evidence != 'predates_v7_verification'
    OR identity_resolution_origin IN (
      'legacy_name_and_biz_match', 'predates_resolution'
    )
  )
""",
)

_SCHEMA_V8_TRIGGERS = """CREATE TRIGGER validate_discovery_attempt_insert
BEFORE INSERT ON discovery_attempts
WHEN
  (NEW.identity_resolution_origin = 'provisional_searchbiz_match' AND NOT EXISTS (
    SELECT 1 FROM identity_resolution_attempts r
    WHERE r.id=NEW.identity_resolution_id
      AND r.outcome='provisional_match'
      AND r.provisional_match_origin='searchbiz_unique_normalized_name'
      AND r.invalidated_at IS NULL AND r.superseding_resolution_id IS NULL
  ))
  OR
  (NEW.identity_resolution_origin = 'legacy_name_and_biz_match' AND NOT EXISTS (
    SELECT 1 FROM identity_resolution_attempts r
    WHERE r.id=NEW.identity_resolution_id
      AND r.outcome='legacy_name_and_biz_match'
      AND r.provisional_match_origin='predates_unique_normalized_name_contract'
  ))
  OR
  (NEW.outcome='success'
    AND NEW.target_identity_evidence='article_url_public_biz_verified')
BEGIN
  SELECT RAISE(ABORT, 'invalid discovery attempt identity relation or evidence');
END;
CREATE TRIGGER validate_discovery_attempt_update
BEFORE UPDATE ON discovery_attempts
WHEN
  (NEW.identity_resolution_origin = 'provisional_searchbiz_match' AND NOT EXISTS (
    SELECT 1 FROM identity_resolution_attempts r
    WHERE r.id=NEW.identity_resolution_id
      AND r.outcome='provisional_match'
      AND r.provisional_match_origin='searchbiz_unique_normalized_name'
      AND r.invalidated_at IS NULL AND r.superseding_resolution_id IS NULL
  ))
  OR
  (NEW.identity_resolution_origin = 'legacy_name_and_biz_match' AND NOT EXISTS (
    SELECT 1 FROM identity_resolution_attempts r
    WHERE r.id=NEW.identity_resolution_id
      AND r.outcome='legacy_name_and_biz_match'
      AND r.provisional_match_origin='predates_unique_normalized_name_contract'
  ))
  OR
  (NEW.outcome='success'
    AND NEW.target_identity_evidence='article_url_public_biz_verified'
    AND NOT EXISTS (
      SELECT 1 FROM discovery_attempt_candidates c WHERE c.probe_attempt_id=NEW.id
    ))
  OR
  (NEW.outcome NOT IN ('reserved', 'success') AND EXISTS (
    SELECT 1 FROM discovery_attempt_candidates c WHERE c.probe_attempt_id=NEW.id
  ))
BEGIN
  SELECT RAISE(ABORT, 'invalid discovery attempt completion relation');
END;
CREATE TRIGGER validate_discovery_candidate_insert
BEFORE INSERT ON discovery_attempt_candidates
WHEN NOT EXISTS (
  SELECT 1 FROM discovery_attempts a
  WHERE a.id=NEW.probe_attempt_id
    AND a.outcome='reserved' AND a.target_identity_evidence='pending'
)
BEGIN
  SELECT RAISE(ABORT, 'candidate requires a pending probe');
END;
CREATE TRIGGER validate_discovery_candidate_update
BEFORE UPDATE ON discovery_attempt_candidates
WHEN
  EXISTS (
    SELECT 1 FROM discovery_attempts a
    WHERE a.id=OLD.probe_attempt_id AND a.outcome!='reserved'
  )
  OR NOT EXISTS (
    SELECT 1 FROM discovery_attempts a
    WHERE a.id=NEW.probe_attempt_id
      AND a.outcome='reserved' AND a.target_identity_evidence='pending'
  )
BEGIN
  SELECT RAISE(ABORT, 'completed probe candidate snapshot is immutable');
END;
CREATE TRIGGER validate_discovery_candidate_delete
BEFORE DELETE ON discovery_attempt_candidates
WHEN EXISTS (
  SELECT 1 FROM discovery_attempts a
  WHERE a.id=OLD.probe_attempt_id AND a.outcome!='reserved'
)
BEGIN
  SELECT RAISE(ABORT, 'completed probe candidate snapshot is immutable');
END;
"""

_SCHEMA_V8 = (
    _SCHEMA_V8_TABLES
    + _SCHEMA_V8_TRIGGERS
    + "PRAGMA user_version=8;\n"
)

_SCHEMA_V9_TABLES = _SCHEMA_V8_TABLES
_V8_IDENTITY_OUTCOME_COLUMN = (
    f"  outcome TEXT NOT NULL CHECK (outcome IN ({_V8_IDENTITY_STATE_SQL})),\n"
)
_V9_IDENTITY_OUTCOME_COLUMNS = (
    f"  outcome TEXT NOT NULL CHECK (outcome IN ({_IDENTITY_STATE_SQL})),\n"
    "  platform_error_ret INTEGER,\n"
    "  platform_error_ret_origin TEXT NOT NULL CHECK (\n"
    "    platform_error_ret_origin IN (\n"
    "      'recorded', 'not_applicable', 'predates_persistence'\n"
    "    )\n"
    "  ),\n"
)
if _SCHEMA_V9_TABLES.count(_V8_IDENTITY_OUTCOME_COLUMN) != 1:
    raise RuntimeError("v8 identity outcome column is not uniquely replaceable")
_SCHEMA_V9_TABLES = _SCHEMA_V9_TABLES.replace(
    _V8_IDENTITY_OUTCOME_COLUMN,
    _V9_IDENTITY_OUTCOME_COLUMNS,
)

_V8_DISCOVERY_OUTCOME_COLUMN = (
    f"  outcome TEXT NOT NULL CHECK (outcome IN ({_V6_STATE_SQL})),\n"
)
_V9_STATE_SQL = ", ".join(
    f"'{state}'"
    for state in (
        DiscoveryState.RESERVED.value,
        *_FAILURE_STATE_VALUES,
        DiscoveryState.SUCCESS.value,
    )
)
_V9_DISCOVERY_OUTCOME_COLUMNS = (
    f"  outcome TEXT NOT NULL CHECK (outcome IN ({_V9_STATE_SQL})),\n"
    "  platform_error_ret INTEGER,\n"
    "  platform_error_ret_origin TEXT NOT NULL CHECK (\n"
    "    platform_error_ret_origin IN (\n"
    "      'recorded', 'not_applicable', 'predates_persistence'\n"
    "    )\n"
    "  ),\n"
)
if _SCHEMA_V9_TABLES.count(_V8_DISCOVERY_OUTCOME_COLUMN) != 1:
    raise RuntimeError("v8 discovery outcome column is not uniquely replaceable")
_SCHEMA_V9_TABLES = _SCHEMA_V9_TABLES.replace(
    _V8_DISCOVERY_OUTCOME_COLUMN,
    _V9_DISCOVERY_OUTCOME_COLUMNS,
)

_IDENTITY_PLATFORM_RET_CHECK_ANCHOR = """  CHECK (invalidated_at IS NULL OR superseding_resolution_id IS NULL)
);
CREATE TABLE discovery_attempts (
"""
_IDENTITY_PLATFORM_RET_CHECK = """  CHECK (invalidated_at IS NULL OR superseding_resolution_id IS NULL),
  CHECK (
    (platform_error_ret_origin = 'recorded'
      AND platform_error_ret IS NOT NULL AND platform_error_ret != 0
      AND outcome IN ('auth_required', 'rate_limited', 'platform_rejected'))
    OR
    (platform_error_ret_origin = 'predates_persistence'
      AND platform_error_ret IS NULL
      AND outcome IN ('auth_required', 'rate_limited', 'response_invalid'))
    OR
    (platform_error_ret_origin = 'not_applicable'
      AND platform_error_ret IS NULL
      AND outcome NOT IN ('auth_required', 'rate_limited', 'platform_rejected'))
  )
);
CREATE TABLE discovery_attempts (
"""
if _SCHEMA_V9_TABLES.count(_IDENTITY_PLATFORM_RET_CHECK_ANCHOR) != 1:
    raise RuntimeError("v8 identity platform-ret check anchor is not unique")
_SCHEMA_V9_TABLES = _SCHEMA_V9_TABLES.replace(
    _IDENTITY_PLATFORM_RET_CHECK_ANCHOR,
    _IDENTITY_PLATFORM_RET_CHECK,
)

_V9_EVIDENCE_CHECK = _V7_EVIDENCE_CHECK.replace(
    "'auth_required', 'rate_limited', 'request_failed', 'response_invalid'",
    "'auth_required', 'rate_limited', 'platform_rejected', "
    "'request_failed', 'response_invalid'",
)
if _SCHEMA_V9_TABLES.count(_V7_EVIDENCE_CHECK) != 1:
    raise RuntimeError("v8 discovery evidence check is not uniquely replaceable for v9")
_SCHEMA_V9_TABLES = _SCHEMA_V9_TABLES.replace(
    _V7_EVIDENCE_CHECK,
    _V9_EVIDENCE_CHECK,
)

_DISCOVERY_PLATFORM_RET_CHECK_ANCHOR = """  ,CHECK (
    target_identity_evidence != 'predates_v7_verification'
    OR identity_resolution_origin IN (
      'legacy_name_and_biz_match', 'predates_resolution'
    )
  )
);
CREATE UNIQUE INDEX one_probe_per_identity_resolution
"""
_DISCOVERY_PLATFORM_RET_CHECK = """  ,CHECK (
    target_identity_evidence != 'predates_v7_verification'
    OR identity_resolution_origin IN (
      'legacy_name_and_biz_match', 'predates_resolution'
    )
  ),
  CHECK (
    (platform_error_ret_origin = 'recorded'
      AND platform_error_ret IS NOT NULL AND platform_error_ret != 0
      AND outcome IN ('auth_required', 'rate_limited', 'platform_rejected'))
    OR
    (platform_error_ret_origin = 'predates_persistence'
      AND platform_error_ret IS NULL
      AND outcome IN ('auth_required', 'rate_limited', 'response_invalid'))
    OR
    (platform_error_ret_origin = 'not_applicable'
      AND platform_error_ret IS NULL
      AND outcome NOT IN ('auth_required', 'rate_limited', 'platform_rejected'))
  )
);
CREATE UNIQUE INDEX one_probe_per_identity_resolution
"""
if _SCHEMA_V9_TABLES.count(_DISCOVERY_PLATFORM_RET_CHECK_ANCHOR) != 1:
    raise RuntimeError("v8 discovery platform-ret check anchor is not unique")
_SCHEMA_V9_TABLES = _SCHEMA_V9_TABLES.replace(
    _DISCOVERY_PLATFORM_RET_CHECK_ANCHOR,
    _DISCOVERY_PLATFORM_RET_CHECK,
)

_SCHEMA_V9_TRIGGERS = _SCHEMA_V8_TRIGGERS + """
CREATE TRIGGER reject_historical_resolution_platform_ret_insert
BEFORE INSERT ON identity_resolution_attempts
WHEN NEW.platform_error_ret_origin='predates_persistence'
BEGIN
  SELECT RAISE(ABORT, 'historical platform ret origin is migration-only');
END;
CREATE TRIGGER reject_historical_probe_platform_ret_insert
BEFORE INSERT ON discovery_attempts
WHEN NEW.platform_error_ret_origin='predates_persistence'
BEGIN
  SELECT RAISE(ABORT, 'historical platform ret origin is migration-only');
END;
"""

_SCHEMA_V10_PLATFORM_RET_TRIGGERS = """
CREATE TRIGGER require_integer_resolution_platform_ret_insert
BEFORE INSERT ON identity_resolution_attempts
WHEN NEW.platform_error_ret IS NOT NULL
 AND typeof(NEW.platform_error_ret) != 'integer'
BEGIN
  SELECT RAISE(ABORT, 'platform error ret must be an integer');
END;
CREATE TRIGGER require_integer_resolution_platform_ret_update
BEFORE UPDATE ON identity_resolution_attempts
WHEN NEW.platform_error_ret IS NOT NULL
 AND typeof(NEW.platform_error_ret) != 'integer'
BEGIN
  SELECT RAISE(ABORT, 'platform error ret must be an integer');
END;
CREATE TRIGGER require_integer_probe_platform_ret_insert
BEFORE INSERT ON discovery_attempts
WHEN NEW.platform_error_ret IS NOT NULL
 AND typeof(NEW.platform_error_ret) != 'integer'
BEGIN
  SELECT RAISE(ABORT, 'platform error ret must be an integer');
END;
CREATE TRIGGER require_integer_probe_platform_ret_update
BEFORE UPDATE ON discovery_attempts
WHEN NEW.platform_error_ret IS NOT NULL
 AND typeof(NEW.platform_error_ret) != 'integer'
BEGIN
  SELECT RAISE(ABORT, 'platform error ret must be an integer');
END;
"""

_SCHEMA_V10_TRIGGERS = _SCHEMA_V9_TRIGGERS + _SCHEMA_V10_PLATFORM_RET_TRIGGERS

_SCHEMA_V9 = _SCHEMA_V9_TABLES + _SCHEMA_V9_TRIGGERS + "PRAGMA user_version=9;\n"
_SCHEMA = (
    _SCHEMA_V9_TABLES
    + _SCHEMA_V10_TRIGGERS
    + f"PRAGMA user_version={SCHEMA_VERSION};\n"
)


class DiscoveryStoreVersionError(RuntimeError):
    pass


class DiscoveryCooldownActive(RuntimeError):
    def __init__(self, next_request_at: datetime) -> None:
        super().__init__("WeChat discovery backend request is still in cooldown")
        self.next_request_at = next_request_at


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("WeChat discovery timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def _datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("persisted WeChat discovery timestamp has no timezone")
    return parsed.astimezone(UTC)


_PLATFORM_ERROR_OUTCOMES = frozenset(
    {
        DiscoveryState.AUTH_REQUIRED.value,
        DiscoveryState.RATE_LIMITED.value,
        DiscoveryState.PLATFORM_REJECTED.value,
    }
)


def _platform_error_fields(
    outcome: DiscoveryState | IdentityResolutionState,
    platform_error_ret: int | None,
) -> tuple[int | None, str]:
    requires_ret = outcome.value in _PLATFORM_ERROR_OUTCOMES
    valid_ret = (
        isinstance(platform_error_ret, int)
        and not isinstance(platform_error_ret, bool)
        and platform_error_ret != 0
    )
    if requires_ret != valid_ret:
        raise ValueError("platform error outcome and exact ret must be recorded together")
    return (
        (platform_error_ret, "recorded")
        if valid_ret
        else (None, "not_applicable")
    )


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


class DiscoveryStore:
    def __init__(self, path: str | Path = DEFAULT_STATE_DB_PATH) -> None:
        self.path = Path(path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if version != SCHEMA_VERSION:
                conn.execute("PRAGMA foreign_keys=OFF")
                conn.execute("BEGIN IMMEDIATE")
                try:
                    version = int(conn.execute("PRAGMA user_version").fetchone()[0])
                    tables = _tables(conn)
                    has_discovery = any(name.startswith("discovery_") for name in tables)
                    if version == 0 and not has_discovery:
                        self._initialize_schema(conn)
                    elif version in {1, 2, 3, 4} and has_discovery:
                        self._migrate_to_v5(conn, version=version)
                        self._migrate_v5_to_v6(conn)
                        self._migrate_v6_to_v7(conn)
                        self._migrate_v7_to_v8(conn)
                        self._migrate_v8_to_v9(conn)
                        self._migrate_v9_to_v10(conn)
                    elif version == 5 and has_discovery:
                        self._migrate_v5_to_v6(conn)
                        self._migrate_v6_to_v7(conn)
                        self._migrate_v7_to_v8(conn)
                        self._migrate_v8_to_v9(conn)
                        self._migrate_v9_to_v10(conn)
                    elif version == 6 and has_discovery:
                        self._migrate_v6_to_v7(conn)
                        self._migrate_v7_to_v8(conn)
                        self._migrate_v8_to_v9(conn)
                        self._migrate_v9_to_v10(conn)
                    elif version == 7 and has_discovery:
                        self._migrate_v7_to_v8(conn)
                        self._migrate_v8_to_v9(conn)
                        self._migrate_v9_to_v10(conn)
                    elif version == 8 and has_discovery:
                        self._migrate_v8_to_v9(conn)
                        self._migrate_v9_to_v10(conn)
                    elif version == 9 and has_discovery:
                        self._migrate_v9_to_v10(conn)
                    elif version != SCHEMA_VERSION:
                        raise DiscoveryStoreVersionError(
                            f"unsupported WeChat discovery state schema version: {version}"
                        )
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
            conn.execute("PRAGMA foreign_keys=ON")
            violation = conn.execute("PRAGMA foreign_key_check").fetchone()
            if violation is not None:
                raise DiscoveryStoreVersionError(
                    "WeChat discovery state database has a foreign-key violation"
                )
            yield conn
        finally:
            conn.close()

    @contextmanager
    def readonly_connect(self) -> Iterator[sqlite3.Connection]:
        if not self.path.exists():
            raise DiscoveryStoreVersionError(
                "WeChat discovery state database does not exist"
            )
        conn = sqlite3.connect(
            f"{self.path.resolve().as_uri()}?mode=ro",
            timeout=30,
            uri=True,
        )
        conn.row_factory = sqlite3.Row
        try:
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if version != SCHEMA_VERSION:
                if version not in range(SCHEMA_VERSION):
                    raise DiscoveryStoreVersionError(
                        "unsupported WeChat discovery state schema version: "
                        f"{version}"
                    )
                raise DiscoveryStoreVersionError(
                    "WeChat discovery state schema requires explicit migration "
                    f"from v{version} to v{SCHEMA_VERSION}"
                )
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA query_only=ON")
            violation = conn.execute("PRAGMA foreign_key_check").fetchone()
            if violation is not None:
                raise DiscoveryStoreVersionError(
                    "WeChat discovery state database has a foreign-key violation"
                )
            yield conn
        finally:
            conn.close()

    def migrate(self) -> tuple[int, int]:
        before = 0
        if self.path.exists():
            with sqlite3.connect(
                f"{self.path.resolve().as_uri()}?mode=ro", uri=True
            ) as existing:
                before = int(existing.execute("PRAGMA user_version").fetchone()[0])
        with self.connect():
            pass
        return before, SCHEMA_VERSION

    @staticmethod
    def _initialize_schema(
        conn: sqlite3.Connection, *, schema: str = _SCHEMA
    ) -> None:
        statement = ""
        for line in schema.splitlines():
            statement += f"{line}\n"
            if sqlite3.complete_statement(statement):
                conn.execute(statement)
                statement = ""
        if statement.strip():
            raise DiscoveryStoreVersionError("incomplete WeChat discovery schema definition")

    @staticmethod
    def _migrate_to_v5(conn: sqlite3.Connection, *, version: int) -> None:
        tables = _tables(conn)
        attempt_columns = _columns(conn, "discovery_attempts")
        has_v4_resolution = "identity_resolution_attempts" in tables
        result_table = "discovery_account_results" in tables

        if has_v4_resolution:
            duplicate = conn.execute(
                "SELECT identity_resolution_id FROM discovery_attempts "
                "WHERE identity_resolution_id IS NOT NULL "
                "GROUP BY identity_resolution_id HAVING COUNT(*) > 1 LIMIT 1"
            ).fetchone()
            if duplicate is not None:
                raise DiscoveryStoreVersionError(
                    "v4 identity resolution was assigned to multiple probe attempts"
                )
            relation_rows = conn.execute(
                """
                SELECT a.id AS attempt_id, a.identity_resolution_id,
                       r.id AS resolution_id, r.consuming_probe_attempt_id,
                       r.state AS resolution_state, r.account_name, r.biz,
                       ar.account_name AS target_name, ar.biz AS target_biz
                FROM discovery_attempts a
                LEFT JOIN identity_resolution_attempts r
                  ON r.id=a.identity_resolution_id
                LEFT JOIN discovery_account_results ar ON ar.attempt_id=a.id
                WHERE a.identity_resolution_id IS NOT NULL
                """
            ).fetchall()
            for row in relation_rows:
                if (
                    row["resolution_id"] is None
                    or row["consuming_probe_attempt_id"] != row["attempt_id"]
                    or row["resolution_state"] != "resolved"
                    or row["account_name"] != row["target_name"]
                    or row["biz"] != row["target_biz"]
                ):
                    raise DiscoveryStoreVersionError(
                        "v4 identity-resolution consumption relationship is contradictory"
                    )
            orphan_consumer = conn.execute(
                """
                SELECT r.id FROM identity_resolution_attempts r
                LEFT JOIN discovery_attempts a
                  ON a.id=r.consuming_probe_attempt_id
                 AND a.identity_resolution_id=r.id
                WHERE r.consuming_probe_attempt_id IS NOT NULL AND a.id IS NULL
                LIMIT 1
                """
            ).fetchone()
            if orphan_consumer is not None:
                raise DiscoveryStoreVersionError(
                    "v4 identity resolution points to a contradictory probe consumer"
                )

        conn.execute(
            f"""
            CREATE TABLE identity_resolution_attempts_v5 (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              started_at TEXT NOT NULL,
              finished_at TEXT,
              configured_account_name TEXT NOT NULL,
              configured_public_biz TEXT NOT NULL,
              outcome TEXT NOT NULL CHECK (outcome IN ({_V6_IDENTITY_STATE_SQL})),
              verification_basis TEXT NOT NULL CHECK (
                verification_basis = 'normalized_account_name_and_public_biz'
              ),
              observed_account_name TEXT,
              observed_public_biz TEXT,
              observed_public_biz_origin TEXT NOT NULL CHECK (
                observed_public_biz_origin IN ('recorded', 'not_observed', 'predates_persistence')
              ),
              resolved_fakeid TEXT,
              invalidated_at TEXT,
              invalidation_reason TEXT,
              superseding_resolution_id INTEGER REFERENCES identity_resolution_attempts_v5(id),
              CHECK ((outcome = 'reserved') = (finished_at IS NULL)),
              CHECK (
                (outcome = 'resolved'
                  AND resolved_fakeid IS NOT NULL AND length(trim(resolved_fakeid)) > 0
                  AND observed_account_name IS NOT NULL
                  AND length(trim(observed_account_name)) > 0
                  AND observed_public_biz IS NOT NULL
                  AND length(trim(observed_public_biz)) > 0
                  AND observed_public_biz = configured_public_biz
                  AND observed_public_biz_origin IN ('recorded', 'predates_persistence'))
                OR
                (outcome != 'resolved' AND resolved_fakeid IS NULL
                  AND observed_account_name IS NULL AND observed_public_biz IS NULL
                  AND observed_public_biz_origin = 'not_observed')
              ),
              CHECK ((invalidated_at IS NULL) = (invalidation_reason IS NULL)),
              CHECK (invalidation_reason IS NULL OR length(trim(invalidation_reason)) > 0),
              CHECK (invalidated_at IS NULL OR outcome = 'resolved'),
              CHECK (superseding_resolution_id IS NULL OR outcome = 'resolved'),
              CHECK (invalidated_at IS NULL OR superseding_resolution_id IS NULL)
            )
            """
        )
        if has_v4_resolution:
            rows = conn.execute("SELECT * FROM identity_resolution_attempts ORDER BY id").fetchall()
            for row in rows:
                is_reserved = row["state"] == IdentityResolutionState.RESERVED.value
                is_resolved = row["state"] == "resolved"
                invalidated_at = row["invalidated_at"]
                invalidation_reason = row["invalidation_reason"]
                observed_public_biz = None
                observed_origin = "not_observed"
                if is_resolved:
                    observed_public_biz = str(row["biz"])
                    observed_origin = "predates_persistence"
                    if invalidated_at is None and row["superseding_resolution_id"] is None:
                        invalidated_at = row["finished_at"]
                        invalidation_reason = "predates_observed_public_biz"
                conn.execute(
                    """
                    INSERT INTO identity_resolution_attempts_v5(
                      id, started_at, finished_at, configured_account_name,
                      configured_public_biz, outcome, verification_basis,
                      observed_account_name, observed_public_biz,
                      observed_public_biz_origin, resolved_fakeid,
                      invalidated_at, invalidation_reason, superseding_resolution_id
                    ) VALUES (?, ?, ?, ?, ?, ?,
                      'normalized_account_name_and_public_biz', ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["id"],
                        row["started_at"],
                        None if is_reserved else row["finished_at"],
                        row["account_name"],
                        row["biz"],
                        row["state"],
                        row["observed_account_name"],
                        observed_public_biz,
                        observed_origin,
                        row["resolved_fakeid"],
                        invalidated_at,
                        invalidation_reason,
                        row["superseding_resolution_id"],
                    ),
                )

        conn.execute(
            f"""
            CREATE TABLE discovery_attempts_v5 (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              started_at TEXT NOT NULL,
              finished_at TEXT,
              kind TEXT NOT NULL CHECK (kind = 'probe'),
              outcome TEXT NOT NULL CHECK (outcome IN ({_V5_STATE_SQL})),
              target_account_name TEXT NOT NULL,
              target_public_biz TEXT NOT NULL,
              change_basis TEXT NOT NULL CHECK (change_basis = 'shadow_state_url_set'),
              requested_page_size INTEGER CHECK (requested_page_size BETWEEN 1 AND 20),
              requested_page_size_origin TEXT NOT NULL CHECK (
                requested_page_size_origin IN ('recorded', 'predates_persistence')
              ),
              identity_resolution_id INTEGER REFERENCES identity_resolution_attempts_v5(id),
              identity_resolution_origin TEXT NOT NULL CHECK (
                identity_resolution_origin IN ('verified_resolution', 'predates_resolution')
              ),
              CHECK ((outcome = 'reserved') = (finished_at IS NULL)),
              CHECK (
                (requested_page_size_origin = 'recorded' AND requested_page_size IS NOT NULL)
                OR (requested_page_size_origin = 'predates_persistence' AND requested_page_size IS NULL)
              ),
              CHECK (
                (identity_resolution_origin = 'verified_resolution' AND identity_resolution_id IS NOT NULL)
                OR (identity_resolution_origin = 'predates_resolution' AND identity_resolution_id IS NULL)
              )
            )
            """
        )
        attempt_rows = conn.execute("SELECT * FROM discovery_attempts ORDER BY id").fetchall()
        for row in attempt_rows:
            if not result_table:
                raise DiscoveryStoreVersionError(
                    "legacy probe attempt has no target-account ledger"
                )
            targets = conn.execute(
                "SELECT account_name, biz FROM discovery_account_results WHERE attempt_id=?",
                (row["id"],),
            ).fetchall()
            if len(targets) != 1:
                raise DiscoveryStoreVersionError(
                    "legacy probe attempt does not have exactly one target account"
                )
            target = targets[0]
            page_size = None
            page_origin = "predates_persistence"
            if "requested_page_size" in attempt_columns:
                page_size = row["requested_page_size"]
            elif "requested_count" in attempt_columns:
                page_size = row["requested_count"]
            if page_size is not None:
                page_origin = (
                    row["requested_page_size_origin"]
                    if "requested_page_size_origin" in attempt_columns
                    else "recorded"
                )
            resolution_id = (
                row["identity_resolution_id"]
                if "identity_resolution_id" in attempt_columns
                else None
            )
            resolution_origin = (
                row["identity_resolution_origin"]
                if "identity_resolution_origin" in attempt_columns
                else "predates_resolution"
            )
            outcome_column = "state" if "state" in attempt_columns else "outcome"
            outcome = row[outcome_column]
            conn.execute(
                """
                INSERT INTO discovery_attempts_v5(
                  id, started_at, finished_at, kind, outcome,
                  target_account_name, target_public_biz, change_basis,
                  requested_page_size, requested_page_size_origin,
                  identity_resolution_id, identity_resolution_origin
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    row["started_at"],
                    None if outcome == DiscoveryState.RESERVED.value else row["finished_at"],
                    row["kind"],
                    outcome,
                    target["account_name"],
                    target["biz"],
                    row["change_basis"],
                    page_size,
                    page_origin,
                    resolution_id,
                    resolution_origin,
                ),
            )

        conn.execute(
            """
            CREATE TABLE discovery_attempt_candidates_v5 (
              attempt_id INTEGER NOT NULL REFERENCES discovery_attempts_v5(id) ON DELETE CASCADE,
              observed_public_biz TEXT NOT NULL,
              url TEXT NOT NULL,
              title TEXT NOT NULL,
              author TEXT NOT NULL,
              published_at TEXT NOT NULL,
              PRIMARY KEY (attempt_id, url)
            )
            """
        )
        if "discovery_attempt_candidates" in tables:
            candidate_columns = _columns(conn, "discovery_attempt_candidates")
            biz_column = "observed_public_biz" if "observed_public_biz" in candidate_columns else "biz"
            conn.execute(
                "INSERT INTO discovery_attempt_candidates_v5("
                "attempt_id, observed_public_biz, url, title, author, published_at) "
                f"SELECT attempt_id, {biz_column}, url, title, author, published_at "
                "FROM discovery_attempt_candidates"
            )

        for table in (
            "discovery_attempt_candidates",
            "discovery_candidates",
            "discovery_account_results",
            "discovery_attempts",
            "identity_resolution_attempts",
        ):
            if table in tables:
                conn.execute(f"DROP TABLE {table}")
        conn.execute(
            "ALTER TABLE identity_resolution_attempts_v5 RENAME TO identity_resolution_attempts"
        )
        conn.execute("ALTER TABLE discovery_attempts_v5 RENAME TO discovery_attempts")
        conn.execute(
            "ALTER TABLE discovery_attempt_candidates_v5 RENAME TO discovery_attempt_candidates"
        )
        conn.execute(
            "CREATE UNIQUE INDEX one_probe_per_identity_resolution "
            "ON discovery_attempts(identity_resolution_id) "
            "WHERE identity_resolution_id IS NOT NULL"
        )
        conn.execute("PRAGMA user_version=5")
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise DiscoveryStoreVersionError("v5 migration created a foreign-key violation")

    @staticmethod
    def _migrate_v5_to_v6(conn: sqlite3.Connection) -> None:
        resolution_rows = conn.execute(
            "SELECT * FROM identity_resolution_attempts ORDER BY id"
        ).fetchall()
        attempt_rows = conn.execute("SELECT * FROM discovery_attempts ORDER BY id").fetchall()
        candidate_rows = conn.execute(
            "SELECT * FROM discovery_attempt_candidates ORDER BY attempt_id, rowid"
        ).fetchall()

        for row in resolution_rows:
            try:
                started_at = _datetime(str(row["started_at"]))
                finished_at = None
                if row["finished_at"] is not None:
                    finished_at = _datetime(str(row["finished_at"]))
                    if finished_at < started_at:
                        raise DiscoveryStoreVersionError(
                            "v5 identity resolution completion precedes its reservation"
                        )
                if row["invalidated_at"] is not None:
                    invalidated_at = _datetime(str(row["invalidated_at"]))
                    if finished_at is None or invalidated_at < finished_at:
                        raise DiscoveryStoreVersionError(
                            "v5 identity invalidation precedes its resolved outcome"
                        )
            except (TypeError, ValueError) as exc:
                raise DiscoveryStoreVersionError(
                    "v5 identity resolution contains a non-canonical timestamp"
                ) from exc
            if row["outcome"] == "resolved":
                required = (
                    row["resolved_fakeid"],
                    row["observed_account_name"],
                    row["observed_public_biz"],
                )
                if any(value is None or not str(value).strip() for value in required):
                    raise DiscoveryStoreVersionError(
                        "v5 resolved identity is missing recoverable identity evidence"
                    )
                if row["observed_public_biz"] != row["configured_public_biz"]:
                    raise DiscoveryStoreVersionError(
                        "v5 resolved identity contradicts its configured public biz"
                    )
                if normalized_account_name(str(row["observed_account_name"])) != (
                    normalized_account_name(str(row["configured_account_name"]))
                ):
                    raise DiscoveryStoreVersionError(
                        "v5 resolved identity contradicts its configured account name"
                    )
            if row["invalidation_reason"] is not None and not str(
                row["invalidation_reason"]
            ).strip():
                raise DiscoveryStoreVersionError(
                    "v5 invalidated identity has an empty reason"
                )

        for row in attempt_rows:
            try:
                started_at = _datetime(str(row["started_at"]))
                if row["finished_at"] is not None:
                    finished_at = _datetime(str(row["finished_at"]))
                    if finished_at < started_at:
                        raise DiscoveryStoreVersionError(
                            "v5 probe completion precedes its reservation"
                        )
            except (TypeError, ValueError) as exc:
                raise DiscoveryStoreVersionError(
                    "v5 probe attempt contains a non-canonical timestamp"
                ) from exc

        resolution_by_id = {int(row["id"]): row for row in resolution_rows}
        consumed_resolution_ids = {
            int(row["identity_resolution_id"])
            for row in attempt_rows
            if row["identity_resolution_id"] is not None
        }
        for row in resolution_rows:
            superseding_id = row["superseding_resolution_id"]
            if superseding_id is None:
                continue
            source_id = int(row["id"])
            target = resolution_by_id.get(int(superseding_id))
            same_identity = target is not None and (
                target["configured_account_name"] == row["configured_account_name"]
                and target["configured_public_biz"] == row["configured_public_biz"]
            )
            both_resolved = target is not None and (
                row["outcome"] == "resolved"
                and target["outcome"] == "resolved"
            )
            ordered = target is not None and (
                int(target["id"]) > source_id
                and row["finished_at"] is not None
                and _datetime(str(target["started_at"]))
                >= _datetime(str(row["finished_at"]))
            )
            if not same_identity or not both_resolved or not ordered:
                raise DiscoveryStoreVersionError(
                    "v5 identity supersession relationship is contradictory"
                )
            if source_id in consumed_resolution_ids:
                raise DiscoveryStoreVersionError(
                    "v5 identity supersession source was consumed by a probe"
                )

        conn.execute("DROP INDEX one_probe_per_identity_resolution")
        conn.execute(
            "ALTER TABLE discovery_attempt_candidates "
            "RENAME TO discovery_attempt_candidates_v5"
        )
        conn.execute("ALTER TABLE discovery_attempts RENAME TO discovery_attempts_v5")
        conn.execute(
            "ALTER TABLE identity_resolution_attempts "
            "RENAME TO identity_resolution_attempts_v5"
        )
        DiscoveryStore._initialize_schema(conn, schema=_SCHEMA_V6)

        for row in resolution_rows:
            is_resolved = row["outcome"] == "resolved"
            conn.execute(
                """
                INSERT INTO identity_resolution_attempts(
                  id, started_at, finished_at, configured_account_name,
                  configured_public_biz, outcome, verification_basis,
                  observed_account_name, public_biz_match_origin, resolved_fakeid,
                  invalidated_at, invalidation_reason, superseding_resolution_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    _iso(_datetime(str(row["started_at"]))),
                    (
                        _iso(_datetime(str(row["finished_at"])))
                        if row["finished_at"] is not None
                        else None
                    ),
                    row["configured_account_name"],
                    row["configured_public_biz"],
                    row["outcome"],
                    row["verification_basis"],
                    row["observed_account_name"] if is_resolved else None,
                    row["observed_public_biz_origin"],
                    row["resolved_fakeid"] if is_resolved else None,
                    (
                        _iso(_datetime(str(row["invalidated_at"])))
                        if row["invalidated_at"] is not None
                        else None
                    ),
                    row["invalidation_reason"],
                    row["superseding_resolution_id"],
                ),
            )

        target_by_attempt: dict[int, tuple[str, str]] = {}
        for row in attempt_rows:
            resolution_id = row["identity_resolution_id"]
            identity_origin = str(row["identity_resolution_origin"])
            if (identity_origin == "verified_resolution") != (resolution_id is not None):
                raise DiscoveryStoreVersionError(
                    "v5 probe has a contradictory identity-resolution origin"
                )
            migrated_resolution_id = resolution_id
            migrated_identity_origin = identity_origin
            if resolution_id is not None:
                resolution = resolution_by_id.get(int(resolution_id))
                if resolution is None or (
                    resolution["configured_account_name"] != row["target_account_name"]
                    or resolution["configured_public_biz"] != row["target_public_biz"]
                ):
                    raise DiscoveryStoreVersionError(
                        "v5 probe target contradicts its identity resolution"
                    )
                if resolution["outcome"] != "resolved":
                    raise DiscoveryStoreVersionError(
                        "v5 probe has no valid verified identity relation"
                    )
                if resolution["observed_public_biz_origin"] == "predates_persistence":
                    migrated_resolution_id = None
                    migrated_identity_origin = "predates_resolution"
                elif resolution["observed_public_biz_origin"] != "recorded":
                    raise DiscoveryStoreVersionError(
                        "v5 probe has no valid verified identity relation"
                    )
                else:
                    if resolution["superseding_resolution_id"] is not None:
                        raise DiscoveryStoreVersionError(
                            "v5 probe used a superseded identity"
                        )
                    resolution_finished_at = resolution["finished_at"]
                    if resolution_finished_at is None or _datetime(
                        str(resolution_finished_at)
                    ) > _datetime(str(row["started_at"])):
                        raise DiscoveryStoreVersionError(
                            "v5 verified identity was not resolved before probe reservation"
                        )
                    invalidated_at = resolution["invalidated_at"]
                    if invalidated_at is not None:
                        invalidated_time = _datetime(str(invalidated_at))
                        probe_started_at = _datetime(str(row["started_at"]))
                        same_probe_mismatch = (
                            row["outcome"] == DiscoveryState.IDENTITY_MISMATCH.value
                            and row["finished_at"] is not None
                            and _datetime(str(row["finished_at"])) == invalidated_time
                            and resolution["invalidation_reason"]
                            == "article_url_biz_mismatch"
                        )
                        if invalidated_time < probe_started_at:
                            raise DiscoveryStoreVersionError(
                                "v5 probe used an identity invalidated before reservation"
                            )
                        if not same_probe_mismatch:
                            raise DiscoveryStoreVersionError(
                                "v5 probe identity invalidation does not match its completion"
                            )
            outcome = str(row["outcome"])
            if outcome in {
                DiscoveryState.SUCCESS_NO_NEW_SHADOW_CANDIDATES.value,
                DiscoveryState.SUCCESS_WITH_NEW_SHADOW_CANDIDATES.value,
            }:
                outcome = DiscoveryState.SUCCESS.value
            is_legacy = migrated_identity_origin == "predates_resolution"
            conn.execute(
                """
                INSERT INTO discovery_attempts(
                  id, started_at, finished_at, outcome,
                  legacy_target_account_name, legacy_target_public_biz,
                  requested_page_size, requested_page_size_origin,
                  identity_resolution_id, identity_resolution_origin
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    _iso(_datetime(str(row["started_at"]))),
                    (
                        _iso(_datetime(str(row["finished_at"])))
                        if row["finished_at"] is not None
                        else None
                    ),
                    outcome,
                    row["target_account_name"] if is_legacy else None,
                    row["target_public_biz"] if is_legacy else None,
                    row["requested_page_size"],
                    row["requested_page_size_origin"],
                    migrated_resolution_id,
                    migrated_identity_origin,
                ),
            )
            target_by_attempt[int(row["id"])] = (
                str(row["target_account_name"]),
                str(row["target_public_biz"]),
            )

        attempt_by_id = {int(row["id"]): row for row in attempt_rows}
        for row in candidate_rows:
            attempt_id = int(row["attempt_id"])
            source_attempt = attempt_by_id.get(attempt_id)
            if source_attempt is None or source_attempt["outcome"] not in {
                DiscoveryState.SUCCESS_NO_NEW_SHADOW_CANDIDATES.value,
                DiscoveryState.SUCCESS_WITH_NEW_SHADOW_CANDIDATES.value,
            }:
                raise DiscoveryStoreVersionError(
                    "v5 failed probe contains candidates"
                )
            target = target_by_attempt.get(attempt_id)
            if target is None or row["observed_public_biz"] != target[1]:
                raise DiscoveryStoreVersionError(
                    "v5 candidate identity contradicts its probe target"
                )
            try:
                if observed_article_biz(str(row["url"])) != target[1]:
                    raise DiscoveryStoreVersionError(
                        "v5 candidate URL contradicts its probe target"
                    )
                published_at = _iso(_datetime(str(row["published_at"])))
            except (DiscoveryIdentityMismatch, TypeError, ValueError) as exc:
                raise DiscoveryStoreVersionError(
                    "v5 candidate is not recoverable as a verified snapshot"
                ) from exc
            conn.execute(
                """
                INSERT INTO discovery_attempt_candidates(
                  probe_attempt_id, url, title, author, published_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (attempt_id, row["url"], row["title"], row["author"], published_at),
            )

        conn.execute("DROP TABLE discovery_attempt_candidates_v5")
        conn.execute("DROP TABLE discovery_attempts_v5")
        conn.execute("DROP TABLE identity_resolution_attempts_v5")
        conn.execute("PRAGMA user_version=6")
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise DiscoveryStoreVersionError("v6 migration created a foreign-key violation")

    @staticmethod
    def _migrate_v6_to_v7(conn: sqlite3.Connection) -> None:
        resolution_rows = conn.execute(
            "SELECT * FROM identity_resolution_attempts ORDER BY id"
        ).fetchall()
        attempt_rows = conn.execute(
            "SELECT * FROM discovery_attempts ORDER BY id"
        ).fetchall()
        candidate_rows = conn.execute(
            "SELECT * FROM discovery_attempt_candidates ORDER BY probe_attempt_id, rowid"
        ).fetchall()

        resolution_by_id = {int(row["id"]): row for row in resolution_rows}
        for row in resolution_rows:
            try:
                started_at = _datetime(str(row["started_at"]))
                finished_at = (
                    _datetime(str(row["finished_at"]))
                    if row["finished_at"] is not None
                    else None
                )
                if finished_at is not None and finished_at < started_at:
                    raise DiscoveryStoreVersionError(
                        "v6 identity resolution completion precedes its reservation"
                    )
                if row["invalidated_at"] is not None:
                    invalidated_at = _datetime(str(row["invalidated_at"]))
                    if finished_at is None or invalidated_at < finished_at:
                        raise DiscoveryStoreVersionError(
                            "v6 identity invalidation precedes its terminal outcome"
                        )
            except (TypeError, ValueError) as exc:
                raise DiscoveryStoreVersionError(
                    "v6 identity resolution contains a non-canonical timestamp"
                ) from exc
            is_resolved = row["outcome"] == "resolved"
            if is_resolved:
                required = (row["resolved_fakeid"], row["observed_account_name"])
                if any(value is None or not str(value).strip() for value in required):
                    raise DiscoveryStoreVersionError(
                        "v6 resolved identity is missing recoverable provisional fields"
                    )
                if normalized_account_name(str(row["observed_account_name"])) != (
                    normalized_account_name(str(row["configured_account_name"]))
                ):
                    raise DiscoveryStoreVersionError(
                        "v6 resolved identity contradicts its configured account name"
                    )
            if row["invalidation_reason"] is not None and not str(
                row["invalidation_reason"]
            ).strip():
                raise DiscoveryStoreVersionError(
                    "v6 invalidated identity has an empty reason"
                )

        attempt_by_id = {int(row["id"]): row for row in attempt_rows}
        target_by_attempt: dict[int, tuple[str, str]] = {}
        for row in attempt_rows:
            try:
                started_at = _datetime(str(row["started_at"]))
                finished_at = (
                    _datetime(str(row["finished_at"]))
                    if row["finished_at"] is not None
                    else None
                )
                if finished_at is not None and finished_at < started_at:
                    raise DiscoveryStoreVersionError(
                        "v6 probe completion precedes its reservation"
                    )
            except (TypeError, ValueError) as exc:
                raise DiscoveryStoreVersionError(
                    "v6 probe attempt contains a non-canonical timestamp"
                ) from exc
            origin = str(row["identity_resolution_origin"])
            resolution_id = row["identity_resolution_id"]
            if origin == "verified_resolution":
                resolution = (
                    resolution_by_id.get(int(resolution_id))
                    if resolution_id is not None
                    else None
                )
                if resolution is None or resolution["outcome"] != "resolved":
                    raise DiscoveryStoreVersionError(
                        "v6 probe has no historical name-and-biz resolution relation"
                    )
                target = (
                    str(resolution["configured_account_name"]),
                    str(resolution["configured_public_biz"]),
                )
            elif origin == "predates_resolution" and resolution_id is None:
                legacy_name = row["legacy_target_account_name"]
                legacy_biz = row["legacy_target_public_biz"]
                if any(
                    value is None or not str(value).strip()
                    for value in (legacy_name, legacy_biz)
                ):
                    raise DiscoveryStoreVersionError(
                        "v6 legacy probe has no recoverable target identity"
                    )
                target = (str(legacy_name), str(legacy_biz))
            else:
                raise DiscoveryStoreVersionError(
                    "v6 probe has a contradictory identity-resolution origin"
                )
            target_by_attempt[int(row["id"])] = target

        candidates_by_attempt: dict[int, list[sqlite3.Row]] = {}
        for row in candidate_rows:
            attempt_id = int(row["probe_attempt_id"])
            source_attempt = attempt_by_id.get(attempt_id)
            target = target_by_attempt.get(attempt_id)
            if (
                source_attempt is None
                or target is None
                or source_attempt["outcome"] != DiscoveryState.SUCCESS.value
            ):
                raise DiscoveryStoreVersionError(
                    "v6 failed probe contains candidates"
                )
            try:
                if observed_article_biz(str(row["url"])) != target[1]:
                    raise DiscoveryStoreVersionError(
                        "v6 candidate URL contradicts its probe target"
                    )
                _datetime(str(row["published_at"]))
            except (
                DiscoveryIdentityMismatch,
                DiscoveryIdentityUnverified,
                DiscoveryResponseInvalid,
                TypeError,
                ValueError,
            ) as exc:
                raise DiscoveryStoreVersionError(
                    "v6 candidate is not recoverable as a historical snapshot"
                ) from exc
            candidates_by_attempt.setdefault(attempt_id, []).append(row)

        conn.execute("DROP INDEX one_probe_per_identity_resolution")
        conn.execute(
            "ALTER TABLE discovery_attempt_candidates "
            "RENAME TO discovery_attempt_candidates_v6"
        )
        conn.execute("ALTER TABLE discovery_attempts RENAME TO discovery_attempts_v6")
        conn.execute(
            "ALTER TABLE identity_resolution_attempts "
            "RENAME TO identity_resolution_attempts_v6"
        )
        DiscoveryStore._initialize_schema(conn, schema=_SCHEMA_V7)

        for row in resolution_rows:
            is_resolved = row["outcome"] == "resolved"
            outcome = (
                IdentityResolutionState.LEGACY_NAME_AND_BIZ_MATCH.value
                if is_resolved
                else str(row["outcome"])
            )
            conn.execute(
                """
                INSERT INTO identity_resolution_attempts(
                  id, started_at, finished_at, configured_account_name,
                  configured_public_biz, outcome, provisional_match_origin,
                  observed_account_name, provisional_fakeid,
                  invalidated_at, invalidation_reason, superseding_resolution_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    _iso(_datetime(str(row["started_at"]))),
                    (
                        _iso(_datetime(str(row["finished_at"])))
                        if row["finished_at"] is not None
                        else None
                    ),
                    row["configured_account_name"],
                    row["configured_public_biz"],
                    outcome,
                    (
                        "predates_unique_normalized_name_contract"
                        if is_resolved
                        else "not_established"
                    ),
                    row["observed_account_name"] if is_resolved else None,
                    row["resolved_fakeid"] if is_resolved else None,
                    (
                        _iso(_datetime(str(row["invalidated_at"])))
                        if row["invalidated_at"] is not None
                        else None
                    ),
                    row["invalidation_reason"],
                    row["superseding_resolution_id"],
                ),
            )

        for row in attempt_rows:
            old_origin = str(row["identity_resolution_origin"])
            is_legacy_target = old_origin == "predates_resolution"
            identity_origin = (
                "predates_resolution"
                if is_legacy_target
                else "legacy_name_and_biz_match"
            )
            outcome = str(row["outcome"])
            if outcome == DiscoveryState.RESERVED.value:
                evidence = TargetIdentityEvidence.PENDING.value
            elif outcome == DiscoveryState.SUCCESS.value or outcome in {
                DiscoveryState.IDENTITY_UNVERIFIED.value,
                DiscoveryState.IDENTITY_MISMATCH.value,
            }:
                evidence = TargetIdentityEvidence.PREDATES_V7_VERIFICATION.value
            else:
                evidence = TargetIdentityEvidence.NOT_OBSERVED.value
            conn.execute(
                """
                INSERT INTO discovery_attempts(
                  id, started_at, finished_at, outcome,
                  legacy_target_account_name, legacy_target_public_biz,
                  requested_page_size, requested_page_size_origin,
                  identity_resolution_id, identity_resolution_origin,
                  target_identity_evidence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    _iso(_datetime(str(row["started_at"]))),
                    (
                        _iso(_datetime(str(row["finished_at"])))
                        if row["finished_at"] is not None
                        else None
                    ),
                    outcome,
                    row["legacy_target_account_name"] if is_legacy_target else None,
                    row["legacy_target_public_biz"] if is_legacy_target else None,
                    row["requested_page_size"],
                    row["requested_page_size_origin"],
                    row["identity_resolution_id"] if not is_legacy_target else None,
                    identity_origin,
                    evidence,
                ),
            )

        for row in candidate_rows:
            conn.execute(
                """
                INSERT INTO discovery_attempt_candidates(
                  probe_attempt_id, url, title, author, published_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    row["probe_attempt_id"],
                    row["url"],
                    row["title"],
                    row["author"],
                    _iso(_datetime(str(row["published_at"]))),
                ),
            )

        conn.execute("DROP TABLE discovery_attempt_candidates_v6")
        conn.execute("DROP TABLE discovery_attempts_v6")
        conn.execute("DROP TABLE identity_resolution_attempts_v6")
        conn.execute("PRAGMA user_version=7")
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise DiscoveryStoreVersionError(
                "v7 migration created a foreign-key violation"
            )

    @staticmethod
    def _migrate_v7_to_v8(conn: sqlite3.Connection) -> None:
        resolution_rows = conn.execute(
            "SELECT * FROM identity_resolution_attempts ORDER BY id"
        ).fetchall()
        attempt_rows = conn.execute(
            "SELECT * FROM discovery_attempts ORDER BY id"
        ).fetchall()
        candidate_rows = conn.execute(
            "SELECT * FROM discovery_attempt_candidates ORDER BY probe_attempt_id, rowid"
        ).fetchall()

        resolution_by_id = {int(row["id"]): row for row in resolution_rows}
        attempt_by_id = {int(row["id"]): row for row in attempt_rows}
        candidates_by_attempt: dict[int, list[sqlite3.Row]] = {}
        for row in candidate_rows:
            candidates_by_attempt.setdefault(int(row["probe_attempt_id"]), []).append(row)

        for row in resolution_rows:
            try:
                started_at = _datetime(str(row["started_at"]))
                finished_at = (
                    _datetime(str(row["finished_at"]))
                    if row["finished_at"] is not None
                    else None
                )
                if finished_at is not None and finished_at < started_at:
                    raise DiscoveryStoreVersionError(
                        "v7 identity resolution completion precedes its reservation"
                    )
                if row["invalidated_at"] is not None:
                    invalidated_at = _datetime(str(row["invalidated_at"]))
                    if finished_at is None or invalidated_at < finished_at:
                        raise DiscoveryStoreVersionError(
                            "v7 identity invalidation precedes its terminal outcome"
                        )
            except (TypeError, ValueError) as exc:
                raise DiscoveryStoreVersionError(
                    "v7 identity resolution contains a non-canonical timestamp"
                ) from exc

        for row in attempt_rows:
            attempt_id = int(row["id"])
            try:
                started_at = _datetime(str(row["started_at"]))
                finished_at = (
                    _datetime(str(row["finished_at"]))
                    if row["finished_at"] is not None
                    else None
                )
                if finished_at is not None and finished_at < started_at:
                    raise DiscoveryStoreVersionError(
                        "v7 probe completion precedes its reservation"
                    )
            except (TypeError, ValueError) as exc:
                raise DiscoveryStoreVersionError(
                    "v7 probe contains a non-canonical timestamp"
                ) from exc

            origin = str(row["identity_resolution_origin"])
            resolution_id = row["identity_resolution_id"]
            resolution = (
                resolution_by_id.get(int(resolution_id))
                if resolution_id is not None
                else None
            )
            if origin == "provisional_searchbiz_match":
                if (
                    resolution is None
                    or resolution["outcome"]
                    != IdentityResolutionState.PROVISIONAL_MATCH.value
                    or resolution["provisional_match_origin"]
                    != "searchbiz_unique_normalized_name"
                ):
                    raise DiscoveryStoreVersionError(
                        "v7 probe has no provisional searchbiz relation"
                    )
                if (
                    row["target_identity_evidence"]
                    == TargetIdentityEvidence.PREDATES_V7_VERIFICATION.value
                ):
                    raise DiscoveryStoreVersionError(
                        "v7 provisional probe uses migration-only identity evidence"
                    )
            elif origin == "legacy_name_and_biz_match":
                if (
                    resolution is None
                    or resolution["outcome"]
                    != IdentityResolutionState.LEGACY_NAME_AND_BIZ_MATCH.value
                    or resolution["provisional_match_origin"]
                    != "predates_unique_normalized_name_contract"
                ):
                    raise DiscoveryStoreVersionError(
                        "v7 probe has no historical name-and-biz relation"
                    )
            elif origin == "predates_resolution":
                if resolution is not None:
                    raise DiscoveryStoreVersionError(
                        "v7 legacy probe unexpectedly references a resolution"
                    )
            else:
                raise DiscoveryStoreVersionError(
                    "v7 probe has an unknown identity relation"
                )

            outcome = str(row["outcome"])
            candidates = candidates_by_attempt.get(attempt_id, [])
            if outcome != DiscoveryState.SUCCESS.value and candidates:
                raise DiscoveryStoreVersionError(
                    "v7 non-success probe contains candidates"
                )
            if (
                row["target_identity_evidence"]
                == TargetIdentityEvidence.ARTICLE_URL_PUBLIC_BIZ_VERIFIED.value
            ):
                if resolution is None:
                    raise DiscoveryStoreVersionError(
                        "v7 verified probe has no provisional relation"
                    )
                if resolution["invalidated_at"] is not None:
                    raise DiscoveryStoreVersionError(
                        "v7 verified probe uses an invalidated relation"
                    )
                if resolution["superseding_resolution_id"] is not None:
                    raise DiscoveryStoreVersionError(
                        "v7 verified probe uses a superseded relation"
                    )
                if not candidates:
                    raise DiscoveryStoreVersionError(
                        "v7 verified probe has no candidate snapshot"
                    )

            target_biz = (
                str(resolution["configured_public_biz"])
                if resolution is not None
                else str(row["legacy_target_public_biz"])
            )
            for candidate in candidates:
                try:
                    if observed_article_biz(str(candidate["url"])) != target_biz:
                        raise DiscoveryStoreVersionError(
                            "v7 candidate URL contradicts its probe target"
                        )
                    _datetime(str(candidate["published_at"]))
                except (
                    DiscoveryIdentityMismatch,
                    DiscoveryIdentityUnverified,
                    DiscoveryResponseInvalid,
                    TypeError,
                    ValueError,
                ) as exc:
                    raise DiscoveryStoreVersionError(
                        "v7 candidate snapshot is not recoverable"
                    ) from exc

        for attempt_id in candidates_by_attempt:
            if attempt_id not in attempt_by_id:
                raise DiscoveryStoreVersionError("v7 candidate has no probe attempt")

        for trigger_name in (
            "validate_discovery_attempt_insert",
            "validate_discovery_attempt_update",
            "validate_discovery_candidate_insert",
            "validate_discovery_candidate_delete",
        ):
            conn.execute(f"DROP TRIGGER {trigger_name}")
        conn.execute("DROP INDEX one_probe_per_identity_resolution")
        conn.execute(
            "ALTER TABLE discovery_attempt_candidates "
            "RENAME TO discovery_attempt_candidates_v7"
        )
        conn.execute("ALTER TABLE discovery_attempts RENAME TO discovery_attempts_v7")
        conn.execute(
            "ALTER TABLE identity_resolution_attempts "
            "RENAME TO identity_resolution_attempts_v7"
        )
        DiscoveryStore._initialize_schema(conn, schema=_SCHEMA_V8)
        for trigger_name in (
            "validate_discovery_attempt_insert",
            "validate_discovery_attempt_update",
            "validate_discovery_candidate_insert",
            "validate_discovery_candidate_update",
            "validate_discovery_candidate_delete",
        ):
            conn.execute(f"DROP TRIGGER {trigger_name}")

        conn.execute(
            """
            INSERT INTO identity_resolution_attempts
            SELECT * FROM identity_resolution_attempts_v7 ORDER BY id
            """
        )
        conn.execute(
            """
            INSERT INTO discovery_attempts
            SELECT * FROM discovery_attempts_v7 ORDER BY id
            """
        )
        conn.execute(
            """
            INSERT INTO discovery_attempt_candidates
            SELECT * FROM discovery_attempt_candidates_v7 ORDER BY probe_attempt_id, rowid
            """
        )
        DiscoveryStore._initialize_schema(conn, schema=_SCHEMA_V8_TRIGGERS)
        conn.execute("DROP TABLE discovery_attempt_candidates_v7")
        conn.execute("DROP TABLE discovery_attempts_v7")
        conn.execute("DROP TABLE identity_resolution_attempts_v7")
        conn.execute("PRAGMA user_version=8")
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise DiscoveryStoreVersionError(
                "v8 migration created a foreign-key violation"
            )

    @staticmethod
    def _migrate_v8_to_v9(conn: sqlite3.Connection) -> None:
        resolution_rows = conn.execute(
            "SELECT * FROM identity_resolution_attempts ORDER BY id"
        ).fetchall()
        attempt_rows = conn.execute(
            "SELECT * FROM discovery_attempts ORDER BY id"
        ).fetchall()
        candidate_rows = conn.execute(
            "SELECT * FROM discovery_attempt_candidates ORDER BY probe_attempt_id, rowid"
        ).fetchall()

        for trigger_name in (
            "validate_discovery_attempt_insert",
            "validate_discovery_attempt_update",
            "validate_discovery_candidate_insert",
            "validate_discovery_candidate_update",
            "validate_discovery_candidate_delete",
        ):
            conn.execute(f"DROP TRIGGER {trigger_name}")
        conn.execute("DROP INDEX one_probe_per_identity_resolution")
        conn.execute(
            "ALTER TABLE discovery_attempt_candidates "
            "RENAME TO discovery_attempt_candidates_v8"
        )
        conn.execute("ALTER TABLE discovery_attempts RENAME TO discovery_attempts_v8")
        conn.execute(
            "ALTER TABLE identity_resolution_attempts "
            "RENAME TO identity_resolution_attempts_v8"
        )
        DiscoveryStore._initialize_schema(conn, schema=_SCHEMA_V9)
        for trigger_name in (
            "validate_discovery_attempt_insert",
            "validate_discovery_attempt_update",
            "validate_discovery_candidate_insert",
            "validate_discovery_candidate_update",
            "validate_discovery_candidate_delete",
            "reject_historical_resolution_platform_ret_insert",
            "reject_historical_probe_platform_ret_insert",
        ):
            conn.execute(f"DROP TRIGGER {trigger_name}")

        for row in resolution_rows:
            outcome = str(row["outcome"])
            platform_ret_origin = (
                "predates_persistence"
                if outcome
                in {
                    IdentityResolutionState.AUTH_REQUIRED.value,
                    IdentityResolutionState.RATE_LIMITED.value,
                    IdentityResolutionState.RESPONSE_INVALID.value,
                }
                else "not_applicable"
            )
            conn.execute(
                """
                INSERT INTO identity_resolution_attempts(
                  id, started_at, finished_at, configured_account_name,
                  configured_public_biz, outcome, platform_error_ret,
                  platform_error_ret_origin, provisional_match_origin,
                  observed_account_name, provisional_fakeid, invalidated_at,
                  invalidation_reason, superseding_resolution_id
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    row["started_at"],
                    row["finished_at"],
                    row["configured_account_name"],
                    row["configured_public_biz"],
                    outcome,
                    platform_ret_origin,
                    row["provisional_match_origin"],
                    row["observed_account_name"],
                    row["provisional_fakeid"],
                    row["invalidated_at"],
                    row["invalidation_reason"],
                    row["superseding_resolution_id"],
                ),
            )

        for row in attempt_rows:
            outcome = str(row["outcome"])
            platform_ret_origin = (
                "predates_persistence"
                if outcome
                in {
                    DiscoveryState.AUTH_REQUIRED.value,
                    DiscoveryState.RATE_LIMITED.value,
                    DiscoveryState.RESPONSE_INVALID.value,
                }
                else "not_applicable"
            )
            conn.execute(
                """
                INSERT INTO discovery_attempts(
                  id, started_at, finished_at, outcome, platform_error_ret,
                  platform_error_ret_origin, legacy_target_account_name,
                  legacy_target_public_biz, requested_page_size,
                  requested_page_size_origin, identity_resolution_id,
                  identity_resolution_origin, target_identity_evidence
                ) VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    row["started_at"],
                    row["finished_at"],
                    outcome,
                    platform_ret_origin,
                    row["legacy_target_account_name"],
                    row["legacy_target_public_biz"],
                    row["requested_page_size"],
                    row["requested_page_size_origin"],
                    row["identity_resolution_id"],
                    row["identity_resolution_origin"],
                    row["target_identity_evidence"],
                ),
            )

        conn.executemany(
            """
            INSERT INTO discovery_attempt_candidates(
              probe_attempt_id, url, title, author, published_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    row["probe_attempt_id"],
                    row["url"],
                    row["title"],
                    row["author"],
                    row["published_at"],
                )
                for row in candidate_rows
            ],
        )
        DiscoveryStore._initialize_schema(conn, schema=_SCHEMA_V9_TRIGGERS)
        conn.execute("DROP TABLE discovery_attempt_candidates_v8")
        conn.execute("DROP TABLE discovery_attempts_v8")
        conn.execute("DROP TABLE identity_resolution_attempts_v8")
        conn.execute("PRAGMA user_version=9")
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise DiscoveryStoreVersionError(
                "v9 migration created a foreign-key violation"
            )

    @staticmethod
    def _migrate_v9_to_v10(conn: sqlite3.Connection) -> None:
        invalid_ret = conn.execute(
            """
            SELECT 'resolution' AS ledger, id
            FROM identity_resolution_attempts
            WHERE platform_error_ret IS NOT NULL
              AND typeof(platform_error_ret) != 'integer'
            UNION ALL
            SELECT 'probe' AS ledger, id
            FROM discovery_attempts
            WHERE platform_error_ret IS NOT NULL
              AND typeof(platform_error_ret) != 'integer'
            LIMIT 1
            """
        ).fetchone()
        if invalid_ret is not None:
            raise DiscoveryStoreVersionError(
                "v9 platform error ret is not an exact integer"
            )
        DiscoveryStore._initialize_schema(
            conn,
            schema=_SCHEMA_V10_PLATFORM_RET_TRIGGERS,
        )
        conn.execute("PRAGMA user_version=10")

    @staticmethod
    def _latest_backend_request(conn: sqlite3.Connection) -> BackendRequest | None:
        row = conn.execute(
            """
            SELECT id, 'resolve' AS kind, started_at, finished_at, outcome,
                   configured_account_name AS account_name,
                   platform_error_ret, platform_error_ret_origin
            FROM identity_resolution_attempts
            UNION ALL
            SELECT a.id, 'probe' AS kind, a.started_at, a.finished_at, a.outcome,
                   COALESCE(r.configured_account_name, a.legacy_target_account_name)
                     AS account_name,
                   a.platform_error_ret, a.platform_error_ret_origin
            FROM discovery_attempts a
            LEFT JOIN identity_resolution_attempts r ON r.id=a.identity_resolution_id
            ORDER BY started_at DESC, kind DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        return BackendRequest(
            id=int(row["id"]),
            kind=str(row["kind"]),
            started_at=_datetime(str(row["started_at"])),
            finished_at=(
                _datetime(str(row["finished_at"])) if row["finished_at"] is not None else None
            ),
            state=str(row["outcome"]),
            account_name=str(row["account_name"]),
            platform_error_ret=(
                int(row["platform_error_ret"])
                if row["platform_error_ret"] is not None
                else None
            ),
            platform_error_ret_origin=str(row["platform_error_ret_origin"]),
        )

    def latest_backend_request(self) -> BackendRequest | None:
        if not self.path.exists():
            return None
        with self.readonly_connect() as conn:
            return self._latest_backend_request(conn)

    def reserve_identity_resolution(
        self,
        account: AccountConfig,
        *,
        config: DiscoveryConfig,
        started_at: datetime,
    ) -> int:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            blocked_until = backend_request_blocked_until(
                config, self._latest_backend_request(conn), now=started_at
            )
            if blocked_until is not None:
                conn.rollback()
                raise DiscoveryCooldownActive(blocked_until)
            cursor = conn.execute(
                """
                INSERT INTO identity_resolution_attempts(
                  started_at, finished_at, configured_account_name,
                  configured_public_biz, outcome, platform_error_ret,
                  platform_error_ret_origin, provisional_match_origin,
                  observed_account_name, provisional_fakeid
                ) VALUES (?, NULL, ?, ?, 'reserved', NULL, 'not_applicable',
                  'not_established', NULL, NULL)
                """,
                (_iso(started_at), account.name, account.public_biz),
            )
            resolution_id = int(cursor.lastrowid)
            conn.commit()
        return resolution_id

    def complete_identity_resolution(
        self,
        resolution_id: int,
        *,
        state: IdentityResolutionState,
        finished_at: datetime,
        provisional: ProvisionalIdentity | None = None,
        platform_error_ret: int | None = None,
    ) -> None:
        if state is IdentityResolutionState.RESERVED:
            raise ValueError("a resolution cannot be completed as reserved")
        if (state is IdentityResolutionState.PROVISIONAL_MATCH) != (
            provisional is not None
        ):
            raise ValueError(
                "only a provisional-match outcome may persist a searchbiz candidate"
            )
        platform_error_ret, platform_error_ret_origin = _platform_error_fields(
            state, platform_error_ret
        )
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM identity_resolution_attempts WHERE id=?", (resolution_id,)
            ).fetchone()
            if row is None:
                conn.rollback()
                raise ValueError("identity resolution reservation does not exist")
            if row["outcome"] != IdentityResolutionState.RESERVED.value:
                conn.rollback()
                raise ValueError("identity resolution reservation is already finalized")
            if finished_at < _datetime(str(row["started_at"])):
                conn.rollback()
                raise ValueError("identity resolution completion precedes its reservation")
            configured_name = str(row["configured_account_name"])
            configured_biz = str(row["configured_public_biz"])
            if provisional is not None:
                if (
                    normalized_account_name(provisional.account_name)
                    != normalized_account_name(configured_name)
                    or not provisional.fakeid.strip()
                ):
                    conn.rollback()
                    raise ValueError(
                        "searchbiz candidate does not match the reserved account name"
                    )
                conn.execute(
                    """
                    UPDATE identity_resolution_attempts
                    SET superseding_resolution_id=?
                    WHERE configured_account_name=? AND configured_public_biz=?
                      AND id<>? AND outcome='provisional_match'
                      AND invalidated_at IS NULL AND superseding_resolution_id IS NULL
                      AND NOT EXISTS (
                        SELECT 1 FROM discovery_attempts a
                        WHERE a.identity_resolution_id=identity_resolution_attempts.id
                      )
                    """,
                    (resolution_id, configured_name, configured_biz, resolution_id),
                )
            elif state in {
                IdentityResolutionState.NO_MATCH,
                IdentityResolutionState.AMBIGUOUS_MATCH,
            }:
                conn.execute(
                    """
                    UPDATE identity_resolution_attempts
                    SET invalidated_at=?, invalidation_reason=?
                    WHERE configured_account_name=? AND configured_public_biz=?
                      AND id<>? AND outcome='provisional_match'
                      AND invalidated_at IS NULL AND superseding_resolution_id IS NULL
                      AND NOT EXISTS (
                        SELECT 1 FROM discovery_attempts a
                        WHERE a.identity_resolution_id=identity_resolution_attempts.id
                      )
                    """,
                    (_iso(finished_at), state.value, configured_name, configured_biz, resolution_id),
                )
            conn.execute(
                """
                UPDATE identity_resolution_attempts
                SET finished_at=?, outcome=?, observed_account_name=?,
                    provisional_match_origin=?, provisional_fakeid=?,
                    platform_error_ret=?, platform_error_ret_origin=?
                WHERE id=?
                """,
                (
                    _iso(finished_at),
                    state.value,
                    (
                        provisional.account_name.strip()
                        if provisional is not None
                        else None
                    ),
                    (
                        "searchbiz_unique_normalized_name"
                        if provisional is not None
                        else "not_established"
                    ),
                    provisional.fakeid.strip() if provisional is not None else None,
                    platform_error_ret,
                    platform_error_ret_origin,
                    resolution_id,
                ),
            )
            conn.commit()

    def identity_resolution(self, resolution_id: int) -> IdentityResolution | None:
        if not self.path.exists():
            return None
        with self.readonly_connect() as conn:
            row = conn.execute(
                """
                SELECT r.*, a.id AS assigned_probe_attempt_id,
                       a.started_at AS assigned_at
                FROM identity_resolution_attempts r
                LEFT JOIN discovery_attempts a ON a.identity_resolution_id=r.id
                WHERE r.id=?
                """,
                (resolution_id,),
            ).fetchone()
        if row is None:
            return None
        return IdentityResolution(
            id=int(row["id"]),
            started_at=_datetime(str(row["started_at"])),
            finished_at=(
                _datetime(str(row["finished_at"])) if row["finished_at"] is not None else None
            ),
            configured_account_name=str(row["configured_account_name"]),
            configured_public_biz=str(row["configured_public_biz"]),
            state=IdentityResolutionState(str(row["outcome"])),
            observed_account_name=(
                str(row["observed_account_name"])
                if row["observed_account_name"] is not None
                else None
            ),
            provisional_match_origin=str(row["provisional_match_origin"]),
            fakeid=(
                str(row["provisional_fakeid"])
                if row["provisional_fakeid"] is not None
                else None
            ),
            assigned_at=(
                _datetime(str(row["assigned_at"])) if row["assigned_at"] is not None else None
            ),
            assigned_probe_attempt_id=(
                int(row["assigned_probe_attempt_id"])
                if row["assigned_probe_attempt_id"] is not None
                else None
            ),
            invalidated_at=(
                _datetime(str(row["invalidated_at"]))
                if row["invalidated_at"] is not None
                else None
            ),
            invalidation_reason=(
                str(row["invalidation_reason"])
                if row["invalidation_reason"] is not None
                else None
            ),
            superseding_resolution_id=(
                int(row["superseding_resolution_id"])
                if row["superseding_resolution_id"] is not None
                else None
            ),
            platform_error_ret=(
                int(row["platform_error_ret"])
                if row["platform_error_ret"] is not None
                else None
            ),
            platform_error_ret_origin=str(row["platform_error_ret_origin"]),
        )

    def latest_identity_resolution(self) -> IdentityResolution | None:
        if not self.path.exists():
            return None
        with self.readonly_connect() as conn:
            row = conn.execute(
                "SELECT id FROM identity_resolution_attempts ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return self.identity_resolution(int(row["id"])) if row is not None else None

    @staticmethod
    def _ready_provisional_match(
        conn: sqlite3.Connection, account: AccountConfig
    ) -> sqlite3.Row | None:
        return conn.execute(
            """
            SELECT r.id, r.provisional_fakeid
            FROM identity_resolution_attempts r
            WHERE r.configured_account_name=? AND r.configured_public_biz=?
              AND r.outcome='provisional_match'
              AND r.provisional_match_origin='searchbiz_unique_normalized_name'
              AND r.invalidated_at IS NULL AND r.superseding_resolution_id IS NULL
              AND NOT EXISTS (
                SELECT 1 FROM discovery_attempts a WHERE a.identity_resolution_id=r.id
              )
            ORDER BY r.id DESC LIMIT 1
            """,
            (account.name, account.public_biz),
        ).fetchone()

    def identity_status(
        self, accounts: tuple[AccountConfig, ...]
    ) -> tuple[tuple[tuple[str, int], ...], int, int, int]:
        ready: list[tuple[str, int]] = []
        assigned = invalidated = unresolved = 0
        if not self.path.exists():
            return (), 0, 0, len(accounts)
        with self.readonly_connect() as conn:
            for account in accounts:
                active = self._ready_provisional_match(conn, account)
                if active is not None:
                    ready.append((account.name, int(active["id"])))
                    continue
                assigned_row = conn.execute(
                    """
                    SELECT 1 FROM identity_resolution_attempts r
                    JOIN discovery_attempts a ON a.identity_resolution_id=r.id
                    WHERE r.configured_account_name=? AND r.configured_public_biz=?
                      AND r.invalidated_at IS NULL
                      AND r.superseding_resolution_id IS NULL
                    LIMIT 1
                    """,
                    (account.name, account.public_biz),
                ).fetchone()
                if assigned_row is not None:
                    assigned += 1
                    continue
                latest = conn.execute(
                    """
                    SELECT outcome, invalidated_at, superseding_resolution_id
                    FROM identity_resolution_attempts
                    WHERE configured_account_name=? AND configured_public_biz=?
                    ORDER BY id DESC LIMIT 1
                    """,
                    (account.name, account.public_biz),
                ).fetchone()
                if latest is not None and (
                    latest["invalidated_at"] is not None
                    or latest["superseding_resolution_id"] is not None
                    or latest["outcome"]
                    in {
                        IdentityResolutionState.NO_MATCH.value,
                        IdentityResolutionState.AMBIGUOUS_MATCH.value,
                    }
                ):
                    invalidated += 1
                else:
                    unresolved += 1
        return tuple(ready), assigned, invalidated, unresolved

    def identity_status_counts(
        self, accounts: tuple[AccountConfig, ...]
    ) -> tuple[int, int, int, int]:
        ready, assigned, invalidated, unresolved = self.identity_status(accounts)
        return len(ready), assigned, invalidated, unresolved

    def reserve_probe(
        self,
        account: AccountConfig,
        *,
        config: DiscoveryConfig,
        started_at: datetime,
        requested_page_size: int,
    ) -> ProbeReservation:
        if isinstance(requested_page_size, bool) or not 1 <= requested_page_size <= 20:
            raise ValueError("WeChat discovery page size must be between 1 and 20")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            blocked_until = backend_request_blocked_until(
                config, self._latest_backend_request(conn), now=started_at
            )
            if blocked_until is not None:
                conn.rollback()
                raise DiscoveryCooldownActive(blocked_until)
            resolution = self._ready_provisional_match(conn, account)
            if resolution is None:
                conn.rollback()
                raise DiscoveryIdentityNoMatch(
                    "no unused provisional searchbiz mapping exists; resolve the account first"
                )
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO discovery_attempts(
                      started_at, finished_at, outcome,
                      platform_error_ret, platform_error_ret_origin,
                      legacy_target_account_name, legacy_target_public_biz,
                      requested_page_size, requested_page_size_origin,
                      identity_resolution_id, identity_resolution_origin,
                      target_identity_evidence
                    ) VALUES (?, NULL, 'reserved', NULL, 'not_applicable', NULL, NULL,
                      ?, 'recorded', ?, 'provisional_searchbiz_match', 'pending')
                    """,
                    (
                        _iso(started_at),
                        requested_page_size,
                        int(resolution["id"]),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                raise DiscoveryIdentityNoMatch(
                    "provisional searchbiz mapping was no longer available; resolve again"
                ) from exc
            attempt_id = int(cursor.lastrowid)
            conn.commit()
        return ProbeReservation(
            attempt_id=attempt_id,
            identity_resolution_id=int(resolution["id"]),
            fakeid=str(resolution["provisional_fakeid"]),
        )

    def complete_probe(
        self,
        attempt_id: int,
        *,
        finished_at: datetime,
        candidates: tuple[DiscoveryArticle, ...] = (),
        state: DiscoveryState | None = None,
        target_identity_evidence: TargetIdentityEvidence | None = None,
        platform_error_ret: int | None = None,
    ) -> ProbeCompletion:
        if state is DiscoveryState.RESERVED:
            raise ValueError("a probe cannot be completed as reserved")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT a.*, r.outcome AS resolution_outcome,
                       r.configured_account_name, r.configured_public_biz,
                       r.provisional_match_origin, r.invalidated_at,
                       r.superseding_resolution_id
                FROM discovery_attempts a
                JOIN identity_resolution_attempts r ON r.id=a.identity_resolution_id
                WHERE a.id=?
                """,
                (attempt_id,),
            ).fetchone()
            if row is None:
                conn.rollback()
                raise ValueError("probe reservation has no provisional identity relation")
            if row["outcome"] != DiscoveryState.RESERVED.value:
                conn.rollback()
                raise ValueError("probe reservation is already finalized")
            if finished_at < _datetime(str(row["started_at"])):
                conn.rollback()
                raise ValueError("probe completion precedes its reservation")
            account_name = str(row["configured_account_name"])
            biz = str(row["configured_public_biz"])
            if (
                row["resolution_outcome"]
                != IdentityResolutionState.PROVISIONAL_MATCH.value
                or row["provisional_match_origin"]
                != "searchbiz_unique_normalized_name"
                or row["invalidated_at"] is not None
                or row["superseding_resolution_id"] is not None
            ):
                conn.rollback()
                raise ValueError("probe reservation provisional relation is no longer valid")

            urls: set[str] = set()
            for article in candidates:
                if article.url in urls:
                    conn.rollback()
                    raise ValueError("probe candidate URLs must be unique within an attempt")
                urls.add(article.url)
                try:
                    url_biz = observed_article_biz(article.url)
                except (
                    DiscoveryIdentityMismatch,
                    DiscoveryIdentityUnverified,
                    DiscoveryResponseInvalid,
                ) as exc:
                    conn.rollback()
                    raise ValueError(
                        "probe candidate URL cannot prove the configured public biz"
                    ) from exc
                if (
                    article.account_name != account_name
                    or article.biz != biz
                    or url_biz != biz
                ):
                    conn.rollback()
                    raise ValueError(
                        "probe candidate identity contradicts its provisional target"
                    )

            terminal_state = state or DiscoveryState.SUCCESS
            if terminal_state in {
                DiscoveryState.SUCCESS_NO_NEW_SHADOW_CANDIDATES,
                DiscoveryState.SUCCESS_WITH_NEW_SHADOW_CANDIDATES,
            }:
                terminal_state = DiscoveryState.SUCCESS
            platform_error_ret, platform_error_ret_origin = _platform_error_fields(
                terminal_state, platform_error_ret
            )
            if target_identity_evidence is None:
                target_identity_evidence = (
                    TargetIdentityEvidence.ARTICLE_URL_PUBLIC_BIZ_VERIFIED
                    if terminal_state is DiscoveryState.SUCCESS
                    else TargetIdentityEvidence.NOT_OBSERVED
                )
            allowed_evidence = {
                DiscoveryState.SUCCESS: {
                    TargetIdentityEvidence.ARTICLE_URL_PUBLIC_BIZ_VERIFIED,
                },
                DiscoveryState.IDENTITY_UNVERIFIED: {
                    TargetIdentityEvidence.EMPTY_ARTICLE_LIST,
                    TargetIdentityEvidence.ARTICLE_URL_PUBLIC_BIZ_UNAVAILABLE,
                },
                DiscoveryState.IDENTITY_MISMATCH: {
                    TargetIdentityEvidence.ARTICLE_URL_PUBLIC_BIZ_MISMATCH,
                },
                DiscoveryState.AUTH_REQUIRED: {TargetIdentityEvidence.NOT_OBSERVED},
                DiscoveryState.RATE_LIMITED: {TargetIdentityEvidence.NOT_OBSERVED},
                DiscoveryState.PLATFORM_REJECTED: {
                    TargetIdentityEvidence.NOT_OBSERVED
                },
                DiscoveryState.REQUEST_FAILED: {TargetIdentityEvidence.NOT_OBSERVED},
                DiscoveryState.RESPONSE_INVALID: {TargetIdentityEvidence.NOT_OBSERVED},
            }
            if target_identity_evidence not in allowed_evidence.get(terminal_state, set()):
                conn.rollback()
                raise ValueError("probe outcome contradicts its target identity evidence")
            if terminal_state is DiscoveryState.SUCCESS and not candidates:
                conn.rollback()
                raise ValueError("a verified successful probe must contain at least one article")
            if terminal_state is not DiscoveryState.SUCCESS and candidates:
                conn.rollback()
                raise ValueError("a failed probe cannot contain candidates")

            existing_urls = {
                str(existing["url"])
                for existing in conn.execute(
                    "SELECT DISTINCT c.url FROM discovery_attempt_candidates c "
                    "JOIN discovery_attempts a ON a.id=c.probe_attempt_id "
                    "LEFT JOIN identity_resolution_attempts r "
                    "ON r.id=a.identity_resolution_id "
                    "WHERE COALESCE(r.configured_public_biz, "
                    "a.legacy_target_public_biz)=? AND c.probe_attempt_id<>?",
                    (biz, attempt_id),
                )
            }
            new_count = sum(article.url not in existing_urls for article in candidates)
            if terminal_state not in FAILURE_STATES | {DiscoveryState.SUCCESS}:
                conn.rollback()
                raise ValueError("probe completion has an unsupported terminal state")
            conn.executemany(
                """
                INSERT INTO discovery_attempt_candidates(
                  probe_attempt_id, url, title, author, published_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        attempt_id,
                        article.url,
                        article.title,
                        article.author,
                        _iso(article.published_at),
                    )
                    for article in candidates
                ],
            )
            conn.execute(
                "UPDATE discovery_attempts SET finished_at=?, outcome=?, "
                "target_identity_evidence=?, platform_error_ret=?, "
                "platform_error_ret_origin=? WHERE id=?",
                (
                    _iso(finished_at),
                    terminal_state.value,
                    target_identity_evidence.value,
                    platform_error_ret,
                    platform_error_ret_origin,
                    attempt_id,
                ),
            )
            if terminal_state is DiscoveryState.IDENTITY_MISMATCH:
                conn.execute(
                    """
                    UPDATE identity_resolution_attempts
                    SET invalidated_at=?, invalidation_reason='article_url_biz_mismatch'
                    WHERE id=? AND invalidated_at IS NULL
                    """,
                    (_iso(finished_at), int(row["identity_resolution_id"])),
                )
            conn.commit()
        return ProbeCompletion(
            attempt_id=attempt_id,
            state=terminal_state,
            target_identity_evidence=target_identity_evidence,
            returned_article_count=len(candidates),
            new_candidate_count=new_count,
        )

    def attempt(self, attempt_id: int) -> DiscoveryAttempt | None:
        if not self.path.exists():
            return None
        with self.readonly_connect() as conn:
            row = conn.execute(
                """
                SELECT a.*,
                       COALESCE(r.configured_account_name, a.legacy_target_account_name)
                         AS target_account_name,
                       COALESCE(r.configured_public_biz, a.legacy_target_public_biz)
                         AS target_public_biz
                FROM discovery_attempts a
                LEFT JOIN identity_resolution_attempts r ON r.id=a.identity_resolution_id
                WHERE a.id=?
                """,
                (attempt_id,),
            ).fetchone()
            if row is None:
                return None
            candidate_rows = conn.execute(
                """
                SELECT title, url, author, published_at
                FROM discovery_attempt_candidates WHERE probe_attempt_id=? ORDER BY rowid
                """,
                (attempt_id,),
            ).fetchall()
        state = DiscoveryState(str(row["outcome"]))
        return DiscoveryAttempt(
            started_at=_datetime(str(row["started_at"])),
            finished_at=(
                _datetime(str(row["finished_at"])) if row["finished_at"] is not None else None
            ),
            state=state,
            kind=AttemptKind.PROBE,
            account_results=(
                AccountResult(
                    account_name=str(row["target_account_name"]),
                    biz=str(row["target_public_biz"]),
                    state=state,
                ),
            ),
            candidate_snapshot=tuple(
                DiscoveryArticle(
                    account_name=str(row["target_account_name"]),
                    biz=str(row["target_public_biz"]),
                    title=str(candidate["title"]),
                    url=str(candidate["url"]),
                    author=str(candidate["author"]),
                    published_at=_datetime(str(candidate["published_at"])),
                )
                for candidate in candidate_rows
            ),
            target_identity_evidence=TargetIdentityEvidence(
                str(row["target_identity_evidence"])
            ),
            requested_page_size=(
                int(row["requested_page_size"])
                if row["requested_page_size"] is not None
                else None
            ),
            requested_page_size_origin=str(row["requested_page_size_origin"]),
            identity_resolution_id=(
                int(row["identity_resolution_id"])
                if row["identity_resolution_id"] is not None
                else None
            ),
            identity_resolution_origin=str(row["identity_resolution_origin"]),
            platform_error_ret=(
                int(row["platform_error_ret"])
                if row["platform_error_ret"] is not None
                else None
            ),
            platform_error_ret_origin=str(row["platform_error_ret_origin"]),
        )

    def latest_attempt(self) -> DiscoveryAttempt | None:
        attempt_id = self.latest_attempt_id()
        return self.attempt(attempt_id) if attempt_id is not None else None

    def latest_attempt_id(self) -> int | None:
        if not self.path.exists():
            return None
        with self.readonly_connect() as conn:
            row = conn.execute(
                "SELECT id FROM discovery_attempts ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return int(row["id"]) if row is not None else None

    def latest_successful_attempts(self) -> tuple[tuple[int, str, str], ...]:
        if not self.path.exists():
            return ()
        with self.readonly_connect() as conn:
            rows = conn.execute(
                """
                SELECT a.id,
                       COALESCE(r.configured_account_name, a.legacy_target_account_name)
                         AS target_account_name,
                       COALESCE(r.configured_public_biz, a.legacy_target_public_biz)
                         AS target_public_biz
                FROM discovery_attempts a
                LEFT JOIN identity_resolution_attempts r ON r.id=a.identity_resolution_id
                WHERE a.outcome=? ORDER BY a.id DESC
                """,
                (DiscoveryState.SUCCESS.value,),
            ).fetchall()
        latest: dict[tuple[str, str], int] = {}
        for row in rows:
            target = (str(row["target_account_name"]), str(row["target_public_biz"]))
            latest.setdefault(target, int(row["id"]))
        return tuple(
            (attempt_id, name, biz) for (name, biz), attempt_id in latest.items()
        )

    def attempt_identity_issue(self, attempt_id: int) -> str | None:
        if not self.path.exists():
            return "the shadow state database does not exist"
        with self.readonly_connect() as conn:
            row = conn.execute(
                """
                SELECT a.identity_resolution_origin, a.identity_resolution_id,
                       a.outcome, a.target_identity_evidence,
                       r.id AS resolution_id, r.outcome AS resolution_outcome,
                       r.configured_account_name, r.configured_public_biz,
                       r.provisional_match_origin, r.invalidated_at,
                       r.superseding_resolution_id,
                       (SELECT COUNT(*) FROM discovery_attempt_candidates c
                        WHERE c.probe_attempt_id=a.id) AS candidate_count
                FROM discovery_attempts a
                LEFT JOIN identity_resolution_attempts r
                  ON r.id=a.identity_resolution_id
                WHERE a.id=?
                """,
                (attempt_id,),
            ).fetchone()
        if row is None:
            return "the probe attempt does not exist"
        if row["outcome"] != DiscoveryState.SUCCESS.value:
            return "the probe attempt did not complete successfully"
        if (
            row["target_identity_evidence"]
            != TargetIdentityEvidence.ARTICLE_URL_PUBLIC_BIZ_VERIFIED.value
        ):
            return "the probe attempt has no persisted article-URL public-biz verification"
        if row["identity_resolution_origin"] != "provisional_searchbiz_match":
            return "the attempt predates provisional searchbiz identity resolution"
        if row["identity_resolution_id"] is None:
            return "the attempt has no provisional searchbiz identity relation"
        if row["resolution_id"] is None:
            return "the attempt's provisional searchbiz identity relation does not exist"
        if row["resolution_outcome"] != IdentityResolutionState.PROVISIONAL_MATCH.value:
            return "the attempt does not reference a provisional searchbiz match"
        if row["provisional_match_origin"] != "searchbiz_unique_normalized_name":
            return "the attempt predates the unique normalized-name selection contract"
        if row["invalidated_at"] is not None:
            return "the attempt used a provisional mapping that was later invalidated"
        if row["superseding_resolution_id"] is not None:
            return "the attempt used a provisional mapping that was later superseded"
        if int(row["candidate_count"]) < 1:
            return "the verified probe has no stored article candidate"
        with self.readonly_connect() as conn:
            candidate_rows = conn.execute(
                "SELECT url FROM discovery_attempt_candidates WHERE probe_attempt_id=?",
                (attempt_id,),
            ).fetchall()
        try:
            if any(
                observed_article_biz(str(candidate["url"]))
                != str(row["configured_public_biz"])
                for candidate in candidate_rows
            ):
                return "a stored article URL contradicts the configured public biz"
        except (
            DiscoveryIdentityMismatch,
            DiscoveryIdentityUnverified,
            DiscoveryResponseInvalid,
        ):
            return "a stored article URL cannot prove the configured public biz"
        return None

    def latest_identity_verified_successful_probe_id(self) -> int | None:
        if not self.path.exists():
            return None
        with self.readonly_connect() as conn:
            rows = conn.execute(
                "SELECT id FROM discovery_attempts WHERE outcome=? "
                "ORDER BY id DESC",
                (DiscoveryState.SUCCESS.value,),
            ).fetchall()
        for row in rows:
            attempt_id = int(row["id"])
            if self.attempt_identity_issue(attempt_id) is None:
                return attempt_id
        return None

    def attempt_candidates(self, attempt_id: int) -> list[DiscoveryArticle]:
        attempt = self.attempt(attempt_id)
        return list(attempt.candidate_snapshot) if attempt is not None else []

    def candidate_urls(self, public_biz: str, *, attempt_id: int | None = None) -> tuple[str, ...]:
        if not self.path.exists():
            return ()
        with self.readonly_connect() as conn:
            if attempt_id is None:
                rows = conn.execute(
                    "SELECT DISTINCT c.url FROM discovery_attempt_candidates c "
                    "JOIN discovery_attempts a ON a.id=c.probe_attempt_id "
                    "LEFT JOIN identity_resolution_attempts r ON r.id=a.identity_resolution_id "
                    "WHERE COALESCE(r.configured_public_biz, a.legacy_target_public_biz)=? "
                    "ORDER BY c.url",
                    (public_biz,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT c.url FROM discovery_attempt_candidates c "
                    "JOIN discovery_attempts a ON a.id=c.probe_attempt_id "
                    "LEFT JOIN identity_resolution_attempts r ON r.id=a.identity_resolution_id "
                    "WHERE c.probe_attempt_id=? "
                    "AND COALESCE(r.configured_public_biz, a.legacy_target_public_biz)=? "
                    "ORDER BY c.url",
                    (attempt_id, public_biz),
                ).fetchall()
        return tuple(str(row["url"]) for row in rows)
