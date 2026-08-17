from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from airadar.wechat_discovery import store as wechat_store
from airadar.wechat_discovery.config import (
    DEFAULT_CONFIG_PATH,
    DiscoveryConfig,
    load_discovery_config,
)
from airadar.wechat_discovery.models import (
    AccountConfig,
    AccountResult,
    AttemptKind,
    BackendRequest,
    DiscoveryArticle,
    DiscoveryAttempt,
    DiscoveryGateState,
    DiscoveryState,
    IdentityResolutionState,
    TargetIdentityEvidence,
)
from airadar.wechat_discovery.protocol import (
    ProvisionalIdentity,
    verify_account_identity,
)
from airadar.wechat_discovery.shadow import (
    ShadowNotComparable,
    canonical_wechat_article_key,
    compare_shadow,
    compare_shadow_window,
)
from airadar.wechat_discovery.status import (
    backend_request_blocked_until,
    effective_status,
    manual_probe_blocked_until,
)
from airadar.wechat_discovery.store import DiscoveryStore, DiscoveryStoreVersionError


def _create_v7_state_with_verified_success(
    path: Path,
    *,
    supersede_before_completion: bool = False,
    migration_only_evidence: bool = False,
) -> None:
    now = datetime(2026, 8, 14, tzinfo=UTC).isoformat()
    finished = datetime(2026, 8, 14, 0, 1, tzinfo=UTC).isoformat()
    with sqlite3.connect(path) as conn:
        DiscoveryStore._initialize_schema(conn, schema=wechat_store._SCHEMA_V7)
        conn.execute(
            """
            INSERT INTO identity_resolution_attempts(
              id, started_at, finished_at, configured_account_name,
              configured_public_biz, outcome, provisional_match_origin,
              observed_account_name, provisional_fakeid
            ) VALUES (1, ?, ?, 'A', 'Qml6QQ==', 'provisional_match',
              'searchbiz_unique_normalized_name', 'A', 'private-fakeid')
            """,
            (now, finished),
        )
        conn.execute(
            """
            INSERT INTO discovery_attempts(
              id, started_at, finished_at, outcome,
              legacy_target_account_name, legacy_target_public_biz,
              requested_page_size, requested_page_size_origin,
              identity_resolution_id, identity_resolution_origin,
              target_identity_evidence
            ) VALUES (1, ?, NULL, 'reserved', NULL, NULL, 5, 'recorded', 1,
              'provisional_searchbiz_match', 'pending')
            """,
            (now,),
        )
        conn.execute(
            """
            INSERT INTO discovery_attempt_candidates(
              probe_attempt_id, url, title, author, published_at
            ) VALUES (1,
              'https://mp.weixin.qq.com/s?__biz=Qml6QQ%3D%3D&mid=1&idx=1&sn=ok',
              'candidate', 'A', ?)
            """,
            (now,),
        )
        if supersede_before_completion:
            conn.execute(
                """
                INSERT INTO identity_resolution_attempts(
                  id, started_at, finished_at, configured_account_name,
                  configured_public_biz, outcome, provisional_match_origin,
                  observed_account_name, provisional_fakeid
                ) VALUES (2, ?, ?, 'A', 'Qml6QQ==', 'provisional_match',
                  'searchbiz_unique_normalized_name', 'A', 'new-private-fakeid')
                """,
                (now, finished),
            )
            conn.execute(
                "UPDATE identity_resolution_attempts SET superseding_resolution_id=2 WHERE id=1"
            )
        conn.execute(
            """
            UPDATE discovery_attempts
            SET finished_at=?, outcome='success', target_identity_evidence=?
            WHERE id=1
            """,
            (
                finished,
                (
                    "predates_v7_verification"
                    if migration_only_evidence
                    else "article_url_public_biz_verified"
                ),
            ),
        )
        conn.commit()


def _write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "wechat-discovery.toml"
    path.write_text(text, encoding="utf-8")
    return path


def _record_probe(
    store: DiscoveryStore,
    *,
    started_at: datetime,
    finished_at: datetime,
    candidates: tuple[DiscoveryArticle, ...] = (),
    state: DiscoveryState | None = None,
    account_name: str = "A",
    biz: str = "Qml6QQ==",
    requested_page_size: int = 5,
) -> tuple[int, int]:
    account = AccountConfig(account_name, biz)
    config = DiscoveryConfig(2, True, timedelta(0), (account,))
    resolution_id = store.reserve_identity_resolution(
        account, config=config, started_at=started_at - timedelta(seconds=2)
    )
    store.complete_identity_resolution(
        resolution_id,
        state=IdentityResolutionState.PROVISIONAL_MATCH,
        finished_at=started_at - timedelta(seconds=1),
        provisional=ProvisionalIdentity(account_name, "fixture-fakeid"),
    )
    reservation = store.reserve_probe(
        account,
        config=config,
        started_at=started_at,
        requested_page_size=requested_page_size,
    )
    completion = store.complete_probe(
        reservation.attempt_id,
        finished_at=finished_at,
        candidates=candidates,
        state=state,
        target_identity_evidence=(
            TargetIdentityEvidence.ARTICLE_URL_PUBLIC_BIZ_VERIFIED
            if (state is None or state is DiscoveryState.SUCCESS)
            else TargetIdentityEvidence.NOT_OBSERVED
        ),
    )
    return reservation.attempt_id, completion.new_candidate_count


def _create_v4_state(
    path: Path,
    *,
    consumed_relation: bool = False,
    contradictory_relation: bool = False,
) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE discovery_attempts (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              started_at TEXT NOT NULL,
              finished_at TEXT NOT NULL,
              kind TEXT NOT NULL,
              state TEXT NOT NULL,
              change_basis TEXT NOT NULL,
              requested_page_size INTEGER,
              requested_page_size_origin TEXT NOT NULL,
              identity_resolution_id INTEGER,
              identity_resolution_origin TEXT NOT NULL
            );
            CREATE TABLE identity_resolution_attempts (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              started_at TEXT NOT NULL,
              finished_at TEXT NOT NULL,
              account_name TEXT NOT NULL,
              biz TEXT NOT NULL,
              state TEXT NOT NULL,
              observed_account_name TEXT,
              resolved_fakeid TEXT,
              consumed_at TEXT,
              consuming_probe_attempt_id INTEGER,
              invalidated_at TEXT,
              invalidation_reason TEXT,
              superseded_at TEXT,
              superseding_resolution_id INTEGER
            );
            CREATE TABLE discovery_account_results (
              attempt_id INTEGER NOT NULL,
              account_name TEXT NOT NULL,
              biz TEXT NOT NULL,
              state TEXT NOT NULL,
              PRIMARY KEY (attempt_id, biz)
            );
            CREATE TABLE discovery_candidates (
              biz TEXT NOT NULL, url TEXT NOT NULL, declared_account_name TEXT NOT NULL,
              title TEXT NOT NULL, author TEXT NOT NULL, published_at TEXT NOT NULL,
              first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
              PRIMARY KEY (biz, url)
            );
            CREATE TABLE discovery_attempt_candidates (
              attempt_id INTEGER NOT NULL, biz TEXT NOT NULL, url TEXT NOT NULL,
              declared_account_name TEXT NOT NULL, title TEXT NOT NULL,
              author TEXT NOT NULL, published_at TEXT NOT NULL,
              new_to_shadow_state INTEGER NOT NULL,
              PRIMARY KEY (attempt_id, biz, url)
            );
            INSERT INTO identity_resolution_attempts VALUES (
              1, '2026-08-13T00:00:00+00:00', '2026-08-13T00:01:00+00:00',
              'A', 'Qml6QQ==', 'resolved', 'A', 'old-fakeid',
              NULL, NULL, NULL, NULL, NULL, NULL
            );
            PRAGMA user_version=4;
            """
        )
        if consumed_relation or contradictory_relation:
            conn.execute(
                "UPDATE identity_resolution_attempts SET consumed_at=?, "
                "consuming_probe_attempt_id=1 WHERE id=1",
                ("2026-08-13T00:02:00+00:00",),
            )
            conn.execute(
                """
                INSERT INTO discovery_attempts VALUES (
                  1, '2026-08-13T00:02:00+00:00', '2026-08-13T00:03:00+00:00',
                  'probe', 'success_no_new_shadow_candidates', 'shadow_state_url_set',
                  5, 'recorded', 1, 'verified_resolution'
                )
                """
            )
            conn.execute(
                "INSERT INTO discovery_account_results VALUES (?, ?, ?, ?)",
                (
                    1,
                    "wrong-account" if contradictory_relation else "A",
                    "Qml6QQ==",
                    "success_no_new_shadow_candidates",
                ),
            )


def _create_weak_v5_state(
    path: Path,
    *,
    invalid_resolved: bool = False,
    contradictory_verified_relation: bool = False,
    reverse_probe_time: bool = False,
) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE identity_resolution_attempts (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              started_at TEXT NOT NULL, finished_at TEXT,
              configured_account_name TEXT NOT NULL,
              configured_public_biz TEXT NOT NULL,
              outcome TEXT NOT NULL, verification_basis TEXT NOT NULL,
              observed_account_name TEXT, observed_public_biz TEXT,
              observed_public_biz_origin TEXT NOT NULL, resolved_fakeid TEXT,
              invalidated_at TEXT, invalidation_reason TEXT,
              superseding_resolution_id INTEGER
            );
            CREATE TABLE discovery_attempts (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              started_at TEXT NOT NULL, finished_at TEXT,
              kind TEXT NOT NULL, outcome TEXT NOT NULL,
              target_account_name TEXT NOT NULL, target_public_biz TEXT NOT NULL,
              change_basis TEXT NOT NULL, requested_page_size INTEGER,
              requested_page_size_origin TEXT NOT NULL,
              identity_resolution_id INTEGER,
              identity_resolution_origin TEXT NOT NULL
            );
            CREATE UNIQUE INDEX one_probe_per_identity_resolution
              ON discovery_attempts(identity_resolution_id)
              WHERE identity_resolution_id IS NOT NULL;
            CREATE TABLE discovery_attempt_candidates (
              attempt_id INTEGER NOT NULL, observed_public_biz TEXT NOT NULL,
              url TEXT NOT NULL, title TEXT NOT NULL, author TEXT NOT NULL,
              published_at TEXT NOT NULL, PRIMARY KEY (attempt_id, url)
            );
            INSERT INTO discovery_attempts VALUES (
              1, '2026-08-13T08:00:00+08:00', '2026-08-13T08:01:00+08:00',
              'probe', 'rate_limited', 'A', 'Qml6QQ==', 'shadow_state_url_set',
              NULL, 'predates_persistence', NULL, 'predates_resolution'
            );
            PRAGMA user_version=5;
            """
        )
        if invalid_resolved:
            conn.execute(
                """
                INSERT INTO identity_resolution_attempts VALUES (
                  1, '2026-08-13T00:00:00+00:00',
                  '2026-08-13T00:01:00+00:00', 'A', 'Qml6QQ==', 'resolved',
                  'normalized_account_name_and_public_biz', NULL, NULL,
                  'recorded', NULL, NULL, NULL, NULL
                )
                """
            )
        if contradictory_verified_relation:
            conn.execute(
                """
                INSERT INTO identity_resolution_attempts VALUES (
                  1, '2026-08-13T00:00:00+00:00',
                  '2026-08-13T00:01:00+00:00', 'A', 'Qml6QQ==', 'no_match',
                  'normalized_account_name_and_public_biz', NULL, NULL,
                  'not_observed', NULL, NULL, NULL, NULL
                )
                """
            )
            conn.execute(
                """
                UPDATE discovery_attempts
                SET identity_resolution_id=1,
                    identity_resolution_origin='verified_resolution'
                WHERE id=1
                """
            )
        if reverse_probe_time:
            conn.execute(
                "UPDATE discovery_attempts SET finished_at=? WHERE id=1",
                ("2026-08-12T23:59:00+00:00",),
            )


