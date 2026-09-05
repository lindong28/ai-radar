from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from airadar import cli
from airadar.wechat_discovery import store as wechat_store
from airadar.wechat_discovery.models import (
    AccountConfig,
    AccountResult,
    AttemptKind,
    DiscoveryArticle,
    DiscoveryAttempt,
    DiscoveryConfig,
    DiscoveryState,
    IdentityResolutionState,
    TargetIdentityEvidence,
)
from airadar.wechat_discovery.protocol import (
    DiscoveryCredentials,
    DiscoveryIdentityMismatch,
    ProvisionalIdentity,
    SearchBizCandidate,
)
from airadar.wechat_discovery.store import DiscoveryStore


@pytest.mark.parametrize(
    ("evidence", "expected"),
    (
        (
            TargetIdentityEvidence.EMPTY_ARTICLE_LIST,
            "Target identity: NOT_VERIFIED — valid empty article list contained no public article URL",
        ),
        (
            TargetIdentityEvidence.ARTICLE_URL_PUBLIC_BIZ_UNAVAILABLE,
            "Target identity: NOT_VERIFIED — returned article URL did not expose a unique public biz",
        ),
        (
            TargetIdentityEvidence.ARTICLE_URL_PUBLIC_BIZ_MISMATCH,
            "Target identity: MISMATCH — returned article URL public biz contradicted configured target",
        ),
        (
            TargetIdentityEvidence.ARTICLE_URL_PUBLIC_BIZ_VERIFIED,
            "Target identity: VERIFIED — all returned article URLs matched configured public biz",
        ),
        (
            TargetIdentityEvidence.PREDATES_V7_VERIFICATION,
            "Target identity: NOT_VERIFIED — probe predates persisted article-URL public-biz verification",
        ),
    ),
)
def test_target_identity_evidence_line_is_reconstructable_from_attempt(
    evidence: TargetIdentityEvidence,
    expected: str,
) -> None:
    now = datetime(2026, 8, 14, tzinfo=UTC)
    attempt = DiscoveryAttempt(
        started_at=now,
        finished_at=now,
        state=(
            DiscoveryState.SUCCESS
            if evidence
            in {
                TargetIdentityEvidence.ARTICLE_URL_PUBLIC_BIZ_VERIFIED,
                TargetIdentityEvidence.PREDATES_V7_VERIFICATION,
            }
            else DiscoveryState.IDENTITY_MISMATCH
            if evidence is TargetIdentityEvidence.ARTICLE_URL_PUBLIC_BIZ_MISMATCH
            else DiscoveryState.IDENTITY_UNVERIFIED
        ),
        kind=AttemptKind.PROBE,
        account_results=(
            AccountResult("测试号", "Qml6QQ==", DiscoveryState.IDENTITY_UNVERIFIED),
        ),
        candidate_snapshot=(),
        requested_page_size=5,
        requested_page_size_origin="recorded",
        identity_resolution_id=1,
        identity_resolution_origin="provisional_searchbiz_match",
        target_identity_evidence=evidence,
    )

    assert cli._target_identity_evidence_line(attempt) == expected


def _config(tmp_path: Path, *, manual_probe_enabled: bool) -> Path:
    path = tmp_path / "wechat-discovery.toml"
    path.write_text(
        f"""
version = 3
manual_backend_requests_enabled = {str(manual_probe_enabled).lower()}
refresh_interval_minutes = 120
[[account]]
name = "测试号"
public_biz = "Qml6QQ=="
seed_urls = ["https://mp.weixin.qq.com/s/seed"]
""",
        encoding="utf-8",
    )
    return path