def _create_v6_success_state(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE identity_resolution_attempts (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              started_at TEXT NOT NULL,
              finished_at TEXT,
              configured_account_name TEXT NOT NULL,
              configured_public_biz TEXT NOT NULL,
              outcome TEXT NOT NULL,
              verification_basis TEXT NOT NULL,
              observed_account_name TEXT,
              public_biz_match_origin TEXT NOT NULL,
              resolved_fakeid TEXT,
              invalidated_at TEXT,
              invalidation_reason TEXT,
              superseding_resolution_id INTEGER
            );
            CREATE TABLE discovery_attempts (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              started_at TEXT NOT NULL,
              finished_at TEXT,
              outcome TEXT NOT NULL,
              legacy_target_account_name TEXT,
              legacy_target_public_biz TEXT,
              requested_page_size INTEGER,
              requested_page_size_origin TEXT NOT NULL,
              identity_resolution_id INTEGER,
              identity_resolution_origin TEXT NOT NULL
            );
            CREATE UNIQUE INDEX one_probe_per_identity_resolution
              ON discovery_attempts(identity_resolution_id)
              WHERE identity_resolution_id IS NOT NULL;
            CREATE TABLE discovery_attempt_candidates (
              probe_attempt_id INTEGER NOT NULL,
              url TEXT NOT NULL,
              title TEXT NOT NULL,
              author TEXT NOT NULL,
              published_at TEXT NOT NULL,
              PRIMARY KEY (probe_attempt_id, url)
            );
            INSERT INTO identity_resolution_attempts VALUES (
              1, '2026-08-13T00:00:00+00:00', '2026-08-13T00:01:00+00:00',
              'A', 'Qml6QQ==', 'resolved',
              'normalized_account_name_and_public_biz', 'A', 'recorded',
              'legacy-fakeid', NULL, NULL, NULL
            );
            INSERT INTO discovery_attempts VALUES (
              1, '2026-08-13T00:02:00+00:00', '2026-08-13T00:03:00+00:00',
              'success', NULL, NULL, 5, 'recorded', 1, 'verified_resolution'
            );
            INSERT INTO discovery_attempt_candidates VALUES (
              1,
              'https://mp.weixin.qq.com/s?__biz=Qml6QQ%3D%3D&mid=1&idx=1&sn=legacy',
              'legacy title', 'legacy author', '2026-08-13T00:02:30+00:00'
            );
            PRAGMA user_version=6;
            """
        )


def test_load_config_accepts_disabled_known_accounts_and_rejects_duplicate_biz(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """
version = 3
manual_backend_requests_enabled = false
refresh_interval_minutes = 120

[[account]]
name = "测试号 A"
public_biz = "TXpBMU1ERT0="
seed_urls = ["https://mp.weixin.qq.com/s/seed-a"]
""",
    )

    config = load_discovery_config(path)

    assert config.manual_backend_requests_enabled is False
    assert config.refresh_interval == timedelta(minutes=120)
    assert config.accounts[0].name == "测试号 A"

    duplicate = _write_config(
        tmp_path,
        """
version = 3
manual_backend_requests_enabled = false
refresh_interval_minutes = 120
[[account]]
name = "A"
public_biz = "TXpBMU1ERT0="
seed_urls = ["https://mp.weixin.qq.com/s/a"]
[[account]]
name = "B"
public_biz = "TXpBMU1ERT0="
seed_urls = ["https://mp.weixin.qq.com/s/b"]
""",
    )
    with pytest.raises(ValueError, match="duplicate public_biz"):
        load_discovery_config(duplicate)

    unknown = _write_config(
        tmp_path,
        """
version = 3
manual_backend_requests_enabled = false
refresh_interval_minutes = 120
future_key = true
[[account]]
name = "A"
public_biz = "Qml6QQ=="
""",
    )
    with pytest.raises(ValueError, match="unknown WeChat discovery config key"):
        load_discovery_config(unknown)

    future = _write_config(
        tmp_path,
        """
version = 3
manual_backend_requests_enabled = false
refresh_interval_minutes = 120
[[account]]
name = "A"
public_biz = "Qml6QQ=="
seed_urls = ["https://mp.weixin.qq.com/s/a"]
identity = { seed_url = "https://mp.weixin.qq.com/s/a", observed_name = "A", observed_public_biz = "Qml6QQ==", observed_at = "2999-01-01" }
""",
    )
    with pytest.raises(ValueError, match="in the future"):
        load_discovery_config(future)


def test_repository_discovery_config_uses_daily_shadow_probe_interval() -> None:
    config = load_discovery_config(DEFAULT_CONFIG_PATH)

    assert config.version == 3
    assert config.manual_backend_requests_enabled is False
    assert config.refresh_interval == timedelta(days=1)
    assert config.accounts[0].public_biz
    assert config.accounts[0].identity_proof is not None
    assert config.accounts[0].identity_proof.observed_public_biz


def test_repository_discovery_config_has_reviewed_identity_for_every_account() -> None:
    config = load_discovery_config(DEFAULT_CONFIG_PATH)

    assert len(config.accounts) == 14
    for account in config.accounts:
        assert account.identity_proof is not None, account.name
        verify_account_identity(account)


def test_shadow_comparison_is_per_account_and_does_not_claim_global_completeness() -> None:
    now = datetime(2026, 8, 13, tzinfo=UTC)
    candidates = [
        DiscoveryArticle("A", "Qml6QQ==", "候选共有", "https://mp.weixin.qq.com/s/shared", "A", now),
        DiscoveryArticle("A", "Qml6QQ==", "候选独有", "https://mp.weixin.qq.com/s/candidate", "A", now),
    ]
    baseline = [
        DiscoveryArticle("A", "Qml6QQ==", "基准共有", "https://mp.weixin.qq.com/s/shared", "A", now),
        DiscoveryArticle("A", "Qml6QQ==", "基准独有", "https://mp.weixin.qq.com/s/missing", "A", now),
    ]

    comparison = compare_shadow(
        account_name="A",
        biz="Qml6QQ==",
        candidates=candidates,
        baseline=baseline,
        observed_at=now,
    )

    assert comparison.missing_baseline_urls == ("https://mp.weixin.qq.com/s/missing",)
    assert comparison.candidate_only_urls == ("https://mp.weixin.qq.com/s/candidate",)
    assert comparison.covered is False
    assert not hasattr(comparison, "complete")


def test_wechat_article_key_normalizes_only_proven_identity_forms() -> None:
    assert canonical_wechat_article_key(
        "http://mp.weixin.qq.com/s/article-id/?utm_source=feed#fragment"
    ) == ("short", "article-id")
    assert canonical_wechat_article_key("https://mp.weixin.qq.com/s/article-id") == (
        "short",
        "article-id",
    )
    assert canonical_wechat_article_key("https://mp.weixin.qq.com/s/other-id") != (
        "short",
        "article-id",
    )
    assert canonical_wechat_article_key(
        "https://mp.weixin.qq.com/s?__biz=abc&mid=1&idx=2&sn=xyz&utm_source=feed"
    ) == ("legacy", "abc", "1", "2", "xyz")
    assert canonical_wechat_article_key(
        "https://mp.weixin.qq.com/s?__biz=abc&mid=1&idx=3&sn=xyz"
    ) != ("legacy", "abc", "1", "2", "xyz")
    assert canonical_wechat_article_key("https://mp.weixin.qq.com/s?__biz=abc&mid=1") is None
    assert canonical_wechat_article_key("https://example.com/s/article-id") is None


def test_window_comparison_rejects_unproven_cross_family_identity() -> None:
    now = datetime(2026, 8, 13, 3, tzinfo=UTC)
    candidate = DiscoveryArticle(
        "A", "Qml6QQ==", "候选", "https://mp.weixin.qq.com/s/short-id", "A", now
    )
    baseline = DiscoveryArticle(
        "A",
        "Qml6QQ==",
        "基准",
        "https://mp.weixin.qq.com/s?__biz=abc&mid=1&idx=1&sn=xyz",
        "A",
        now,
    )
    attempt = DiscoveryAttempt(
        now,
        now + timedelta(minutes=1),
        DiscoveryState.SUCCESS_WITH_NEW_SHADOW_CANDIDATES,
        AttemptKind.PROBE,
        (AccountResult("A", "Qml6QQ==", DiscoveryState.SUCCESS_WITH_NEW_SHADOW_CANDIDATES),),
        (candidate,),
        5,
        "recorded",
        1,
        "provisional_searchbiz_match",
        TargetIdentityEvidence.ARTICLE_URL_PUBLIC_BIZ_VERIFIED,
    )

    with pytest.raises(ShadowNotComparable, match="identity forms differ"):
        compare_shadow_window(
            account_name="A",
            biz="Qml6QQ==",
            baseline=[baseline],
            since=now - timedelta(hours=1),
            attempt=attempt,
        )


def test_store_persists_attempt_accounts_candidates_and_comparison_outside_items(tmp_path: Path) -> None:
    now = datetime(2026, 8, 13, tzinfo=UTC)
    store = DiscoveryStore(tmp_path / "wechat-discovery.db")
    article = DiscoveryArticle(
        "A",
        "Qml6QQ==",
        "标题",
        "https://mp.weixin.qq.com/s?__biz=Qml6QQ%3D%3D&mid=1&idx=1&sn=article",
        "A",
        now,
    )
    attempt_id, new_count = _record_probe(
        store,
        started_at=now,
        finished_at=now + timedelta(seconds=2),
        candidates=(article,),
    )
    latest = store.latest_attempt()

    assert attempt_id == 1
    assert new_count == 1
    assert latest is not None
    assert latest.candidate_snapshot == (article,)
    assert store.candidate_urls("Qml6QQ==") == (article.url,)
    with store.connect() as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='items'").fetchone() is None
        serialized = " ".join(
            str(value)
            for row in conn.execute("SELECT * FROM discovery_attempts")
            for value in row
        )
    assert "cookie" not in serialized.lower()
    assert "token" not in serialized.lower()


def test_store_preserves_each_attempt_candidate_set_and_rejects_unknown_schema(tmp_path: Path) -> None:
    now = datetime(2026, 8, 13, tzinfo=UTC)
    path = tmp_path / "wechat-discovery.db"
    store = DiscoveryStore(path)
    first_article = DiscoveryArticle(
        "A",
        "Qml6QQ==",
        "一",
        "https://mp.weixin.qq.com/s?__biz=Qml6QQ%3D%3D&mid=1&idx=1&sn=one",
        "A",
        now,
    )
    first_id, first_new = _record_probe(
        store, started_at=now, finished_at=now, candidates=(first_article,)
    )
    second_id, second_new = _record_probe(
        store,
        started_at=now + timedelta(seconds=3),
        finished_at=now + timedelta(seconds=3),
        candidates=(first_article,),
    )

    assert (first_new, second_new) == (1, 0)
    assert store.candidate_urls("Qml6QQ==", attempt_id=first_id) == (
        first_article.url,
    )
    assert store.candidate_urls("Qml6QQ==", attempt_id=second_id) == (
        first_article.url,
    )
    with store.connect() as conn:
        snapshot = conn.execute(
            "SELECT title, author, published_at FROM discovery_attempt_candidates WHERE probe_attempt_id=?",
            (first_id,),
        ).fetchone()
        assert tuple(snapshot) == ("一", "A", now.isoformat())
        assert conn.execute("PRAGMA user_version").fetchone()[0] == wechat_store.SCHEMA_VERSION
        conn.execute("PRAGMA user_version=99")
    with pytest.raises(DiscoveryStoreVersionError, match="schema version: 99"):
        store.latest_attempt()


def test_store_migrates_v1_attempts_without_inventing_requested_page_size(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE discovery_attempts (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              started_at TEXT NOT NULL,
              finished_at TEXT NOT NULL,
              kind TEXT NOT NULL,
              state TEXT NOT NULL,
              change_basis TEXT NOT NULL
            );
            CREATE TABLE discovery_account_results (
              attempt_id INTEGER NOT NULL,
              account_name TEXT NOT NULL,
              biz TEXT NOT NULL,
              state TEXT NOT NULL,
              PRIMARY KEY (attempt_id, biz)
            );
            CREATE TABLE discovery_candidates (
              biz TEXT NOT NULL, url TEXT NOT NULL, declared_account_name TEXT NOT NULL,
              title TEXT NOT NULL, author TEXT NOT NULL, published_at TEXT NOT NULL,
              first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
              PRIMARY KEY (biz, url)
            );
            CREATE TABLE discovery_attempt_candidates (
              attempt_id INTEGER NOT NULL, biz TEXT NOT NULL, url TEXT NOT NULL,
              declared_account_name TEXT NOT NULL, title TEXT NOT NULL, author TEXT NOT NULL,
              published_at TEXT NOT NULL, new_to_shadow_state INTEGER NOT NULL,
              PRIMARY KEY (attempt_id, biz, url)
            );
            INSERT INTO discovery_attempts VALUES (
              1, '2026-08-13T00:00:00+00:00', '2026-08-13T00:01:00+00:00',
              'probe', 'rate_limited', 'shadow_state_url_set'
            );
            INSERT INTO discovery_account_results VALUES (
              1, 'A', 'Qml6QQ==', 'rate_limited'
            );
            PRAGMA user_version=1;
            """
        )

    store = DiscoveryStore(path)
    store.migrate()
    attempt = store.attempt(1)

    assert attempt is not None
    assert attempt.requested_page_size is None
    assert attempt.requested_page_size_origin == "predates_persistence"
    with store.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == wechat_store.SCHEMA_VERSION
        row = conn.execute(
            "SELECT requested_page_size, requested_page_size_origin "
            "FROM discovery_attempts WHERE id=1"
        ).fetchone()
        assert tuple(row) == (None, "predates_persistence")


def test_store_recovers_interrupted_v1_page_size_migration(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE discovery_attempts (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              started_at TEXT NOT NULL,
              finished_at TEXT NOT NULL,
              kind TEXT NOT NULL,
              state TEXT NOT NULL,
              change_basis TEXT NOT NULL,
              requested_page_size INTEGER
            );
            PRAGMA user_version=1;
            """
        )

    store = DiscoveryStore(path)
    with store.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == wechat_store.SCHEMA_VERSION
        columns = {row[1] for row in conn.execute("PRAGMA table_info(discovery_attempts)")}
        assert "requested_page_size" in columns
        assert "requested_page_size_origin" in columns


def test_store_migrates_v2_requested_count_without_losing_recorded_value(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE discovery_attempts (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              started_at TEXT NOT NULL,
              finished_at TEXT NOT NULL,
              kind TEXT NOT NULL,
              state TEXT NOT NULL,
              change_basis TEXT NOT NULL,
              requested_count INTEGER
            );
            CREATE TABLE discovery_account_results (
              attempt_id INTEGER NOT NULL,
              account_name TEXT NOT NULL,
              biz TEXT NOT NULL,
              state TEXT NOT NULL,
              PRIMARY KEY (attempt_id, biz)
            );
            INSERT INTO discovery_attempts VALUES (
              1, '2026-08-13T00:00:00+00:00', '2026-08-13T00:01:00+00:00',
              'probe', 'success_no_new_shadow_candidates', 'shadow_state_url_set', 5
            );
            INSERT INTO discovery_account_results VALUES (
              1, 'A', 'Qml6QQ==', 'success_no_new_shadow_candidates'
            );
            PRAGMA user_version=2;
            """
        )

    with DiscoveryStore(path).connect() as conn:
        row = conn.execute(
            "SELECT requested_page_size, requested_page_size_origin "
            "FROM discovery_attempts WHERE id=1"
        ).fetchone()
        assert tuple(row) == (5, "recorded")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(discovery_attempts)")}
        assert "requested_count" not in columns
        assert conn.execute("PRAGMA user_version").fetchone()[0] == wechat_store.SCHEMA_VERSION