def _mp2rss_db(tmp_path: Path, *rows: tuple[str, str, datetime, datetime]) -> Path:
    path = tmp_path / "radar.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE sources (
              id TEXT PRIMARY KEY,
              enabled INTEGER NOT NULL,
              paused INTEGER NOT NULL
            );
            CREATE TABLE items (
              source_id TEXT NOT NULL,
              author TEXT,
              url TEXT NOT NULL,
              published_at TEXT NOT NULL,
              fetched_at TEXT NOT NULL
            );
            INSERT INTO sources VALUES ('wx_mp2rss', 1, 1);
            """
        )
        conn.executemany(
            "INSERT INTO items VALUES ('wx_mp2rss', ?, ?, ?, ?)",
            [
                (author, url, published_at.isoformat(), fetched_at.isoformat())
                for author, url, published_at, fetched_at in rows
            ],
        )
    return path


def _v7_state_db(tmp_path: Path) -> Path:
    path = tmp_path / "wechat-discovery-v7.db"
    with sqlite3.connect(path) as conn:
        DiscoveryStore._initialize_schema(conn, schema=wechat_store._SCHEMA_V7)
    return path


def _successful_attempt(
    state_db: Path,
    *,
    candidates: list[DiscoveryArticle],
    requested_page_size: int = 5,
    finished_at: datetime | None = None,
) -> int:
    store = DiscoveryStore(state_db)
    existing_id = store.latest_attempt_id() or 0
    finished_at = finished_at or datetime(2026, 8, 13, 3, tzinfo=UTC) + timedelta(
        minutes=10 * existing_id
    )
    account = AccountConfig("测试号", "Qml6QQ==")
    config = DiscoveryConfig(2, True, timedelta(0), (account,))
    resolution_id = store.reserve_identity_resolution(
        account, config=config, started_at=finished_at - timedelta(minutes=2)
    )
    store.complete_identity_resolution(
        resolution_id,
        state=IdentityResolutionState.PROVISIONAL_MATCH,
        finished_at=finished_at - timedelta(minutes=1),
        provisional=ProvisionalIdentity("测试号", "verified-fakeid"),
    )
    reservation = store.reserve_probe(
        account,
        config=config,
        started_at=finished_at - timedelta(seconds=2),
        requested_page_size=requested_page_size,
    )
    store.complete_probe(
        reservation.attempt_id,
        finished_at=finished_at,
        candidates=tuple(candidates),
        state=(DiscoveryState.SUCCESS if candidates else DiscoveryState.IDENTITY_UNVERIFIED),
        target_identity_evidence=(
            TargetIdentityEvidence.ARTICLE_URL_PUBLIC_BIZ_VERIFIED
            if candidates
            else TargetIdentityEvidence.EMPTY_ARTICLE_LIST
        ),
    )
    return reservation.attempt_id


def _failed_attempt(
    state_db: Path,
    *,
    started_at: datetime,
    state: DiscoveryState,
) -> int:
    store = DiscoveryStore(state_db)
    account = AccountConfig("测试号", "Qml6QQ==")
    config = DiscoveryConfig(2, True, timedelta(0), (account,))
    resolution_id = store.reserve_identity_resolution(
        account, config=config, started_at=started_at - timedelta(seconds=2)
    )
    store.complete_identity_resolution(
        resolution_id,
        state=IdentityResolutionState.PROVISIONAL_MATCH,
        finished_at=started_at - timedelta(seconds=1),
        provisional=ProvisionalIdentity("测试号", "verified-fakeid"),
    )
    reservation = store.reserve_probe(
        account, config=config, started_at=started_at, requested_page_size=5
    )
    store.complete_probe(
        reservation.attempt_id,
        finished_at=started_at,
        state=state,
        target_identity_evidence=TargetIdentityEvidence.NOT_OBSERVED,
        platform_error_ret=(
            200013
            if state is DiscoveryState.RATE_LIMITED
            else 200003 if state is DiscoveryState.AUTH_REQUIRED else None
        ),
    )
    return reservation.attempt_id


def test_admin_wechat_discovery_status_disabled_is_self_explanatory(capsys, tmp_path: Path) -> None:  # noqa: ANN001
    args = cli.build_parser().parse_args(
        [
            "admin",
            "wechat-discovery",
            "status",
            "--config",
            str(_config(tmp_path, manual_probe_enabled=False)),
            "--state-db",
            str(tmp_path / "state.db"),
            "--session-file",
            str(tmp_path / "missing-session.json"),
        ]
    )

    assert cli._admin(args) == 0
    output = capsys.readouterr().out
    assert output.startswith("WeChat discovery request gate: DISABLED\n")
    assert "Accounts: 1 configured" in output
    assert "Mp2RSS remains unchanged" in output
    assert "Credentials: not checked while disabled" in output
    assert (
        "Next: no action while disabled; explicitly enable only for an authorized "
        "one-shot request"
    ) in output


def test_admin_wechat_discovery_status_disabled_does_not_open_bad_state_db(
    capsys, tmp_path: Path
) -> None:  # noqa: ANN001
    bad_state_path = tmp_path / "state-directory"
    bad_state_path.mkdir()
    args = cli.build_parser().parse_args(
        [
            "admin",
            "wechat-discovery",
            "status",
            "--config",
            str(_config(tmp_path, manual_probe_enabled=False)),
            "--state-db",
            str(bad_state_path),
        ]
    )

    assert cli._admin(args) == 0
    assert capsys.readouterr().out.startswith("WeChat discovery request gate: DISABLED\n")


def test_status_and_compare_do_not_implicitly_migrate_shadow_state(
    monkeypatch, capsys, tmp_path: Path
) -> None:  # noqa: ANN001
    state_db = _v7_state_db(tmp_path)
    config_path = _config(tmp_path, manual_probe_enabled=True)
    session_file = tmp_path / "session.json"
    session_file.write_text("fixture", encoding="utf-8")
    session_file.chmod(0o600)
    monkeypatch.setattr(
        cli,
        "load_credentials",
        lambda _path: DiscoveryCredentials("fixture-token", "session=fixture-cookie"),
    )
    status_args = cli.build_parser().parse_args(
        [
            "admin",
            "wechat-discovery",
            "status",
            "--config",
            str(config_path),
            "--state-db",
            str(state_db),
            "--session-file",
            str(session_file),
        ]
    )

    assert cli._admin(status_args) == 2
    status_output = capsys.readouterr().out
    assert status_output.startswith("WeChat discovery: UNAVAILABLE\n")
    assert (
        f"Next: run ./run.sh admin wechat-discovery migrate --state-db {state_db}"
        in status_output
    )
    with sqlite3.connect(state_db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 7

    compare_args = cli.build_parser().parse_args(
        [
            "admin",
            "wechat-discovery",
            "compare",
            "--account",
            "测试号",
            "--attempt",
            "1",
            "--since",
            "2026-08-13T00:00:00+00:00",
            "--config",
            str(config_path),
            "--state-db",
            str(state_db),
            "--db-path",
            str(tmp_path / "unused-radar.db"),
        ]
    )

    assert cli._admin(compare_args) == 2
    compare_output = capsys.readouterr().out
    assert compare_output.startswith("WeChat discovery comparison: NOT_COMPARABLE\n")
    assert (
        f"Next: run ./run.sh admin wechat-discovery migrate --state-db {state_db}"
        in compare_output
    )
    with sqlite3.connect(state_db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 7

    migrate_args = cli.build_parser().parse_args(
        [
            "admin",
            "wechat-discovery",
            "migrate",
            "--state-db",
            str(state_db),
        ]
    )
    assert cli._admin(migrate_args) == 0
    migrate_output = capsys.readouterr().out
    assert migrate_output.startswith("WeChat discovery state migration: MIGRATED\n")
    assert "Schema: v7 -> v10" in migrate_output
    assert "Impact: private shadow state only" in migrate_output
    assert "Next: run ./run.sh admin wechat-discovery status" in migrate_output
    with sqlite3.connect(state_db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == wechat_store.SCHEMA_VERSION


def test_admin_wechat_discovery_status_disabled_preserves_historical_readiness(
    capsys, tmp_path: Path
) -> None:  # noqa: ANN001
    state_db = tmp_path / "state.db"
    attempt_id = _successful_attempt(
        state_db,
        candidates=[
            DiscoveryArticle(
                "测试号",
                "Qml6QQ==",
                "候选",
                "https://mp.weixin.qq.com/s?__biz=Qml6QQ%3D%3D&mid=9&idx=1&sn=ready",
                "测试号",
                datetime(2026, 8, 13, 2, tzinfo=UTC),
            )
        ],
    )
    args = cli.build_parser().parse_args(
        [
            "admin",
            "wechat-discovery",
            "status",
            "--config",
            str(_config(tmp_path, manual_probe_enabled=False)),
            "--state-db",
            str(state_db),
        ]
    )

    assert cli._admin(args) == 0
    output = capsys.readouterr().out
    assert (
        "Replacement readiness: NOT_VALIDATED — article-URL public-biz-verified probe "
        "exists; explicit comparison required"
    ) in output
    assert (
        f"Historical shadow evidence: ARTICLE_URL_BIZ_VERIFIED_SUCCESSFUL_PROBE — "
        f"probe {attempt_id} (测试号); comparison not assessed"
    ) in output


def test_readonly_disabled_status_does_not_upgrade_v6_success_to_v7_verification(
    tmp_path: Path,
) -> None:
    state_db = tmp_path / "v6.db"
    with sqlite3.connect(state_db) as conn:
        DiscoveryStore._initialize_schema(conn, schema=wechat_store._SCHEMA_V6)
        conn.execute(
            """
            INSERT INTO identity_resolution_attempts(
              id, started_at, finished_at, configured_account_name,
              configured_public_biz, outcome, verification_basis,
              observed_account_name, public_biz_match_origin, resolved_fakeid
            ) VALUES (1, '2026-08-14T00:00:00+00:00',
              '2026-08-14T00:01:00+00:00', '测试号', 'Qml6QQ==', 'resolved',
              'normalized_account_name_and_public_biz', '测试号', 'recorded',
              'private-fakeid')
            """
        )
        conn.execute(
            """
            INSERT INTO discovery_attempts(
              id, started_at, finished_at, outcome,
              legacy_target_account_name, legacy_target_public_biz,
              requested_page_size, requested_page_size_origin,
              identity_resolution_id, identity_resolution_origin
            ) VALUES (1, '2026-08-14T00:02:00+00:00',
              '2026-08-14T00:03:00+00:00', 'success', NULL, NULL, 5, 'recorded',
              1, 'verified_resolution')
            """
        )
        conn.execute(
            """
            INSERT INTO discovery_attempt_candidates(
              probe_attempt_id, url, title, author, published_at
            ) VALUES (1,
              'https://mp.weixin.qq.com/s?__biz=Qml6QQ%3D%3D&mid=1&idx=1&sn=old',
              'old', '测试号', '2026-08-14T00:02:00+00:00')
            """
        )
        conn.commit()

    evidence = cli._readonly_wechat_discovery_evidence(state_db)

    assert evidence == ("schema v6", 1, 1, 0, None, None, None)


def test_readonly_disabled_status_rejects_v7_candidate_without_public_biz_proof(
    tmp_path: Path,
) -> None:
    state_db = tmp_path / "v7.db"
    with sqlite3.connect(state_db) as conn:
        DiscoveryStore._initialize_schema(conn, schema=wechat_store._SCHEMA_V7)
        conn.execute(
            """
            INSERT INTO identity_resolution_attempts(
              id, started_at, finished_at, configured_account_name,
              configured_public_biz, outcome, provisional_match_origin,
              observed_account_name, provisional_fakeid
            ) VALUES (1, '2026-08-14T00:00:00+00:00',
              '2026-08-14T00:01:00+00:00', '测试号', 'Qml6QQ==',
              'provisional_match', 'searchbiz_unique_normalized_name', '测试号',
              'private-fakeid')
            """
        )
        conn.execute(
            """
            INSERT INTO discovery_attempts(
              id, started_at, finished_at, outcome,
              legacy_target_account_name, legacy_target_public_biz,
              requested_page_size, requested_page_size_origin,
              identity_resolution_id, identity_resolution_origin,
              target_identity_evidence
            ) VALUES (1, '2026-08-14T00:02:00+00:00', NULL, 'reserved',
              NULL, NULL, 5, 'recorded', 1, 'provisional_searchbiz_match', 'pending')
            """
        )
        conn.execute(
            """
            INSERT INTO discovery_attempt_candidates(
              probe_attempt_id, url, title, author, published_at
            ) VALUES (1, 'https://mp.weixin.qq.com/s/short-token', 'bad', '测试号',
              '2026-08-14T00:02:00+00:00')
            """
        )
        conn.execute(
            """
            UPDATE discovery_attempts
            SET finished_at='2026-08-14T00:03:00+00:00', outcome='success',
                target_identity_evidence='article_url_public_biz_verified'
            WHERE id=1
            """
        )
        conn.commit()

    evidence = cli._readonly_wechat_discovery_evidence(state_db)

    assert evidence == ("schema v7", 1, 1, 0, None, None, None)


def test_disabled_status_exposes_historical_platform_ret_absence(
    capsys, tmp_path: Path
) -> None:  # noqa: ANN001
    state_db = tmp_path / "state.db"
    now = "2026-08-16T10:00:00+00:00"
    with sqlite3.connect(state_db) as conn:
        DiscoveryStore._initialize_schema(conn, schema=wechat_store._SCHEMA_V8)
        conn.execute(
            """
            INSERT INTO discovery_attempts(
              id, started_at, finished_at, outcome,
              legacy_target_account_name, legacy_target_public_biz,
              requested_page_size, requested_page_size_origin,
              identity_resolution_id, identity_resolution_origin,
              target_identity_evidence
            ) VALUES (1, ?, ?, 'rate_limited', '测试号', 'Qml6QQ==', NULL,
              'predates_persistence', NULL, 'predates_resolution', 'not_observed')
            """,
            (now, now),
        )
    assert DiscoveryStore(state_db).migrate() == (8, 10)
    args = cli.build_parser().parse_args(
        [
            "admin",
            "wechat-discovery",
            "status",
            "--config",
            str(_config(tmp_path, manual_probe_enabled=False)),
            "--state-db",
            str(state_db),
        ]
    )

    assert cli._admin(args) == 0
    output = capsys.readouterr().out
    assert "Historical platform failure: exact ret was not recorded by the old schema" in output


def test_admin_wechat_discovery_status_disabled_marks_unreadable_evidence_unassessed(
    capsys, tmp_path: Path
) -> None:  # noqa: ANN001
    bad_state_path = tmp_path / "state-directory"
    bad_state_path.mkdir()
    args = cli.build_parser().parse_args(
        [
            "admin",
            "wechat-discovery",
            "status",
            "--config",
            str(_config(tmp_path, manual_probe_enabled=False)),
            "--state-db",
            str(bad_state_path),
        ]
    )

    assert cli._admin(args) == 0
    output = capsys.readouterr().out
    assert "Replacement readiness: UNASSESSED" in output
    assert "Historical shadow evidence: UNASSESSED" in output


def test_admin_wechat_discovery_compare_reports_windowed_coverage(
    capsys, tmp_path: Path
) -> None:  # noqa: ANN001
    state_db = tmp_path / "state.db"
    published_at = datetime(2026, 8, 13, 2, tzinfo=UTC)
    candidate = DiscoveryArticle(
        "测试号",
        "Qml6QQ==",
        "候选",
        "https://mp.weixin.qq.com/s?__biz=Qml6QQ%3D%3D&mid=1&idx=1&sn=shared&utm_source=shadow#fragment",
        "测试号",
        published_at,
    )
    attempt_id = _successful_attempt(state_db, candidates=[candidate])
    production_db = _mp2rss_db(
        tmp_path,
        (
            "测 试 号",
            "https://mp.weixin.qq.com/s?__biz=Qml6QQ%3D%3D&mid=1&idx=1&sn=shared",
            published_at,
            published_at + timedelta(minutes=15),
        ),
    )
    args = cli.build_parser().parse_args(
        [
            "admin",
            "wechat-discovery",
            "compare",
            "--account",
            "测试号",
            "--attempt",
            str(attempt_id),
            "--since",
            "2026-08-13T00:00:00+00:00",
            "--config",
            str(_config(tmp_path, manual_probe_enabled=False)),
            "--state-db",
            str(state_db),
            "--db-path",
            str(production_db),
        ]
    )

    assert cli._admin(args) == 0
    output = capsys.readouterr().out
    assert output.startswith("WeChat discovery comparison: COVERED_IN_WINDOW\n")
    assert "Account: 测试号" in output
    assert "Coverage: 1/1 Mp2RSS baseline URLs matched" in output
    assert "Candidate-only URLs: 0" in output
    assert "does not prove shared omissions, other accounts, or future coverage" in output


def test_admin_wechat_discovery_compare_distinguishes_missing_and_not_comparable(
    capsys, tmp_path: Path
) -> None:  # noqa: ANN001
    state_db = tmp_path / "state.db"
    published_at = datetime(2026, 8, 13, 2, tzinfo=UTC)
    candidate = DiscoveryArticle(
        "测试号",
        "Qml6QQ==",
        "候选",
        "https://mp.weixin.qq.com/s?__biz=Qml6QQ%3D%3D&mid=2&idx=1&sn=candidate",
        "测试号",
        published_at,
    )
    attempt_id = _successful_attempt(state_db, candidates=[candidate])
    production_db = _mp2rss_db(
        tmp_path,
        (
            "测试号",
            "https://mp.weixin.qq.com/s?__biz=Qml6QQ%3D%3D&mid=3&idx=1&sn=missing",
            published_at,
            published_at + timedelta(minutes=15),
        ),
    )
    common = [
        "admin",
        "wechat-discovery",
        "compare",
        "--account",
        "测试号",
        "--attempt",
        str(attempt_id),
        "--since",
        "2026-08-13T00:00:00+00:00",
        "--config",
        str(_config(tmp_path, manual_probe_enabled=False)),
        "--state-db",
        str(state_db),
        "--db-path",
        str(production_db),
    ]

    assert cli._admin(cli.build_parser().parse_args(common)) == 1
    output = capsys.readouterr().out
    assert output.startswith("WeChat discovery comparison: MISSING_IN_WINDOW\n")
    assert "Coverage: 0/1 Mp2RSS baseline URLs matched" in output
    assert "sn=missing" in output

    truncated_candidates = [
        DiscoveryArticle(
            "测试号",
            "Qml6QQ==",
            str(index),
            f"https://mp.weixin.qq.com/s?__biz=Qml6QQ%3D%3D&mid={10 + index}&idx=1&sn={index}",
            "测试号",
            datetime(2026, 8, 13, 2, index, tzinfo=UTC),
        )
        for index in range(5)
    ]
    truncated_id = _successful_attempt(state_db, candidates=truncated_candidates)
    truncated = common.copy()
    truncated[truncated.index(str(attempt_id))] = str(truncated_id)
    assert cli._admin(cli.build_parser().parse_args(truncated)) == 2
    output = capsys.readouterr().out
    assert output.startswith("WeChat discovery comparison: NOT_COMPARABLE\n")
    assert "page did not reach the start of the requested window" in output
    assert "Mp2RSS and production items are unchanged" in output
    assert "Next: use a narrower reached window" in output


def test_admin_wechat_discovery_compare_explains_baseline_preconditions(
    capsys, tmp_path: Path
) -> None:  # noqa: ANN001
    state_db = tmp_path / "state.db"
    published_at = datetime(2026, 8, 13, 2, tzinfo=UTC)
    candidate = DiscoveryArticle(
        "测试号",
        "Qml6QQ==",
        "候选",
        "https://mp.weixin.qq.com/s?__biz=Qml6QQ%3D%3D&mid=2&idx=1&sn=candidate",
        "测试号",
        published_at,
    )
    attempt_id = _successful_attempt(state_db, candidates=[candidate])
    production_db = _mp2rss_db(
        tmp_path,
        (
            "测试号",
            "https://mp.weixin.qq.com/s/old",
            published_at - timedelta(days=2),
            published_at - timedelta(days=2),
        ),
    )
    common = [
        "admin",
        "wechat-discovery",
        "compare",
        "--account",
        "测试号",
        "--attempt",
        str(attempt_id),
        "--since",
        "2026-08-13T00:00:00+00:00",
        "--config",
        str(_config(tmp_path, manual_probe_enabled=False)),
        "--state-db",
        str(state_db),
        "--db-path",
        str(production_db),
    ]

    assert cli._admin(cli.build_parser().parse_args(common)) == 2
    output = capsys.readouterr().out
    assert "baseline is empty in the requested window" in output
    assert "Next: choose a window containing Mp2RSS items" in output

    with sqlite3.connect(production_db) as conn:
        conn.execute("UPDATE sources SET enabled=0 WHERE id='wx_mp2rss'")
    assert cli._admin(cli.build_parser().parse_args(common)) == 2
    output = capsys.readouterr().out
    assert "source is absent or disabled" in output
    assert "Next: restore or enable wx_mp2rss" in output


def test_admin_wechat_discovery_compare_rejects_author_and_url_identity_gaps(
    capsys, tmp_path: Path
) -> None:  # noqa: ANN001
    state_db = tmp_path / "state.db"
    published_at = datetime(2026, 8, 13, 2, tzinfo=UTC)
    candidate = DiscoveryArticle(
        "测试号",
        "Qml6QQ==",
        "候选",
        "https://mp.weixin.qq.com/s?__biz=Qml6QQ%3D%3D&mid=2&idx=1&sn=candidate",
        "测试号",
        published_at,
    )
    attempt_id = _successful_attempt(state_db, candidates=[candidate])
    production_db = _mp2rss_db(
        tmp_path,
        ("另一个号", "https://mp.weixin.qq.com/s/other", published_at, published_at),
    )
    common = [
        "admin",
        "wechat-discovery",
        "compare",
        "--account",
        "测试号",
        "--attempt",
        str(attempt_id),
        "--since",
        "2026-08-13T00:00:00+00:00",
        "--config",
        str(_config(tmp_path, manual_probe_enabled=False)),
        "--state-db",
        str(state_db),
        "--db-path",
        str(production_db),
    ]

    assert cli._admin(cli.build_parser().parse_args(common)) == 2
    output = capsys.readouterr().out
    assert "no normalized production author bucket" in output
    assert "Next: correct the configured account name" in output

    with sqlite3.connect(production_db) as conn:
        conn.execute("DELETE FROM items")
        conn.execute(
            "INSERT INTO items VALUES ('wx_mp2rss', ?, ?, ?, ?)",
            (
                "测试号",
                "https://mp.weixin.qq.com/s/short-baseline",
                published_at.isoformat(),
                published_at.isoformat(),
            ),
        )
    assert cli._admin(cli.build_parser().parse_args(common)) == 2
    output = capsys.readouterr().out
    assert "URL identity forms differ" in output
    assert "Next: inspect the URL identities" in output


def test_admin_wechat_discovery_compare_rejects_legacy_unknown_page_size(
    capsys, tmp_path: Path
) -> None:  # noqa: ANN001
    state_db = tmp_path / "state.db"
    published_at = datetime(2026, 8, 13, 2, tzinfo=UTC)
    candidate_url = (
        "https://mp.weixin.qq.com/s?__biz=Qml6QQ%3D%3D&mid=1&idx=1&sn=candidate"
    )
    with sqlite3.connect(state_db) as conn:
        conn.executescript(
            f"""
            CREATE TABLE discovery_attempts (
              id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT NOT NULL,
              kind TEXT NOT NULL, state TEXT NOT NULL, change_basis TEXT NOT NULL
            );
            CREATE TABLE discovery_account_results (
              attempt_id INTEGER NOT NULL, account_name TEXT NOT NULL, biz TEXT NOT NULL,
              state TEXT NOT NULL, PRIMARY KEY (attempt_id, biz)
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
              1, '2026-08-13T01:59:00+00:00', '2026-08-13T03:00:00+00:00',
              'probe', 'success_with_new_shadow_candidates', 'shadow_state_url_set'
            );
            INSERT INTO discovery_account_results VALUES (
              1, '测试号', 'Qml6QQ==', 'success_with_new_shadow_candidates'
            );
            INSERT INTO discovery_candidates VALUES (
              'Qml6QQ==', '{candidate_url}', '测试号', '候选', '测试号',
              '{published_at.isoformat()}', '2026-08-13T03:00:00+00:00',
              '2026-08-13T03:00:00+00:00'
            );
            INSERT INTO discovery_attempt_candidates VALUES (
              1, 'Qml6QQ==', '{candidate_url}', '测试号', '候选', '测试号',
              '{published_at.isoformat()}', 1
            );
            PRAGMA user_version=1;
            """
        )
    production_db = _mp2rss_db(
        tmp_path,
        ("测试号", candidate_url, published_at, published_at),
    )
    DiscoveryStore(state_db).migrate()
    args = cli.build_parser().parse_args(
        [
            "admin",
            "wechat-discovery",
            "compare",
            "--account",
            "测试号",
            "--attempt",
            "1",
            "--since",
            "2026-08-13T00:00:00+00:00",
            "--config",
            str(_config(tmp_path, manual_probe_enabled=False)),
            "--state-db",
            str(state_db),
            "--db-path",
            str(production_db),
        ]
    )

    assert cli._admin(args) == 2
    output = capsys.readouterr().out
    assert "no persisted article-URL public-biz verification" in output
    assert "Next: resolve a provisional account mapping, then run a new authorized" in output


def test_admin_wechat_discovery_compare_rejects_failed_attempt(
    capsys, tmp_path: Path
) -> None:  # noqa: ANN001
    state_db = tmp_path / "state.db"
    now = datetime(2026, 8, 13, 3, tzinfo=UTC)
    attempt_id = _failed_attempt(
        state_db, started_at=now, state=DiscoveryState.RATE_LIMITED
    )
    production_db = _mp2rss_db(
        tmp_path,
        (
            "测试号",
            "https://mp.weixin.qq.com/s/baseline",
            now - timedelta(hours=1),
            now,
        ),
    )
    args = cli.build_parser().parse_args(
        [
            "admin",
            "wechat-discovery",
            "compare",
            "--account",
            "测试号",
            "--attempt",
            str(attempt_id),
            "--since",
            "2026-08-13T00:00:00+00:00",
            "--config",
            str(_config(tmp_path, manual_probe_enabled=False)),
            "--state-db",
            str(state_db),
            "--db-path",
            str(production_db),
        ]
    )

    assert cli._admin(args) == 2
    output = capsys.readouterr().out
    assert "ended as rate_limited, not success" in output
    assert "Next: wait for an authorized successful shadow probe" in output


def test_admin_wechat_discovery_status_retains_latest_success_after_newer_failure(
    monkeypatch, capsys, tmp_path: Path
) -> None:  # noqa: ANN001
    state_db = tmp_path / "state.db"
    now = datetime(2026, 8, 13, 3, tzinfo=UTC)
    candidate = DiscoveryArticle(
        "测试号",
        "Qml6QQ==",
        "候选",
        "https://mp.weixin.qq.com/s?__biz=Qml6QQ%3D%3D&mid=2&idx=1&sn=candidate",
        "测试号",
        now - timedelta(hours=1),
    )
    success_id = _successful_attempt(state_db, candidates=[candidate])
    failure_id = _failed_attempt(
        state_db,
        started_at=now + timedelta(hours=1),
        state=DiscoveryState.RATE_LIMITED,
    )
    session_file = tmp_path / "session.json"
    session_file.write_text("fixture", encoding="utf-8")
    session_file.chmod(0o600)
    monkeypatch.setattr(
        cli,
        "load_credentials",
        lambda _path: DiscoveryCredentials("fixture-token", "session=fixture-cookie"),
    )
    real_effective_status = cli.effective_status
    monkeypatch.setattr(
        cli,
        "effective_status",
        lambda config, **kwargs: real_effective_status(
            config, **kwargs, now=now + timedelta(hours=2)
        ),
    )
    args = cli.build_parser().parse_args(
        [
            "admin",
            "wechat-discovery",
            "status",
            "--config",
            str(_config(tmp_path, manual_probe_enabled=True)),
            "--state-db",
            str(state_db),
            "--session-file",
            str(session_file),
        ]
    )

    assert cli._admin(args) == 1
    output = capsys.readouterr().out
    assert (
        f"Latest attempt: {failure_id} probe (测试号) request outcome RATE_LIMITED"
        in output
    )
    assert "Latest attempt platform error: ret=200013" in output
    assert f"Latest successful attempt: {success_id} probe (测试号)" in output
    assert (
        f"Next: compare probe {success_id} for 测试号 before making another backend request"
        in output
    )
    assert "Next: resolve one configured account identity" not in output


def test_admin_wechat_discovery_status_cooldown_preserves_ready_probe_action(
    monkeypatch, capsys, tmp_path: Path
) -> None:  # noqa: ANN001
    state_db = tmp_path / "state.db"
    config_path = _config(tmp_path, manual_probe_enabled=True)
    config = cli.load_discovery_config(config_path)
    now = datetime.now(UTC)
    store = DiscoveryStore(state_db)
    resolution_id = store.reserve_identity_resolution(
        config.accounts[0],
        config=config,
        started_at=now - timedelta(minutes=1),
    )
    store.complete_identity_resolution(
        resolution_id,
        state=IdentityResolutionState.PROVISIONAL_MATCH,
        finished_at=now - timedelta(seconds=30),
        provisional=ProvisionalIdentity("测试号", "private-fakeid"),
    )
    session_file = tmp_path / "session.json"
    session_file.write_text("fixture", encoding="utf-8")
    session_file.chmod(0o600)
    monkeypatch.setattr(
        cli,
        "load_credentials",
        lambda _path: DiscoveryCredentials("fixture-token", "session=fixture-cookie"),
    )
    args = cli.build_parser().parse_args(
        [
            "admin",
            "wechat-discovery",
            "status",
            "--config",
            str(config_path),
            "--state-db",
            str(state_db),
            "--session-file",
            str(session_file),
        ]
    )

    assert cli._admin(args) == 1
    output = capsys.readouterr().out
    assert output.startswith("WeChat discovery request gate: COOLDOWN\n")
    assert (
        "Request timing basis: local safety policy; not a published WeChat platform window"
        in output
    )
    assert "Identity mapping: 1 provisional ready" in output
    assert "Next: after that time, run one authorized probe for 测试号" in output
    assert "resolve one unresolved account" not in output


def test_admin_wechat_discovery_status_unconfigured_exits_nonzero_with_action(capsys, tmp_path: Path) -> None:  # noqa: ANN001
    session_file = tmp_path / "missing-session.json"
    args = cli.build_parser().parse_args(
        [
            "admin",
            "wechat-discovery",
            "status",
            "--config",
            str(_config(tmp_path, manual_probe_enabled=True)),
            "--state-db",
            str(tmp_path / "state.db"),
            "--session-file",
            str(session_file),
        ]
    )

    assert cli._admin(args) == 1
    output = capsys.readouterr().out
    assert output.startswith("WeChat discovery request gate: UNCONFIGURED\n")
    assert "Impact: no WeChat admin requests can run" in output
    assert "only if you have an authorized WeChat admin account" in output


def test_admin_wechat_discovery_status_unknown_request_gives_recovery_action(
    monkeypatch, capsys, tmp_path: Path
) -> None:  # noqa: ANN001
    state_db = tmp_path / "state.db"
    config_path = _config(tmp_path, manual_probe_enabled=True)
    config = cli.load_discovery_config(config_path)
    store = DiscoveryStore(state_db)
    store.reserve_identity_resolution(
        config.accounts[0], config=config, started_at=datetime.now(UTC)
    )
    session_file = tmp_path / "session.json"
    session_file.write_text("fixture", encoding="utf-8")
    session_file.chmod(0o600)
    monkeypatch.setattr(
        cli,
        "load_credentials",
        lambda _path: DiscoveryCredentials("fixture-token", "session=fixture-cookie"),
    )
    args = cli.build_parser().parse_args(
        [
            "admin",
            "wechat-discovery",
            "status",
            "--config",
            str(config_path),
            "--state-db",
            str(state_db),
            "--session-file",
            str(session_file),
        ]
    )

    assert cli._admin(args) == 1
    output = capsys.readouterr().out
    assert output.startswith("WeChat discovery request gate: REQUEST_OUTCOME_UNKNOWN\n")
    assert "Next request allowed by local policy after:" in output
    assert (
        "Next: after that time, run one new identity resolution for 测试号; "
        "do not repeat the unknown request"
    ) in output


def test_admin_wechat_discovery_status_shows_failed_resolution_verification_basis(
    monkeypatch, capsys, tmp_path: Path
) -> None:  # noqa: ANN001
    state_db = tmp_path / "state.db"
    config_path = _config(tmp_path, manual_probe_enabled=True)
    config = cli.load_discovery_config(config_path)
    store = DiscoveryStore(state_db)
    started_at = datetime.now(UTC) - timedelta(minutes=1)
    resolution_id = store.reserve_identity_resolution(
        config.accounts[0], config=config, started_at=started_at
    )
    store.complete_identity_resolution(
        resolution_id,
        state=IdentityResolutionState.NO_MATCH,
        finished_at=started_at + timedelta(seconds=1),
    )
    session_file = tmp_path / "session.json"
    session_file.write_text("fixture", encoding="utf-8")
    session_file.chmod(0o600)
    monkeypatch.setattr(
        cli,
        "load_credentials",
        lambda _path: DiscoveryCredentials("fixture-token", "session=fixture-cookie"),
    )
    args = cli.build_parser().parse_args(
        [
            "admin",
            "wechat-discovery",
            "status",
            "--config",
            str(config_path),
            "--state-db",
            str(state_db),
            "--session-file",
            str(session_file),
        ]
    )

    assert cli._admin(args) == 0
    output = capsys.readouterr().out
    assert "request outcome NO_MATCH" in output
    assert "Selection basis: not established" in output
    assert "Public biz verification: NOT_OBSERVED" in output


def test_admin_wechat_discovery_resolve_reserves_before_network_and_hides_fakeid(
    monkeypatch, capsys, tmp_path: Path
) -> None:  # noqa: ANN001
    state_db = tmp_path / "state.db"
    session_file = tmp_path / "session.json"
    session_file.write_text("fixture", encoding="utf-8")
    session_file.chmod(0o600)
    monkeypatch.setattr(
        cli,
        "load_credentials",
        lambda _path: DiscoveryCredentials("fixture-token", "session=fixture-cookie"),
    )
    monkeypatch.setattr(cli, "verify_account_identity", lambda _account: None)

    class FakeClient:
        def __init__(self, _credentials: DiscoveryCredentials) -> None:
            pass

        def search_accounts(self, *, account_name: str):
            latest = DiscoveryStore(state_db).latest_backend_request()
            assert latest is not None
            assert latest.kind == "resolve"
            assert latest.state == "reserved"
            assert account_name == "测试号"
            return [SearchBizCandidate("测试号", "verified-fakeid")]

    monkeypatch.setattr(cli, "WeChatAdminClient", FakeClient)
    args = cli.build_parser().parse_args(
        [
            "admin",
            "wechat-discovery",
            "resolve",
            "--account",
            "测试号",
            "--config",
            str(_config(tmp_path, manual_probe_enabled=True)),
            "--state-db",
            str(state_db),
            "--session-file",
            str(session_file),
        ]
    )

    assert cli._admin(args) == 0
    output = capsys.readouterr().out
    assert output.startswith("WeChat identity resolution: PROVISIONAL_MATCH\n")
    assert "Mapping: provisional and available for one identity-checking probe only" in output
    assert "verified-fakeid" not in output
    resolved = DiscoveryStore(state_db).latest_identity_resolution()
    assert resolved is not None
    assert resolved.state is IdentityResolutionState.PROVISIONAL_MATCH
    assert resolved.observed_account_name == "测试号"
    assert resolved.fakeid == "verified-fakeid"


def test_admin_wechat_discovery_resolve_rate_limit_reports_requirement_and_next_time(
    monkeypatch, capsys, tmp_path: Path
) -> None:  # noqa: ANN001
    state_db = tmp_path / "state.db"
    session_file = tmp_path / "session.json"
    session_file.write_text("fixture", encoding="utf-8")
    session_file.chmod(0o600)
    monkeypatch.setattr(
        cli,
        "load_credentials",
        lambda _path: DiscoveryCredentials("fixture-token", "session=fixture-cookie"),
    )
    monkeypatch.setattr(cli, "verify_account_identity", lambda _account: None)

    class RateLimitedClient:
        def __init__(self, _credentials: DiscoveryCredentials) -> None:
            pass

        def search_accounts(self, *, account_name: str):  # noqa: ANN202
            raise cli.DiscoveryRateLimited("fixture", platform_error_ret=200013)

    monkeypatch.setattr(cli, "WeChatAdminClient", RateLimitedClient)
    args = cli.build_parser().parse_args(
        [
            "admin",
            "wechat-discovery",
            "resolve",
            "--account",
            "测试号",
            "--config",
            str(_config(tmp_path, manual_probe_enabled=True)),
            "--state-db",
            str(state_db),
            "--session-file",
            str(session_file),
        ]
    )

    assert cli._admin(args) == 1
    output = capsys.readouterr().out
    assert "Identity proof:" not in output
    assert "Search match: normalized account name only" in output
    assert "Public biz verification: NOT_OBSERVED" in output
    assert "Next request allowed by local policy after:" in output
    assert "." not in next(
        line for line in output.splitlines() if line.startswith("Next request allowed by local policy after:")
    ).split("T", 1)[1].split("+", 1)[0]


def test_admin_wechat_discovery_probe_is_single_account_shadow_only(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    state_db = tmp_path / "state.db"
    session_file = tmp_path / "session.json"
    session_file.write_text("fixture", encoding="utf-8")
    session_file.chmod(0o600)
    monkeypatch.setattr(
        cli,
        "load_credentials",
        lambda _path: DiscoveryCredentials("fixture-token", "session=fixture-cookie"),
    )

    class FakeClient:
        def __init__(self, _credentials: DiscoveryCredentials) -> None:
            pass

        def fetch_latest(self, *, account_name: str, biz: str, fakeid: str, count: int):
            assert account_name == "测试号"
            assert biz == "Qml6QQ=="
            assert fakeid == "verified-fakeid"
            assert count == 5
            return [
                DiscoveryArticle(
                    account_name,
                    biz,
                    "候选文章",
                    "https://mp.weixin.qq.com/s?__biz=Qml6QQ%3D%3D&mid=2&idx=1&sn=candidate",
                    account_name,
                    datetime(2026, 8, 13, tzinfo=UTC),
                )
            ]

    monkeypatch.setattr(cli, "WeChatAdminClient", FakeClient)
    monkeypatch.setattr(cli, "verify_account_identity", lambda _account: None)
    config_path = _config(tmp_path, manual_probe_enabled=True)
    config = cli.load_discovery_config(config_path)
    store = DiscoveryStore(state_db)
    resolution_id = store.reserve_identity_resolution(
        config.accounts[0],
        config=config,
        started_at=datetime.now(UTC) - timedelta(hours=3),
    )
    store.complete_identity_resolution(
        resolution_id,
        state=IdentityResolutionState.PROVISIONAL_MATCH,
        finished_at=datetime.now(UTC) - timedelta(hours=2, minutes=1),
        provisional=ProvisionalIdentity("测试号", "verified-fakeid"),
    )
    args = cli.build_parser().parse_args(
        [
            "admin",
            "wechat-discovery",
            "probe",
            "--account",
            "测试号",
            "--config",
            str(config_path),
            "--state-db",
            str(state_db),
            "--session-file",
            str(session_file),
        ]
    )

    assert cli._admin(args) == 0
    output = capsys.readouterr().out
    assert output.startswith("WeChat discovery probe: SUCCESS\n")
    assert "Account: 测试号" in output
    assert "Returned articles: 1 (1 new to shadow state URL set)" in output
    assert f"Shadow state: {state_db}" in output
    assert "Mp2RSS and production items are unchanged" in output
    latest = DiscoveryStore(state_db).latest_attempt()
    assert latest is not None
    assert latest.state is DiscoveryState.SUCCESS
    assert DiscoveryStore(state_db).candidate_urls("Qml6QQ==") == (
        "https://mp.weixin.qq.com/s?__biz=Qml6QQ%3D%3D&mid=2&idx=1&sn=candidate",
    )
    status_args = cli.build_parser().parse_args(
        [
            "admin",
            "wechat-discovery",
            "status",
            "--config",
            str(_config(tmp_path, manual_probe_enabled=True)),
            "--state-db",
            str(state_db),
            "--session-file",
            str(session_file),
        ]
    )
    assert cli._admin(status_args) == 1
    status_output = capsys.readouterr().out
    assert status_output.startswith("WeChat discovery request gate: COOLDOWN\n")
    assert "Latest attempt: 1 probe (测试号)" in status_output
    assert status_output.count("Target identity: VERIFIED") == 1
    assert "Identity mapping: 0 provisional ready, 1 assigned to probe reservation" in status_output
    assert "Latest article-URL-biz-verified successful probe: 1" in status_output
    assert (
        "Next: compare probe 1 for 测试号 before making another backend request"
        in status_output
    )


def test_admin_wechat_discovery_probe_preflights_state_before_network(
    monkeypatch, capsys, tmp_path: Path
) -> None:  # noqa: ANN001
    session_file = tmp_path / "session.json"
    session_file.write_text("fixture", encoding="utf-8")
    session_file.chmod(0o600)
    monkeypatch.setattr(
        cli,
        "load_credentials",
        lambda _path: DiscoveryCredentials("fixture-token", "session=fixture-cookie"),
    )
    called = False

    def unexpected_identity(_account):  # noqa: ANN001
        nonlocal called
        called = True

    monkeypatch.setattr(cli, "verify_account_identity", unexpected_identity)
    bad_state_path = tmp_path / "state-directory"
    bad_state_path.mkdir()
    args = cli.build_parser().parse_args(
        [
            "admin",
            "wechat-discovery",
            "probe",
            "--account",
            "测试号",
            "--config",
            str(_config(tmp_path, manual_probe_enabled=True)),
            "--state-db",
            str(bad_state_path),
            "--session-file",
            str(session_file),
        ]
    )

    assert cli._admin(args) == 2
    output = capsys.readouterr().out
    assert output.startswith("WeChat discovery probe: STATE_UNAVAILABLE\n")
    assert "no request was sent" in output
    assert called is False


def test_admin_wechat_discovery_probe_enforces_persisted_cooldown_before_identity_or_network(
    monkeypatch, capsys, tmp_path: Path
) -> None:  # noqa: ANN001
    state_db = tmp_path / "state.db"
    session_file = tmp_path / "session.json"
    session_file.write_text("fixture", encoding="utf-8")
    session_file.chmod(0o600)
    monkeypatch.setattr(
        cli,
        "load_credentials",
        lambda _path: DiscoveryCredentials("fixture-token", "session=fixture-cookie"),
    )
    now = datetime.now(UTC)
    _successful_attempt(
        state_db, candidates=[], finished_at=now - timedelta(minutes=1)
    )

    def unexpected_identity(_account):  # noqa: ANN001
        raise AssertionError("identity verification must not run during cooldown")

    monkeypatch.setattr(cli, "verify_account_identity", unexpected_identity)
    args = cli.build_parser().parse_args(
        [
            "admin",
            "wechat-discovery",
            "probe",
            "--account",
            "测试号",
            "--config",
            str(_config(tmp_path, manual_probe_enabled=True)),
            "--state-db",
            str(state_db),
            "--session-file",
            str(session_file),
        ]
    )

    assert cli._admin(args) == 1
    output = capsys.readouterr().out
    assert output.startswith("WeChat discovery probe: COOLDOWN\n")
    assert "Impact: no request was sent" in output
    assert (
        "Request timing basis: local safety policy; not a published WeChat platform window"
        in output
    )
    assert "Next request allowed by local policy after:" in output
    latest = DiscoveryStore(state_db).latest_attempt()
    assert latest is not None
    assert latest.state is DiscoveryState.IDENTITY_UNVERIFIED


def test_admin_wechat_discovery_probe_rate_limit_reports_unavailable_result_and_next_time(
    monkeypatch, capsys, tmp_path: Path
) -> None:  # noqa: ANN001
    state_db = tmp_path / "state.db"
    session_file = tmp_path / "session.json"
    session_file.write_text("fixture", encoding="utf-8")
    session_file.chmod(0o600)
    monkeypatch.setattr(
        cli,
        "load_credentials",
        lambda _path: DiscoveryCredentials("fixture-token", "session=fixture-cookie"),
    )

    class RateLimitedClient:
        def __init__(self, _credentials: DiscoveryCredentials) -> None:
            pass

        def fetch_latest(self, **_kwargs):  # noqa: ANN003, ANN202
            raise cli.DiscoveryRateLimited("fixture", platform_error_ret=200013)

    monkeypatch.setattr(cli, "WeChatAdminClient", RateLimitedClient)
    monkeypatch.setattr(cli, "verify_account_identity", lambda _account: None)
    config_path = _config(tmp_path, manual_probe_enabled=True)
    config = cli.load_discovery_config(config_path)
    store = DiscoveryStore(state_db)
    resolution_id = store.reserve_identity_resolution(
        config.accounts[0],
        config=config,
        started_at=datetime.now(UTC) - timedelta(hours=3),
    )
    store.complete_identity_resolution(
        resolution_id,
        state=IdentityResolutionState.PROVISIONAL_MATCH,
        finished_at=datetime.now(UTC) - timedelta(hours=2, minutes=1),
        provisional=ProvisionalIdentity("测试号", "verified-fakeid"),
    )
    args = cli.build_parser().parse_args(
        [
            "admin",
            "wechat-discovery",
            "probe",
            "--account",
            "测试号",
            "--config",
            str(config_path),
            "--state-db",
            str(state_db),
            "--session-file",
            str(session_file),
        ]
    )

    assert cli._admin(args) == 1
    output = capsys.readouterr().out
    assert "Returned articles:" not in output
    assert "Article result: unavailable — request was rate-limited" in output
    assert "Next request allowed by local policy after:" in output


def test_admin_wechat_discovery_probe_platform_rejection_reports_ret_without_cooldown(
    monkeypatch, capsys, tmp_path: Path
) -> None:  # noqa: ANN001
    state_db = tmp_path / "state.db"
    session_file = tmp_path / "session.json"
    session_file.write_text("fixture", encoding="utf-8")
    session_file.chmod(0o600)
    monkeypatch.setattr(
        cli,
        "load_credentials",
        lambda _path: DiscoveryCredentials("fixture-token", "session=fixture-cookie"),
    )

    class RejectedClient:
        def __init__(self, _credentials: DiscoveryCredentials) -> None:
            pass

        def fetch_latest(self, **_kwargs):  # noqa: ANN003, ANN202
            raise cli.DiscoveryPlatformRejected(
                "WeChat admin rejected the request (ret=200002)",
                platform_error_ret=200002,
            )

    monkeypatch.setattr(cli, "WeChatAdminClient", RejectedClient)
    monkeypatch.setattr(cli, "verify_account_identity", lambda _account: None)
    config_path = _config(tmp_path, manual_probe_enabled=True)
    config = cli.load_discovery_config(config_path)
    store = DiscoveryStore(state_db)
    resolution_id = store.reserve_identity_resolution(
        config.accounts[0],
        config=config,
        started_at=datetime.now(UTC) - timedelta(hours=3),
    )
    store.complete_identity_resolution(
        resolution_id,
        state=IdentityResolutionState.PROVISIONAL_MATCH,
        finished_at=datetime.now(UTC) - timedelta(hours=2, minutes=1),
        provisional=ProvisionalIdentity("测试号", "sentinel-private-fakeid"),
    )
    args = cli.build_parser().parse_args(
        [
            "admin",
            "wechat-discovery",
            "probe",
            "--account",
            "测试号",
            "--config",
            str(config_path),
            "--state-db",
            str(state_db),
            "--session-file",
            str(session_file),
        ]
    )

    assert cli._admin(args) == 1
    output = capsys.readouterr().out
    assert output.startswith("WeChat discovery probe: PLATFORM_REJECTED\n")
    assert "Platform error: ret=200002" in output
    assert "Next request allowed by local policy after:" not in output
    assert "cooldown" not in output.lower()
    assert "do not repeat the unchanged request" in output
    assert "sentinel-private-fakeid" not in output


def test_admin_wechat_discovery_probe_preserves_identity_mismatch_terminal(
    monkeypatch, capsys, tmp_path: Path
) -> None:  # noqa: ANN001
    state_db = tmp_path / "state.db"
    session_file = tmp_path / "session.json"
    session_file.write_text("fixture", encoding="utf-8")
    session_file.chmod(0o600)
    monkeypatch.setattr(
        cli,
        "load_credentials",
        lambda _path: DiscoveryCredentials("fixture-token", "session=fixture-cookie"),
    )

    class MismatchClient:
        def __init__(self, _credentials: DiscoveryCredentials) -> None:
            pass

        def fetch_latest(self, **_kwargs):  # noqa: ANN003, ANN202
            raise DiscoveryIdentityMismatch("fixture")

    monkeypatch.setattr(cli, "WeChatAdminClient", MismatchClient)
    monkeypatch.setattr(cli, "verify_account_identity", lambda _account: None)
    config_path = _config(tmp_path, manual_probe_enabled=True)
    config = cli.load_discovery_config(config_path)
    store = DiscoveryStore(state_db)
    started_at = datetime.now(UTC) - timedelta(hours=3)
    resolution_id = store.reserve_identity_resolution(
        config.accounts[0], config=config, started_at=started_at
    )
    store.complete_identity_resolution(
        resolution_id,
        state=IdentityResolutionState.PROVISIONAL_MATCH,
        finished_at=started_at + timedelta(minutes=1),
        provisional=ProvisionalIdentity("测试号", "sentinel-private-fakeid"),
    )
    args = cli.build_parser().parse_args(
        [
            "admin",
            "wechat-discovery",
            "probe",
            "--account",
            "测试号",
            "--config",
            str(config_path),
            "--state-db",
            str(state_db),
            "--session-file",
            str(session_file),
        ]
    )

    assert cli._admin(args) == 1
    output = capsys.readouterr().out
    assert output.startswith("WeChat discovery probe: IDENTITY_MISMATCH\n")
    assert (
        "Target identity: MISMATCH — returned article URL public biz contradicted "
        "configured target"
    ) in output
    assert (
        "Preserved: no candidates were stored; the provisional mapping was invalidated"
    ) in output
    resolution = DiscoveryStore(state_db).identity_resolution(resolution_id)
    assert resolution is not None
    assert resolution.invalidated_at is not None
    assert DiscoveryStore(state_db).attempt_candidates(1) == []


def test_admin_wechat_discovery_login_reports_saved_scope_without_enabling_canary(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    session_file = tmp_path / "session.json"
    browser_profile = tmp_path / "browser"
    captured: dict[str, object] = {}

    def fake_capture(**kwargs: object) -> None:
        captured.update(kwargs)
        session_file.write_text("fixture", encoding="utf-8")
        session_file.chmod(0o600)

    monkeypatch.setattr(cli, "capture_login", fake_capture)
    args = cli.build_parser().parse_args(
        [
            "admin",
            "wechat-discovery",
            "login",
            "--session-file",
            str(session_file),
            "--browser-profile",
            str(browser_profile),
            "--timeout-seconds",
            "120",
        ]
    )

    assert cli._admin(args) == 0
    assert captured == {
        "session_path": session_file,
        "browser_profile": browser_profile,
        "timeout_seconds": 120,
    }
    output = capsys.readouterr().out
    assert output.startswith("WeChat discovery login: WAITING\n")
    assert "WeChat discovery login: SAVED" in output
    assert f"Session: {session_file} (0600)" in output
    assert "Canary: unchanged and not scheduled" in output


def test_admin_wechat_discovery_login_failure_says_existing_session_is_retained(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:  # noqa: ANN001
    session_file = tmp_path / "session.json"
    session_file.write_text("existing", encoding="utf-8")
    session_file.chmod(0o600)

    def fake_capture(**_kwargs: object) -> None:
        raise cli.DiscoveryLoginError("fixture failure")

    monkeypatch.setattr(cli, "capture_login", fake_capture)
    args = cli.build_parser().parse_args(
        ["admin", "wechat-discovery", "login", "--session-file", str(session_file)]
    )

    assert cli._admin(args) == 1
    output = capsys.readouterr().out
    assert "No new private session was saved" in output
    assert "existing session file remains available" in output
    assert session_file.read_text(encoding="utf-8") == "existing"
    DiscoveryConfig,