def test_store_migrates_v3_attempts_as_unverified_identity_evidence(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE discovery_attempts (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              started_at TEXT NOT NULL,
              finished_at TEXT NOT NULL,
              kind TEXT NOT NULL,
              state TEXT NOT NULL,
              change_basis TEXT NOT NULL,
              requested_page_size INTEGER,
              requested_page_size_origin TEXT NOT NULL
            );
            CREATE TABLE discovery_account_results (
              attempt_id INTEGER NOT NULL REFERENCES discovery_attempts(id) ON DELETE CASCADE,
              account_name TEXT NOT NULL, biz TEXT NOT NULL, state TEXT NOT NULL,
              PRIMARY KEY (attempt_id, biz)
            );
            CREATE TABLE discovery_candidates (
              biz TEXT NOT NULL, url TEXT NOT NULL, declared_account_name TEXT NOT NULL,
              title TEXT NOT NULL, author TEXT NOT NULL, published_at TEXT NOT NULL,
              first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
              PRIMARY KEY (biz, url)
            );
            CREATE TABLE discovery_attempt_candidates (
              attempt_id INTEGER NOT NULL REFERENCES discovery_attempts(id) ON DELETE CASCADE,
              biz TEXT NOT NULL, url TEXT NOT NULL, declared_account_name TEXT NOT NULL,
              title TEXT NOT NULL, author TEXT NOT NULL, published_at TEXT NOT NULL,
              new_to_shadow_state INTEGER NOT NULL,
              PRIMARY KEY (attempt_id, biz, url)
            );
            INSERT INTO discovery_attempts VALUES (
              1, '2026-08-13T00:00:00+00:00', '2026-08-13T00:01:00+00:00',
              'probe', 'rate_limited', 'shadow_state_url_set', 5, 'recorded'
            );
            INSERT INTO discovery_account_results VALUES (
              1, 'A', 'Qml6QQ==', 'rate_limited'
            );
            PRAGMA user_version=3;
            """
        )

    store = DiscoveryStore(path)
    store.migrate()
    attempt = store.attempt(1)

    assert attempt is not None
    assert attempt.identity_resolution_id is None
    assert attempt.identity_resolution_origin == "predates_resolution"
    with store.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == wechat_store.SCHEMA_VERSION
        target = conn.execute(
            "SELECT legacy_target_account_name, legacy_target_public_biz "
            "FROM discovery_attempts WHERE id=1"
        ).fetchone()
        assert tuple(target) == ("A", "Qml6QQ==")
        assert conn.execute(
            "SELECT COUNT(*) FROM identity_resolution_attempts"
        ).fetchone()[0] == 0


def test_v4_to_v5_downgrades_unproven_observed_biz_and_removes_duplicate_homes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    _create_v4_state(path)

    store = DiscoveryStore(path)
    store.migrate()
    resolution = store.identity_resolution(1)

    assert resolution is not None
    assert resolution.state is IdentityResolutionState.LEGACY_NAME_AND_BIZ_MATCH
    assert resolution.provisional_match_origin == "predates_unique_normalized_name_contract"
    assert resolution.invalidation_reason == "predates_observed_public_biz"
    assert store.identity_status((AccountConfig("A", "Qml6QQ=="),)) == ((), 0, 1, 0)
    with store.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == wechat_store.SCHEMA_VERSION
        tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert "discovery_account_results" not in tables
        assert "discovery_candidates" not in tables
        assert "consuming_probe_attempt_id" not in {
            str(row[1]) for row in conn.execute("PRAGMA table_info(identity_resolution_attempts)")
        }


def test_v4_consumed_identity_migrates_as_explicit_legacy_probe(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    _create_v4_state(path, consumed_relation=True)

    store = DiscoveryStore(path)
    store.migrate()
    attempt = store.attempt(1)
    resolution = store.identity_resolution(1)

    assert attempt is not None
    assert attempt.identity_resolution_id is None
    assert attempt.identity_resolution_origin == "predates_resolution"
    assert attempt.account_results[0].account_name == "A"
    assert attempt.account_results[0].biz == "Qml6QQ=="
    assert resolution is not None
    assert resolution.provisional_match_origin == "predates_unique_normalized_name_contract"
    assert resolution.invalidated_at is not None
    with store.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == wechat_store.SCHEMA_VERSION
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_fresh_schema_uses_v8_single_source_probe_ledger(tmp_path: Path) -> None:
    store = DiscoveryStore(tmp_path / "state.db")

    with store.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == wechat_store.SCHEMA_VERSION
        probe_columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(discovery_attempts)")
        }
        candidate_columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(discovery_attempt_candidates)")
        }
        resolution_columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(identity_resolution_attempts)")
        }

    assert "kind" not in probe_columns
    assert "change_basis" not in probe_columns
    assert "target_account_name" not in probe_columns
    assert "target_public_biz" not in probe_columns
    assert {"legacy_target_account_name", "legacy_target_public_biz"} <= probe_columns
    assert "probe_attempt_id" in candidate_columns
    assert "attempt_id" not in candidate_columns
    assert "observed_public_biz" not in candidate_columns
    assert "observed_public_biz" not in resolution_columns
    assert "provisional_match_origin" in resolution_columns
    assert "target_identity_evidence" in probe_columns


def test_v7_raw_schema_rejects_false_provisional_relation_and_verified_zero_candidate(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 14, tzinfo=UTC)
    account = AccountConfig("A", "Qml6QQ==")
    config = DiscoveryConfig(3, True, timedelta(0), (account,))
    store = DiscoveryStore(tmp_path / "state.db")
    failed_resolution = store.reserve_identity_resolution(
        account, config=config, started_at=now
    )
    store.complete_identity_resolution(
        failed_resolution,
        state=IdentityResolutionState.AUTH_REQUIRED,
        finished_at=now + timedelta(seconds=1),
        platform_error_ret=200003,
    )
    with store.connect() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO discovery_attempts(
              started_at, finished_at, outcome,
              legacy_target_account_name, legacy_target_public_biz,
              requested_page_size, requested_page_size_origin,
              identity_resolution_id, identity_resolution_origin,
              target_identity_evidence
            ) VALUES (?, NULL, 'reserved', NULL, NULL, 5, 'recorded', ?,
              'provisional_searchbiz_match', 'pending')
            """,
            ((now + timedelta(seconds=2)).isoformat(), failed_resolution),
        )

    provisional_id = store.reserve_identity_resolution(
        account, config=config, started_at=now + timedelta(seconds=3)
    )
    store.complete_identity_resolution(
        provisional_id,
        state=IdentityResolutionState.PROVISIONAL_MATCH,
        finished_at=now + timedelta(seconds=4),
        provisional=ProvisionalIdentity("A", "private-fakeid"),
    )
    reservation = store.reserve_probe(
        account,
        config=config,
        started_at=now + timedelta(seconds=5),
        requested_page_size=5,
    )
    with store.connect() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE discovery_attempts SET finished_at=?, outcome='success', "
            "target_identity_evidence='article_url_public_biz_verified' WHERE id=?",
            ((now + timedelta(seconds=6)).isoformat(), reservation.attempt_id),
        )


def test_v7_raw_schema_rejects_candidate_attached_to_nonverified_probe(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 14, tzinfo=UTC)
    account = AccountConfig("A", "Qml6QQ==")
    config = DiscoveryConfig(3, True, timedelta(0), (account,))
    store = DiscoveryStore(tmp_path / "state.db")
    resolution_id = store.reserve_identity_resolution(account, config=config, started_at=now)
    store.complete_identity_resolution(
        resolution_id,
        state=IdentityResolutionState.PROVISIONAL_MATCH,
        finished_at=now + timedelta(seconds=1),
        provisional=ProvisionalIdentity("A", "private-fakeid"),
    )
    reservation = store.reserve_probe(
        account,
        config=config,
        started_at=now + timedelta(seconds=2),
        requested_page_size=5,
    )
    store.complete_probe(
        reservation.attempt_id,
        finished_at=now + timedelta(seconds=3),
        state=DiscoveryState.IDENTITY_UNVERIFIED,
        target_identity_evidence=TargetIdentityEvidence.EMPTY_ARTICLE_LIST,
    )

    with store.connect() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO discovery_attempt_candidates(
              probe_attempt_id, url, title, author, published_at
            ) VALUES (?, ?, 'bad', 'A', ?)
            """,
            (
                reservation.attempt_id,
                "https://mp.weixin.qq.com/s?__biz=Qml6QQ%3D%3D&mid=1&idx=1&sn=bad",
                now.isoformat(),
            ),
        )


def test_v7_to_v8_preserves_verified_success_and_freezes_candidate_snapshot(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    _create_v7_state_with_verified_success(path)

    store = DiscoveryStore(path)
    store.migrate()

    assert store.attempt_identity_issue(1) is None
    with store.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == wechat_store.SCHEMA_VERSION
        for statement, parameters in (
            (
                """
                INSERT INTO discovery_attempt_candidates(
                  probe_attempt_id, url, title, author, published_at
                ) VALUES (1,
                  'https://mp.weixin.qq.com/s?__biz=Qml6QQ%3D%3D&mid=2&idx=1&sn=late',
                  'late', 'A', '2026-08-14T00:00:00+00:00')
                """,
                (),
            ),
            (
                "UPDATE discovery_attempt_candidates SET title='tampered' "
                "WHERE probe_attempt_id=1",
                (),
            ),
            (
                "DELETE FROM discovery_attempt_candidates WHERE probe_attempt_id=1",
                (),
            ),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(statement, parameters)


@pytest.mark.parametrize(
    ("supersede_before_completion", "migration_only_evidence", "message"),
    (
        (True, False, "superseded"),
        (False, True, "migration-only"),
    ),
)
def test_v7_to_v8_rejects_unrecoverable_verified_relationships_and_rolls_back(
    tmp_path: Path,
    supersede_before_completion: bool,
    migration_only_evidence: bool,
    message: str,
) -> None:
    path = tmp_path / "state.db"
    _create_v7_state_with_verified_success(
        path,
        supersede_before_completion=supersede_before_completion,
        migration_only_evidence=migration_only_evidence,
    )

    with pytest.raises(DiscoveryStoreVersionError, match=message):
        DiscoveryStore(path).migrate()

    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 7
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE '%_v7'"
        ).fetchone() is None


def test_v8_raw_schema_rejects_fresh_provisional_probe_with_migration_only_evidence(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    _create_v7_state_with_verified_success(path)
    store = DiscoveryStore(path)
    store.migrate()
    assert store.latest_attempt() is not None

    with store.connect() as conn:
        conn.execute(
            "UPDATE discovery_attempts SET finished_at=NULL, outcome='reserved', "
            "target_identity_evidence='pending' WHERE id=1"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE discovery_attempts SET finished_at=?, outcome='success', "
                "target_identity_evidence='predates_v7_verification' WHERE id=1",
                (datetime(2026, 8, 14, 0, 2, tzinfo=UTC).isoformat(),),
            )


@pytest.mark.parametrize(
    ("requested_page_size", "requested_page_size_origin", "legacy_name", "legacy_biz"),
    (
        (None, "recorded", "A", "Qml6QQ=="),
        (5, "predates_persistence", "A", "Qml6QQ=="),
        (None, "predates_persistence", None, "Qml6QQ=="),
        (None, "predates_persistence", "A", None),
    ),
)
def test_v8_raw_schema_rejects_unrecoverable_legacy_probe_fields(
    tmp_path: Path,
    requested_page_size: int | None,
    requested_page_size_origin: str,
    legacy_name: str | None,
    legacy_biz: str | None,
) -> None:
    store = DiscoveryStore(tmp_path / "state.db")

    with store.connect() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO discovery_attempts(
              started_at, finished_at, outcome,
              legacy_target_account_name, legacy_target_public_biz,
              requested_page_size, requested_page_size_origin,
              identity_resolution_id, identity_resolution_origin,
              target_identity_evidence
            ) VALUES ('2026-08-14T00:00:00+00:00',
              '2026-08-14T00:01:00+00:00', 'rate_limited', ?, ?, ?, ?, NULL,
              'predates_resolution', 'not_observed')
            """,
            (
                legacy_name,
                legacy_biz,
                requested_page_size,
                requested_page_size_origin,
            ),
        )


def test_v8_raw_schema_rejects_verified_completion_after_relation_is_superseded(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 14, tzinfo=UTC)
    account = AccountConfig("A", "Qml6QQ==")
    config = DiscoveryConfig(3, True, timedelta(0), (account,))
    store = DiscoveryStore(tmp_path / "state.db")
    first_id = store.reserve_identity_resolution(account, config=config, started_at=now)
    store.complete_identity_resolution(
        first_id,
        state=IdentityResolutionState.PROVISIONAL_MATCH,
        finished_at=now + timedelta(seconds=1),
        provisional=ProvisionalIdentity("A", "first-private-fakeid"),
    )
    reservation = store.reserve_probe(
        account,
        config=config,
        started_at=now + timedelta(seconds=2),
        requested_page_size=5,
    )
    with store.connect() as conn:
        conn.execute(
            """
            INSERT INTO discovery_attempt_candidates(
              probe_attempt_id, url, title, author, published_at
            ) VALUES (?,
              'https://mp.weixin.qq.com/s?__biz=Qml6QQ%3D%3D&mid=1&idx=1&sn=ok',
              'candidate', 'A', ?)
            """,
            (reservation.attempt_id, now.isoformat()),
        )
        cursor = conn.execute(
            """
                INSERT INTO identity_resolution_attempts(
                  started_at, finished_at, configured_account_name,
                  configured_public_biz, outcome, provisional_match_origin,
                  observed_account_name, provisional_fakeid,
                  platform_error_ret_origin
                ) VALUES (?, ?, 'A', 'Qml6QQ==', 'provisional_match',
                  'searchbiz_unique_normalized_name', 'A', 'second-private-fakeid',
                  'not_applicable')
            """,
            (
                (now + timedelta(seconds=3)).isoformat(),
                (now + timedelta(seconds=4)).isoformat(),
            ),
        )
        conn.execute(
            "UPDATE identity_resolution_attempts SET superseding_resolution_id=? WHERE id=?",
            (int(cursor.lastrowid), first_id),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                UPDATE discovery_attempts
                SET finished_at=?, outcome='success',
                    target_identity_evidence='article_url_public_biz_verified'
                WHERE id=?
                """,
                ((now + timedelta(seconds=5)).isoformat(), reservation.attempt_id),
            )


def test_v5_to_v6_preserves_legacy_failure_and_normalizes_utc(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    _create_weak_v5_state(path)

    store = DiscoveryStore(path)
    store.migrate()
    attempt = store.attempt(1)

    assert attempt is not None
    assert attempt.state is DiscoveryState.RATE_LIMITED
    assert attempt.started_at == datetime(2026, 8, 13, tzinfo=UTC)
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == wechat_store.SCHEMA_VERSION
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute(
            "SELECT started_at FROM discovery_attempts WHERE id=1"
        ).fetchone()[0] == "2026-08-13T00:00:00+00:00"


def test_v5_to_v6_rejects_unrecoverable_resolved_identity_and_rolls_back(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    _create_weak_v5_state(path, invalid_resolved=True)

    with pytest.raises(DiscoveryStoreVersionError, match="missing recoverable"):
        DiscoveryStore(path).migrate()

    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name='identity_resolution_attempts_v5'"
        ).fetchone() is None
        assert conn.execute(
            "SELECT COUNT(*) FROM identity_resolution_attempts"
        ).fetchone()[0] == 1


def test_v5_to_v6_rejects_resolved_identity_with_mismatched_observed_name(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    _create_weak_v5_state(path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO identity_resolution_attempts VALUES (
              1, '2026-08-12T23:50:00+00:00', '2026-08-12T23:51:00+00:00',
              'A', 'Qml6QQ==', 'resolved',
              'normalized_account_name_and_public_biz', 'different-account',
              'Qml6QQ==', 'recorded', 'fakeid', NULL, NULL, NULL
            )
            """
        )

    with pytest.raises(DiscoveryStoreVersionError, match="account name"):
        DiscoveryStore(path).migrate()

    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 5


def test_v5_to_v6_rejects_probe_that_claims_nonresolved_verified_identity(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    _create_weak_v5_state(path, contradictory_verified_relation=True)

    with pytest.raises(DiscoveryStoreVersionError, match="verified identity relation"):
        DiscoveryStore(path).migrate()

    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
        assert conn.execute(
            "SELECT identity_resolution_origin FROM discovery_attempts WHERE id=1"
        ).fetchone()[0] == "verified_resolution"


def test_v5_to_v6_rejects_reverse_probe_completion_time_and_rolls_back(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    _create_weak_v5_state(path, reverse_probe_time=True)

    with pytest.raises(DiscoveryStoreVersionError, match="precedes"):
        DiscoveryStore(path).migrate()

    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 5


def test_v5_to_v6_rejects_verified_identity_resolved_after_probe_started(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    _create_weak_v5_state(path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO identity_resolution_attempts VALUES (
              1, '2026-08-13T00:10:00+00:00', '2026-08-13T00:11:00+00:00',
              'A', 'Qml6QQ==', 'resolved',
              'normalized_account_name_and_public_biz', 'A', 'Qml6QQ==',
              'recorded', 'late-fakeid', NULL, NULL, NULL
            )
            """
        )
        conn.execute(
            "UPDATE discovery_attempts SET identity_resolution_id=1, "
            "identity_resolution_origin='verified_resolution' WHERE id=1"
        )

    with pytest.raises(DiscoveryStoreVersionError, match="resolved before probe"):
        DiscoveryStore(path).migrate()

    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 5


def test_v5_to_v6_rejects_probe_using_superseded_identity(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    _create_weak_v5_state(path)
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            INSERT INTO identity_resolution_attempts VALUES (
              1, '2026-08-12T23:50:00+00:00', '2026-08-12T23:51:00+00:00',
              'A', 'Qml6QQ==', 'resolved',
              'normalized_account_name_and_public_biz', 'A', 'Qml6QQ==',
              'recorded', 'old-fakeid', NULL, NULL, 2
            );
            INSERT INTO identity_resolution_attempts VALUES (
              2, '2026-08-12T23:52:00+00:00', '2026-08-12T23:53:00+00:00',
              'A', 'Qml6QQ==', 'resolved',
              'normalized_account_name_and_public_biz', 'A', 'Qml6QQ==',
              'recorded', 'new-fakeid', NULL, NULL, NULL
            );
            UPDATE discovery_attempts
            SET identity_resolution_id=1,
                identity_resolution_origin='verified_resolution'
            WHERE id=1;
            """
        )

    with pytest.raises(DiscoveryStoreVersionError, match="supersession source"):
        DiscoveryStore(path).migrate()

    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 5


@pytest.mark.parametrize("topology", ("self", "back_edge", "cross_account", "cycle"))
def test_v5_to_v6_rejects_invalid_supersession_topology(
    tmp_path: Path,
    topology: str,
) -> None:
    path = tmp_path / "state.db"
    _create_weak_v5_state(path)
    second_account = "B" if topology == "cross_account" else "A"
    first_superseding_id = 1 if topology == "self" else 2
    second_superseding_id = 1 if topology in {"back_edge", "cycle"} else None
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO identity_resolution_attempts VALUES (
              1, '2026-08-12T23:50:00+00:00', '2026-08-12T23:51:00+00:00',
              'A', 'Qml6QQ==', 'resolved',
              'normalized_account_name_and_public_biz', 'A', 'Qml6QQ==',
              'recorded', 'old-fakeid', NULL, NULL, ?
            )
            """,
            (first_superseding_id,),
        )
        if topology != "self":
            conn.execute(
                """
                INSERT INTO identity_resolution_attempts VALUES (
                  2, '2026-08-12T23:52:00+00:00', '2026-08-12T23:53:00+00:00',
                  ?, 'Qml6QQ==', 'resolved',
                  'normalized_account_name_and_public_biz', ?, 'Qml6QQ==',
                  'recorded', 'new-fakeid', NULL, NULL, ?
                )
                """,
                (second_account, second_account, second_superseding_id),
            )

    with pytest.raises(DiscoveryStoreVersionError, match="supersession"):
        DiscoveryStore(path).migrate()

    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 5


def test_v5_to_v6_preserves_valid_unused_identity_supersession(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    _create_weak_v5_state(path)
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            INSERT INTO identity_resolution_attempts VALUES (
              1, '2026-08-12T23:50:00+00:00', '2026-08-12T23:51:00+00:00',
              'A', 'Qml6QQ==', 'resolved',
              'normalized_account_name_and_public_biz', 'A', 'Qml6QQ==',
              'recorded', 'old-fakeid', NULL, NULL, 2
            );
            INSERT INTO identity_resolution_attempts VALUES (
              2, '2026-08-12T23:52:00+00:00', '2026-08-12T23:53:00+00:00',
              'A', 'Qml6QQ==', 'resolved',
              'normalized_account_name_and_public_biz', 'A', 'Qml6QQ==',
              'recorded', 'new-fakeid', NULL, NULL, NULL
            );
            """
        )

    store = DiscoveryStore(path)
    store.migrate()
    old_resolution = store.identity_resolution(1)
    new_resolution = store.identity_resolution(2)

    assert old_resolution is not None
    assert old_resolution.superseding_resolution_id == 2
    assert new_resolution is not None
    assert new_resolution.superseding_resolution_id is None


@pytest.mark.parametrize(
    ("invalidated_at", "outcome", "finished_at", "reason", "accepted"),
    (
        (
            "2026-08-13T00:00:00+00:00",
            "identity_mismatch",
            "2026-08-13T00:00:00+00:00",
            "article_url_biz_mismatch",
            True,
        ),
        (
            "2026-08-13T00:01:00+00:00",
            "identity_mismatch",
            "2026-08-13T00:01:00+00:00",
            "article_url_biz_mismatch",
            True,
        ),
        (
            "2026-08-13T00:00:00+00:00",
            "auth_required",
            "2026-08-13T00:00:00+00:00",
            "article_url_biz_mismatch",
            False,
        ),
        (
            "2026-08-13T00:00:00+00:00",
            "identity_mismatch",
            "2026-08-13T00:01:00+00:00",
            "article_url_biz_mismatch",
            False,
        ),
        (
            "2026-08-13T00:00:00+00:00",
            "identity_mismatch",
            "2026-08-13T00:00:00+00:00",
            "manual_invalidation",
            False,
        ),
        (
            "2026-08-13T00:01:00+00:00",
            "auth_required",
            "2026-08-13T00:02:00+00:00",
            "article_url_biz_mismatch",
            False,
        ),
    ),
)
def test_v5_to_v6_accepts_invalidation_only_for_same_probe_mismatch(
    tmp_path: Path,
    invalidated_at: str,
    outcome: str,
    finished_at: str,
    reason: str,
    accepted: bool,
) -> None:
    path = tmp_path / "state.db"
    _create_weak_v5_state(path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO identity_resolution_attempts VALUES (
              1, '2026-08-12T23:50:00+00:00', '2026-08-12T23:51:00+00:00',
              'A', 'Qml6QQ==', 'resolved',
              'normalized_account_name_and_public_biz', 'A', 'Qml6QQ==',
              'recorded', 'fakeid', ?, ?, NULL
            )
            """,
            (invalidated_at, reason),
        )
        conn.execute(
            """
            UPDATE discovery_attempts
            SET finished_at=?, outcome=?, identity_resolution_id=1,
                identity_resolution_origin='verified_resolution'
            WHERE id=1
            """,
            (finished_at, outcome),
        )

    if accepted:
        store = DiscoveryStore(path)
        store.migrate()
        assert store.attempt(1).state is DiscoveryState.IDENTITY_MISMATCH
        assert store.identity_resolution(1).invalidated_at == datetime(
            2026, 8, 13, 0, int(invalidated_at[14:16]), tzinfo=UTC
        )
    else:
        with pytest.raises(
            DiscoveryStoreVersionError,
            match="identity invalidation|invalidated before reservation",
        ):
            DiscoveryStore(path).migrate()
        with sqlite3.connect(path) as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 5


def test_v5_to_v6_rejects_candidates_attached_to_failed_probe(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    _create_weak_v5_state(path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO discovery_attempt_candidates VALUES (
              1, 'Qml6QQ==',
              'https://mp.weixin.qq.com/s?__biz=Qml6QQ==',
              'title', 'author', '2026-08-13T00:00:00+00:00'
            )
            """
        )

    with pytest.raises(DiscoveryStoreVersionError, match="failed probe contains candidates"):
        DiscoveryStore(path).migrate()

    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 5


def test_v6_success_with_clean_candidate_snapshot_stays_legacy_after_v7_migration(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    _create_v6_success_state(path)

    store = DiscoveryStore(path)
    store.migrate()
    attempt = store.attempt(1)

    assert attempt is not None
    assert attempt.state is DiscoveryState.SUCCESS
    assert (
        attempt.target_identity_evidence
        is TargetIdentityEvidence.PREDATES_V7_VERIFICATION
    )
    assert store.attempt_identity_issue(1) is not None
    with store.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == wechat_store.SCHEMA_VERSION
        verification_origin = conn.execute(
            "SELECT target_identity_evidence FROM discovery_attempts WHERE id=1"
        ).fetchone()[0]
        assert verification_origin == "predates_v7_verification"


@pytest.mark.parametrize(
    ("state", "evidence", "with_candidate"),
    (
        (
            DiscoveryState.IDENTITY_UNVERIFIED,
            TargetIdentityEvidence.EMPTY_ARTICLE_LIST,
            False,
        ),
        (
            DiscoveryState.IDENTITY_UNVERIFIED,
            TargetIdentityEvidence.ARTICLE_URL_PUBLIC_BIZ_UNAVAILABLE,
            False,
        ),
        (
            DiscoveryState.IDENTITY_MISMATCH,
            TargetIdentityEvidence.ARTICLE_URL_PUBLIC_BIZ_MISMATCH,
            False,
        ),
        (
            DiscoveryState.SUCCESS,
            TargetIdentityEvidence.ARTICLE_URL_PUBLIC_BIZ_VERIFIED,
            True,
        ),
    ),
)
def test_probe_reconstructs_distinct_target_identity_evidence_from_store(
    tmp_path: Path,
    state: DiscoveryState,
    evidence: TargetIdentityEvidence,
    with_candidate: bool,
) -> None:
    now = datetime(2026, 8, 14, tzinfo=UTC)
    account = AccountConfig("A", "Qml6QQ==")
    config = DiscoveryConfig(3, True, timedelta(0), (account,))
    store = DiscoveryStore(tmp_path / "state.db")
    resolution_id = store.reserve_identity_resolution(
        account,
        config=config,
        started_at=now,
    )
    store.complete_identity_resolution(
        resolution_id,
        state=IdentityResolutionState.PROVISIONAL_MATCH,
        finished_at=now + timedelta(seconds=1),
        provisional=ProvisionalIdentity("A", "private-fakeid"),
    )
    reservation = store.reserve_probe(
        account,
        config=config,
        started_at=now + timedelta(seconds=2),
        requested_page_size=5,
    )
    candidate = DiscoveryArticle(
        "A",
        "Qml6QQ==",
        "candidate",
        "https://mp.weixin.qq.com/s?__biz=Qml6QQ%3D%3D&mid=1&idx=1&sn=verified",
        "A",
        now,
    )
    store.complete_probe(
        reservation.attempt_id,
        finished_at=now + timedelta(seconds=3),
        candidates=(candidate,) if with_candidate else (),
        state=state,
        target_identity_evidence=evidence,
    )

    attempt = store.attempt(reservation.attempt_id)

    assert attempt is not None
    assert attempt.target_identity_evidence is evidence
    identity_issue = store.attempt_identity_issue(reservation.attempt_id)
    if evidence is TargetIdentityEvidence.ARTICLE_URL_PUBLIC_BIZ_VERIFIED:
        assert identity_issue is None
    else:
        assert identity_issue is not None


def test_v4_to_v5_rolls_back_a_contradictory_consumption_relation(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    _create_v4_state(path, contradictory_relation=True)

    with pytest.raises(DiscoveryStoreVersionError, match="contradictory"):
        DiscoveryStore(path).migrate()

    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name='discovery_attempts_v5'"
        ).fetchone() is None


def test_concurrent_legacy_openers_coordinate_one_monotonic_migration(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE discovery_attempts (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              started_at TEXT NOT NULL, finished_at TEXT NOT NULL,
              kind TEXT NOT NULL, state TEXT NOT NULL, change_basis TEXT NOT NULL,
              requested_page_size INTEGER, requested_page_size_origin TEXT NOT NULL
            );
            CREATE TABLE discovery_account_results (
              attempt_id INTEGER NOT NULL, account_name TEXT NOT NULL,
              biz TEXT NOT NULL, state TEXT NOT NULL, PRIMARY KEY (attempt_id, biz)
            );
            CREATE TABLE discovery_candidates (
              biz TEXT NOT NULL, url TEXT NOT NULL, declared_account_name TEXT NOT NULL,
              title TEXT NOT NULL, author TEXT NOT NULL, published_at TEXT NOT NULL,
              first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
              PRIMARY KEY (biz, url)
            );
            CREATE TABLE discovery_attempt_candidates (
              attempt_id INTEGER NOT NULL, biz TEXT NOT NULL, url TEXT NOT NULL,
              declared_account_name TEXT NOT NULL, title TEXT NOT NULL,
              author TEXT NOT NULL, published_at TEXT NOT NULL,
              new_to_shadow_state INTEGER NOT NULL,
              PRIMARY KEY (attempt_id, biz, url)
            );
            PRAGMA user_version=3;
            """
        )

    def open_version() -> int:
        with DiscoveryStore(path).connect() as conn:
            return int(conn.execute("PRAGMA user_version").fetchone()[0])

    with ThreadPoolExecutor(max_workers=2) as pool:
        versions = list(pool.map(lambda _index: open_version(), range(2)))

    assert versions == [wechat_store.SCHEMA_VERSION, wechat_store.SCHEMA_VERSION]
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_store_serializes_first_seen_decision_for_competing_attempts(tmp_path: Path) -> None:
    now = datetime(2026, 8, 13, tzinfo=UTC)
    store = DiscoveryStore(tmp_path / "state.db")
    article = DiscoveryArticle(
        "A",
        "Qml6QQ==",
        "一",
        "https://mp.weixin.qq.com/s?__biz=Qml6QQ%3D%3D&mid=1&idx=1&sn=one",
        "A",
        now,
    )

    first_id, first_new = _record_probe(
        store, started_at=now, finished_at=now, candidates=(article,)
    )
    second_id, second_new = _record_probe(
        store,
        started_at=now + timedelta(seconds=3),
        finished_at=now + timedelta(seconds=3),
        candidates=(article,),
    )

    assert (first_id, second_id, first_new, second_new) == (1, 2, 1, 0)
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM discovery_attempts").fetchone()[0] == 2
        assert conn.execute(
            "SELECT COUNT(DISTINCT url) FROM discovery_attempt_candidates"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name='discovery_candidates'"
        ).fetchone() is None


def test_store_preserves_future_platform_time_but_compare_rejects_coverage(tmp_path: Path) -> None:
    now = datetime(2026, 8, 13, tzinfo=UTC)
    article = DiscoveryArticle(
        "A",
        "Qml6QQ==",
        "平台时间略超本机",
        "https://mp.weixin.qq.com/s?__biz=Qml6QQ%3D%3D&mid=2&idx=1&sn=future",
        "A",
        now + timedelta(seconds=5),
    )
    store = DiscoveryStore(tmp_path / "state.db")
    attempt_id, _new_count = _record_probe(
        store, started_at=now, finished_at=now, candidates=(article,)
    )
    persisted = store.attempt(attempt_id)

    assert persisted is not None
    assert persisted.candidate_snapshot == (article,)
    with pytest.raises(ShadowNotComparable, match="publication time is after"):
        compare_shadow_window(
            account_name="A",
            biz="Qml6QQ==",
            baseline=[article],
            since=now - timedelta(hours=1),
            attempt=persisted,
        )


def test_effective_status_preserves_failure_across_throttle_window(tmp_path: Path) -> None:
    now = datetime(2026, 8, 13, tzinfo=UTC)
    account = AccountConfig("A", "Qml6QQ==", ("https://mp.weixin.qq.com/s/a",))
    config = DiscoveryConfig(
        version=2,
        manual_backend_requests_enabled=True,
        refresh_interval=timedelta(hours=2),
        accounts=(account,),
    )
    credential_path = tmp_path / "session.json"
    credential_path.write_text(
        json.dumps(
            {
                "version": 1,
                "token": "fixture-token",
                "cookies": [
                    {"name": "session", "value": "fixture-cookie", "domain": ".weixin.qq.com"}
                ],
            }
        ),
        encoding="utf-8",
    )
    credential_path.chmod(0o600)
    failed = DiscoveryAttempt(
        started_at=now - timedelta(minutes=5),
        finished_at=now - timedelta(minutes=4),
        state=DiscoveryState.AUTH_REQUIRED,
        kind=AttemptKind.PROBE,
        account_results=(AccountResult("A", "Qml6QQ==", DiscoveryState.AUTH_REQUIRED),),
        candidate_snapshot=(),
        requested_page_size=5,
        requested_page_size_origin="recorded",
        identity_resolution_id=1,
        identity_resolution_origin="provisional_searchbiz_match",
        target_identity_evidence=TargetIdentityEvidence.NOT_OBSERVED,
    )

    latest_request = BackendRequest(
        id=1,
        kind="probe",
        started_at=failed.started_at,
        finished_at=failed.finished_at,
        state=failed.state.value,
        account_name="A",
    )
    status = effective_status(
        config,
        credential_path=credential_path,
        latest_attempt=failed,
        latest_request=latest_request,
        now=now,
    )

    assert status.state is DiscoveryGateState.READY_TO_RESOLVE
    assert status.latest_request.state == DiscoveryState.AUTH_REQUIRED.value
    assert status.next_request_at is None


def test_effective_status_distinguishes_disabled_unconfigured_ready_and_request_window(tmp_path: Path) -> None:
    now = datetime(2026, 8, 13, tzinfo=UTC)
    account = AccountConfig("A", "Qml6QQ==", ("https://mp.weixin.qq.com/s/a",))
    disabled = DiscoveryConfig(1, False, timedelta(hours=2), (account,))
    manual = DiscoveryConfig(1, True, timedelta(hours=2), (account,))
    missing = tmp_path / "missing.json"

    assert effective_status(disabled, credential_path=missing, now=now).state is DiscoveryGateState.DISABLED
    assert effective_status(manual, credential_path=missing, now=now).state is DiscoveryGateState.UNCONFIGURED

    credential_path = tmp_path / "session.json"
    credential_path.write_text(
        json.dumps(
            {
                "version": 1,
                "token": "fixture-token",
                "cookies": [
                    {"name": "session", "value": "fixture-cookie", "domain": ".weixin.qq.com"}
                ],
            }
        ),
        encoding="utf-8",
    )
    credential_path.chmod(0o600)
    assert effective_status(manual, credential_path=credential_path, now=now).state is DiscoveryGateState.READY_TO_RESOLVE

    success = DiscoveryAttempt(
        started_at=now - timedelta(minutes=5),
        finished_at=now - timedelta(minutes=4),
        state=DiscoveryState.SUCCESS_NO_NEW_SHADOW_CANDIDATES,
        kind=AttemptKind.PROBE,
        account_results=(
            AccountResult("A", "Qml6QQ==", DiscoveryState.SUCCESS_NO_NEW_SHADOW_CANDIDATES),
        ),
        candidate_snapshot=(),
        requested_page_size=5,
        requested_page_size_origin="recorded",
        identity_resolution_id=1,
        identity_resolution_origin="legacy_name_and_biz_match",
        target_identity_evidence=TargetIdentityEvidence.PREDATES_V7_VERIFICATION,
    )
    latest_request = BackendRequest(
        id=1,
        kind="probe",
        started_at=success.started_at,
        finished_at=success.finished_at,
        state=success.state.value,
        account_name="A",
    )
    status = effective_status(
        manual,
        credential_path=credential_path,
        latest_attempt=success,
        latest_request=latest_request,
        now=now,
    )
    assert status.state is DiscoveryGateState.COOLDOWN
    assert status.next_request_at == success.finished_at + timedelta(hours=2)


def test_effective_status_does_not_leave_expired_unknown_reservation_as_global_gate(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 13, 5, tzinfo=UTC)
    account = AccountConfig("A", "Qml6QQ==")
    config = DiscoveryConfig(1, True, timedelta(hours=2), (account,))
    credential_path = tmp_path / "session.json"
    credential_path.write_text("fixture", encoding="utf-8")
    latest = BackendRequest(
        id=4,
        kind="resolve",
        started_at=now - timedelta(hours=3),
        finished_at=None,
        state="reserved",
        account_name="A",
    )

    status = effective_status(
        config,
        credential_path=credential_path,
        latest_request=latest,
        now=now,
    )

    assert status.state is DiscoveryGateState.READY_TO_RESOLVE
    assert status.latest_request == latest
    assert status.next_request_at is None


def test_manual_probe_cooldown_covers_recent_success_and_same_day_rate_limit() -> None:
    now = datetime(2026, 8, 13, 12, tzinfo=UTC)
    account = AccountConfig("A", "Qml6QQ==", ())
    config = DiscoveryConfig(1, True, timedelta(days=1), (account,))
    recent_success = DiscoveryAttempt(
        now - timedelta(hours=1),
        now - timedelta(hours=1),
        DiscoveryState.SUCCESS_NO_NEW_SHADOW_CANDIDATES,
        AttemptKind.PROBE,
        (AccountResult("A", "Qml6QQ==", DiscoveryState.SUCCESS_NO_NEW_SHADOW_CANDIDATES),),
        (),
        5,
        "recorded",
        1,
        "legacy_name_and_biz_match",
        TargetIdentityEvidence.PREDATES_V7_VERIFICATION,
    )
    same_day_rate_limit = DiscoveryAttempt(
        now - timedelta(hours=2),
        now - timedelta(hours=2),
        DiscoveryState.RATE_LIMITED,
        AttemptKind.PROBE,
        (AccountResult("A", "Qml6QQ==", DiscoveryState.RATE_LIMITED),),
        (),
        5,
        "recorded",
        1,
        "provisional_searchbiz_match",
        TargetIdentityEvidence.NOT_OBSERVED,
        200013,
        "recorded",
    )
    previous_day_rate_limit = DiscoveryAttempt(
        datetime(2026, 8, 12, 3, tzinfo=UTC),
        datetime(2026, 8, 12, 3, tzinfo=UTC),
        DiscoveryState.RATE_LIMITED,
        AttemptKind.PROBE,
        (AccountResult("A", "Qml6QQ==", DiscoveryState.RATE_LIMITED),),
        (),
        5,
        "recorded",
        1,
        "provisional_searchbiz_match",
        TargetIdentityEvidence.NOT_OBSERVED,
        200013,
        "recorded",
    )
    historical_rate_limit_without_exact_ret = DiscoveryAttempt(
        now - timedelta(hours=2),
        now - timedelta(hours=2),
        DiscoveryState.RATE_LIMITED,
        AttemptKind.PROBE,
        (AccountResult("A", "Qml6QQ==", DiscoveryState.RATE_LIMITED),),
        (),
        5,
        "recorded",
        1,
        "provisional_searchbiz_match",
        TargetIdentityEvidence.NOT_OBSERVED,
        None,
        "predates_persistence",
    )

    assert manual_probe_blocked_until(config, recent_success, now=now) == (
        recent_success.finished_at + timedelta(days=1)
    )
    assert manual_probe_blocked_until(config, same_day_rate_limit, now=now) == datetime(
        2026, 8, 13, 16, tzinfo=UTC
    )
    assert manual_probe_blocked_until(config, previous_day_rate_limit, now=now) is None
    assert (
        manual_probe_blocked_until(
            config,
            historical_rate_limit_without_exact_ret,
            now=now,
        )
        is None
    )


def test_backend_request_cooldown_requires_recorded_rate_limit_evidence() -> None:
    now = datetime(2026, 8, 16, 12, tzinfo=UTC)
    config = DiscoveryConfig(
        3,
        True,
        timedelta(days=1),
        (AccountConfig("A", "Qml6QQ=="),),
    )
    finished_at = now - timedelta(hours=2)
    recorded = BackendRequest(
        id=1,
        kind="probe",
        started_at=finished_at,
        finished_at=finished_at,
        state=DiscoveryState.RATE_LIMITED.value,
        account_name="A",
        platform_error_ret=200013,
        platform_error_ret_origin="recorded",
    )
    historical = BackendRequest(
        id=2,
        kind="probe",
        started_at=finished_at,
        finished_at=finished_at,
        state=DiscoveryState.RATE_LIMITED.value,
        account_name="A",
        platform_error_ret=None,
        platform_error_ret_origin="predates_persistence",
    )

    assert backend_request_blocked_until(config, recorded, now=now) == datetime(
        2026,
        8,
        16,
        16,
        tzinfo=UTC,
    )
    assert backend_request_blocked_until(config, historical, now=now) is None


def test_platform_rejected_probe_persists_exact_ret_without_fake_time_cooldown(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 16, 11, tzinfo=UTC)
    account = AccountConfig("A", "Qml6QQ==")
    config = DiscoveryConfig(3, True, timedelta(hours=1), (account,))
    store = DiscoveryStore(tmp_path / "state.db")
    resolution_id = store.reserve_identity_resolution(account, config=config, started_at=now)
    store.complete_identity_resolution(
        resolution_id,
        state=IdentityResolutionState.PROVISIONAL_MATCH,
        finished_at=now + timedelta(seconds=1),
        provisional=ProvisionalIdentity("A", "private-fakeid"),
    )
    reservation = store.reserve_probe(
        account,
        config=config,
        started_at=now + timedelta(hours=1, seconds=1),
        requested_page_size=5,
    )
    store.complete_probe(
        reservation.attempt_id,
        finished_at=now + timedelta(hours=1, seconds=2),
        candidates=(),
        state=DiscoveryState.PLATFORM_REJECTED,
        target_identity_evidence=TargetIdentityEvidence.NOT_OBSERVED,
        platform_error_ret=200002,
    )

    attempt = store.attempt(reservation.attempt_id)
    assert attempt is not None
    assert attempt.state is DiscoveryState.PLATFORM_REJECTED
    assert attempt.platform_error_ret == 200002
    assert attempt.platform_error_ret_origin == "recorded"
    latest = store.latest_backend_request()
    assert latest is not None
    assert latest.platform_error_ret == 200002
    assert backend_request_blocked_until(
        config,
        latest,
        now=now + timedelta(hours=1, seconds=3),
    ) is None


def test_current_raw_schema_rejects_missing_or_contradictory_platform_error_ret(
    tmp_path: Path,
) -> None:
    store = DiscoveryStore(tmp_path / "state.db")
    with store.connect() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO identity_resolution_attempts(
                  started_at, finished_at, configured_account_name,
                  configured_public_biz, outcome, provisional_match_origin,
                  platform_error_ret, platform_error_ret_origin
                ) VALUES (?, ?, 'A', 'Qml6QQ==', 'platform_rejected',
                  'not_established', NULL, 'recorded')
                """,
                (now := datetime(2026, 8, 16, tzinfo=UTC).isoformat(), now),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO identity_resolution_attempts(
                  started_at, finished_at, configured_account_name,
                  configured_public_biz, outcome, provisional_match_origin,
                  platform_error_ret, platform_error_ret_origin
                ) VALUES (?, ?, 'A', 'Qml6QQ==', 'request_failed',
                  'not_established', 200002, 'recorded')
                """,
                (now, now),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO identity_resolution_attempts(
                  started_at, finished_at, configured_account_name,
                  configured_public_biz, outcome, provisional_match_origin,
                  platform_error_ret, platform_error_ret_origin
                ) VALUES (?, ?, 'A', 'Qml6QQ==', 'platform_rejected',
                  'not_established', 200002.5, 'recorded')
                """,
                (now, now),
            )


def test_v8_to_current_preserves_legacy_platform_failures_without_guessing_ret(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    now = datetime(2026, 8, 16, tzinfo=UTC).isoformat()
    with sqlite3.connect(path) as conn:
        DiscoveryStore._initialize_schema(conn, schema=wechat_store._SCHEMA_V8)
        conn.execute(
            """
            INSERT INTO identity_resolution_attempts(
              id, started_at, finished_at, configured_account_name,
              configured_public_biz, outcome, provisional_match_origin
            ) VALUES (1, ?, ?, 'A', 'Qml6QQ==', 'response_invalid',
              'not_established')
            """,
            (now, now),
        )
        conn.execute(
            """
            INSERT INTO discovery_attempts(
              id, started_at, finished_at, outcome,
              legacy_target_account_name, legacy_target_public_biz,
              requested_page_size, requested_page_size_origin,
              identity_resolution_id, identity_resolution_origin,
              target_identity_evidence
            ) VALUES (1, ?, ?, 'rate_limited', 'A', 'Qml6QQ==', NULL,
              'predates_persistence', NULL, 'predates_resolution', 'not_observed')
            """,
            (now, now),
        )

    assert DiscoveryStore(path).migrate() == (8, wechat_store.SCHEMA_VERSION)

    with sqlite3.connect(path) as conn:
        resolution_ret = conn.execute(
            "SELECT platform_error_ret, platform_error_ret_origin "
            "FROM identity_resolution_attempts WHERE id=1"
        ).fetchone()
        probe_ret = conn.execute(
            "SELECT platform_error_ret, platform_error_ret_origin "
            "FROM discovery_attempts WHERE id=1"
        ).fetchone()
        assert resolution_ret == (None, "predates_persistence")
        assert probe_ret == (None, "predates_persistence")
        assert conn.execute("PRAGMA user_version").fetchone()[0] == wechat_store.SCHEMA_VERSION


def test_v9_to_v10_adds_exact_integer_ret_guard() -> None:
    with sqlite3.connect(":memory:") as conn:
        DiscoveryStore._initialize_schema(conn, schema=wechat_store._SCHEMA_V9)
        now = datetime(2026, 8, 16, tzinfo=UTC).isoformat()
        conn.execute(
            """
            INSERT INTO identity_resolution_attempts(
              started_at, finished_at, configured_account_name,
              configured_public_biz, outcome, provisional_match_origin,
              platform_error_ret, platform_error_ret_origin
            ) VALUES (?, ?, 'A', 'Qml6QQ==', 'platform_rejected',
              'not_established', 200002, 'recorded')
            """,
            (now, now),
        )

        DiscoveryStore._migrate_v9_to_v10(conn)

        assert conn.execute("PRAGMA user_version").fetchone()[0] == 10
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                UPDATE identity_resolution_attempts
                SET platform_error_ret=200002.5
                WHERE id=1
                """
            )


def test_v9_to_v10_rejects_noninteger_existing_ret_and_rolls_back(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    now = datetime(2026, 8, 16, tzinfo=UTC).isoformat()
    with sqlite3.connect(path) as conn:
        DiscoveryStore._initialize_schema(conn, schema=wechat_store._SCHEMA_V9)
        conn.execute(
            """
            INSERT INTO identity_resolution_attempts(
              started_at, finished_at, configured_account_name,
              configured_public_biz, outcome, provisional_match_origin,
              platform_error_ret, platform_error_ret_origin
            ) VALUES (?, ?, 'A', 'Qml6QQ==', 'platform_rejected',
              'not_established', 200002.5, 'recorded')
            """,
            (now, now),
        )

    with pytest.raises(
        DiscoveryStoreVersionError,
        match="platform error ret is not an exact integer",
    ):
        DiscoveryStore(path).migrate()

    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 9
        assert conn.execute(
            "SELECT typeof(platform_error_ret) FROM identity_resolution_attempts"
        ).fetchone()[0] == "real"
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type='trigger' AND name LIKE 'require_integer_%'"
        ).fetchone()[0] == 0
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
