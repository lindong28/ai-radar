from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest

from airadar.wechat_discovery.models import (
    AccountConfig,
    DiscoveryConfig,
    DiscoveryState,
    TargetIdentityEvidence,
)
from airadar.wechat_discovery.protocol import (
    DiscoveryCredentials,
    DiscoveryIdentityAmbiguous,
    DiscoveryIdentityMismatch,
    DiscoveryIdentityNoMatch,
    DiscoveryIdentityUnverified,
    DiscoveryResponseInvalid,
    ProvisionalIdentity,
    WeChatAdminClient,
    parse_appmsgpublish,
    parse_searchbiz,
    select_unique_searchbiz_candidate,
)
from airadar.wechat_discovery.store import (
    DiscoveryCooldownActive,
    DiscoveryStore,
    IdentityResolutionState,
)


def _account() -> AccountConfig:
    return AccountConfig(name="测试 号", public_biz="Qml6QQ==")


def _config(account: AccountConfig) -> DiscoveryConfig:
    return DiscoveryConfig(
        version=2,
        manual_backend_requests_enabled=True,
        refresh_interval=timedelta(hours=2),
        accounts=(account,),
    )


def test_searchbiz_selects_only_one_normalized_name_as_provisional() -> None:
    candidates = parse_searchbiz(
        {
            "base_resp": {"ret": 0, "err_msg": "ok"},
            "total": 2,
            "list": [
                {"nickname": "测试号", "fakeid": "candidate-one"},
                {"nickname": "另一个号", "fakeid": "candidate-two"},
            ],
        }
    )

    provisional = select_unique_searchbiz_candidate(_account(), candidates)

    assert provisional.account_name == "测试号"
    assert provisional.fakeid == "candidate-one"
    assert "candidate-one" not in repr(provisional)


def test_searchbiz_fails_closed_for_no_match_ambiguous_or_truncated_results() -> None:
    wrong = parse_searchbiz(
        {
            "base_resp": {"ret": 0},
            "total": 1,
            "list": [{"nickname": "另一个号", "fakeid": "candidate"}],
        }
    )
    with pytest.raises(DiscoveryIdentityNoMatch):
        select_unique_searchbiz_candidate(_account(), wrong)

    duplicated = parse_searchbiz(
        {
            "base_resp": {"ret": 0},
            "total": 2,
            "list": [
                {"nickname": "测试号", "fakeid": "same"},
                {"nickname": "测试号", "fakeid": "same"},
            ],
        }
    )
    with pytest.raises(DiscoveryIdentityAmbiguous):
        select_unique_searchbiz_candidate(_account(), duplicated)

    with pytest.raises(DiscoveryIdentityAmbiguous, match="complete"):
        parse_searchbiz(
            {
                "base_resp": {"ret": 0},
                "total": 51,
                "list": [
                    {"nickname": "测试号", "fakeid": "one"}
                ],
            }
        )

    with pytest.raises(DiscoveryResponseInvalid, match="total"):
        parse_searchbiz(
            {
                "base_resp": {"ret": 0},
                "list": [{"nickname": "测试号", "fakeid": "one"}],
            }
        )

    with pytest.raises(DiscoveryResponseInvalid, match="required candidate fields"):
        parse_searchbiz(
            {"base_resp": {"ret": 0}, "total": 1, "list": [{"nickname": "测试号"}]}
        )

    with pytest.raises(DiscoveryIdentityAmbiguous, match="complete"):
        parse_searchbiz(
            {
                "base_resp": {"ret": 0},
                "total": 0,
                "list": [
                    {"nickname": "测试号", "fakeid": "one"}
                ],
            }
        )


def test_admin_client_uses_searchbiz_for_resolution_and_fakeid_for_publish() -> None:
    requests = []

    def request_json(request, timeout):  # noqa: ANN001, ARG001
        requests.append(request)
        if "searchbiz" in request.full_url:
            return {"base_resp": {"ret": 0}, "total": 0, "list": []}
        return {"base_resp": {"ret": 0}, "publish_page": {"publish_list": []}}

    client = WeChatAdminClient(
        DiscoveryCredentials(token="secret-token", cookie_header="session=secret-cookie"),
        request_json=request_json,
    )

    client.search_accounts(account_name="测试 号")
    client.fetch_latest(
        account_name="测试 号", biz="Qml6QQ==", fakeid="verified-fakeid", count=5
    )

    search_query = parse_qs(urlsplit(requests[0].full_url).query)
    publish_query = parse_qs(urlsplit(requests[1].full_url).query)
    assert search_query["action"] == ["search_biz"]
    assert search_query["query"] == ["测试 号"]
    assert search_query["count"] == ["50"]
    assert publish_query["fakeid"] == ["verified-fakeid"]
    assert publish_query["fakeid"] != ["Qml6QQ=="]


def test_publish_articles_use_observed_url_biz_and_reject_identity_gaps() -> None:
    payload = {
        "base_resp": {"ret": 0},
        "publish_page": {
            "publish_list": [
                {
                    "publish_info": {
                        "appmsgex": [
                            {
                                "title": "一",
                                "link": "https://mp.weixin.qq.com/s?__biz=Qml6QQ%3D%3D&mid=1&idx=1&sn=x",
                                "update_time": 1786500000,
                            }
                        ]
                    }
                }
            ]
        },
    }

    articles = parse_appmsgpublish(payload, account_name="测试号", expected_biz="Qml6QQ==")

    assert [article.biz for article in articles] == ["Qml6QQ=="]

    with pytest.raises(DiscoveryIdentityMismatch):
        parse_appmsgpublish(payload, account_name="测试号", expected_biz="wrong")

    missing = {
        "base_resp": {"ret": 0},
        "publish_page": {
            "publish_list": [
                {
                    "publish_info": {
                        "appmsgex": [
                            {
                                "title": "短链",
                                "link": "https://mp.weixin.qq.com/s/short",
                                "update_time": 1786500000,
                            }
                        ]
                    }
                }
            ]
        },
    }
    with pytest.raises(DiscoveryIdentityUnverified):
        parse_appmsgpublish(missing, account_name="测试号", expected_biz="Qml6QQ==")

    for invalid_url in (
        "https://evil.example/s?__biz=Qml6QQ%3D%3D",
        "https://mp.weixin.qq.com/spam?__biz=Qml6QQ%3D%3D",
    ):
        invalid = {
            "base_resp": {"ret": 0},
            "publish_page": {
                "publish_list": [
                    {
                        "publish_info": {
                            "appmsgex": [
                                {
                                    "title": "伪链接",
                                    "link": invalid_url,
                                    "update_time": 1786500000,
                                }
                            ]
                        }
                    }
                ]
            },
        }
        with pytest.raises(DiscoveryResponseInvalid):
            parse_appmsgpublish(
                invalid, account_name="测试号", expected_biz="Qml6QQ=="
            )


def test_request_reservations_are_persisted_before_network_and_probe_consumes_mapping(
    tmp_path,
) -> None:  # noqa: ANN001
    account = _account()
    config = _config(account)
    store = DiscoveryStore(tmp_path / "state.db")
    started = datetime(2026, 8, 13, 0, tzinfo=UTC)

    resolution_id = store.reserve_identity_resolution(account, config=config, started_at=started)
    latest = store.latest_backend_request()
    assert latest is not None
    assert latest.kind == "resolve"
    assert latest.state == "reserved"
    assert latest.started_at == started
    assert latest.finished_at is None

    with pytest.raises(DiscoveryCooldownActive):
        store.reserve_identity_resolution(
            account, config=config, started_at=started + timedelta(minutes=1)
        )

    store.complete_identity_resolution(
        resolution_id,
        state=IdentityResolutionState.PROVISIONAL_MATCH,
        finished_at=started + timedelta(minutes=2),
        provisional=ProvisionalIdentity("测试号", "verified-fakeid"),
    )
    resolved = store.identity_resolution(resolution_id)
    assert resolved is not None
    assert resolved.observed_account_name == "测试号"

    with pytest.raises(DiscoveryCooldownActive):
        store.reserve_probe(
            account,
            config=config,
            started_at=started + timedelta(hours=1),
            requested_page_size=5,
        )

    reservation = store.reserve_probe(
        account,
        config=config,
        started_at=started + timedelta(hours=2, minutes=2),
        requested_page_size=5,
    )
    assert reservation.identity_resolution_id == resolution_id
    assert reservation.fakeid == "verified-fakeid"
    assert store.identity_resolution(resolution_id).assigned_probe_attempt_id == (
        reservation.attempt_id
    )

    with pytest.raises(DiscoveryIdentityNoMatch, match="resolve"):
        store.reserve_probe(
            account,
            config=config,
            started_at=started + timedelta(hours=4, minutes=3),
            requested_page_size=5,
        )


def test_store_rejects_mismatched_resolved_identity_and_reverse_completion_time(
    tmp_path,
) -> None:  # noqa: ANN001
    account = _account()
    config = _config(account)
    store = DiscoveryStore(tmp_path / "state.db")
    started = datetime(2026, 8, 13, tzinfo=UTC)
    resolution_id = store.reserve_identity_resolution(account, config=config, started_at=started)

    with pytest.raises(ValueError, match="does not match"):
        store.complete_identity_resolution(
            resolution_id,
            state=IdentityResolutionState.PROVISIONAL_MATCH,
            finished_at=started + timedelta(seconds=1),
            provisional=ProvisionalIdentity("另一个号", "fakeid"),
        )
    with pytest.raises(ValueError, match="precedes"):
        store.complete_identity_resolution(
            resolution_id,
            state=IdentityResolutionState.AUTH_REQUIRED,
            finished_at=started - timedelta(seconds=1),
            platform_error_ret=200003,
        )

    persisted = store.identity_resolution(resolution_id)
    assert persisted is not None
    assert persisted.state is IdentityResolutionState.RESERVED
    assert persisted.finished_at is None


def test_store_preserves_unavailable_url_identity_and_rejects_reverse_probe_time(tmp_path) -> None:  # noqa: ANN001
    account = _account()
    config = _config(account)
    store = DiscoveryStore(tmp_path / "state.db")
    started = datetime(2026, 8, 13, tzinfo=UTC)
    resolution_id = store.reserve_identity_resolution(account, config=config, started_at=started)
    store.complete_identity_resolution(
        resolution_id,
        state=IdentityResolutionState.PROVISIONAL_MATCH,
        finished_at=started + timedelta(seconds=1),
        provisional=ProvisionalIdentity("测试号", "fakeid"),
    )
    reservation = store.reserve_probe(
        account,
        config=config,
        started_at=started + timedelta(hours=2, seconds=1),
        requested_page_size=5,
    )
    with pytest.raises(ValueError, match="precedes"):
        store.complete_probe(
            reservation.attempt_id,
            finished_at=started,
            state=DiscoveryState.AUTH_REQUIRED,
            platform_error_ret=200003,
        )

    completion = store.complete_probe(
        reservation.attempt_id,
        finished_at=started + timedelta(hours=2, seconds=2),
        state=DiscoveryState.IDENTITY_UNVERIFIED,
        target_identity_evidence=(
            TargetIdentityEvidence.ARTICLE_URL_PUBLIC_BIZ_UNAVAILABLE
        ),
    )

    assert completion.state is DiscoveryState.IDENTITY_UNVERIFIED
    assert completion.returned_article_count == 0
    assert store.attempt_candidates(reservation.attempt_id) == []
    assert store.identity_resolution(resolution_id).invalidation_reason is None
    assert store.identity_status((account,)) == ((), 1, 0, 0)


def test_newer_request_failure_does_not_mask_an_older_active_mapping(tmp_path) -> None:  # noqa: ANN001
    account = _account()
    config = DiscoveryConfig(2, True, timedelta(0), (account,))
    store = DiscoveryStore(tmp_path / "state.db")
    started = datetime(2026, 8, 13, tzinfo=UTC)
    ready_id = store.reserve_identity_resolution(account, config=config, started_at=started)
    store.complete_identity_resolution(
        ready_id,
        state=IdentityResolutionState.PROVISIONAL_MATCH,
        finished_at=started + timedelta(seconds=1),
        provisional=ProvisionalIdentity("测试号", "fakeid"),
    )
    failed_id = store.reserve_identity_resolution(
        account, config=config, started_at=started + timedelta(seconds=2)
    )
    store.complete_identity_resolution(
        failed_id,
        state=IdentityResolutionState.AUTH_REQUIRED,
        finished_at=started + timedelta(seconds=3),
        platform_error_ret=200003,
    )

    ready, assigned, invalidated, unresolved = store.identity_status((account,))
    assert ready == ((account.name, ready_id),)
    assert (assigned, invalidated, unresolved) == (0, 0, 0)
    reservation = store.reserve_probe(
        account,
        config=config,
        started_at=started + timedelta(seconds=4),
        requested_page_size=5,
    )
    assert reservation.identity_resolution_id == ready_id


def test_raw_v5_schema_rejects_missing_resolved_identity_and_empty_invalidation_reason(
    tmp_path,
) -> None:  # noqa: ANN001
    store = DiscoveryStore(tmp_path / "state.db")
    with store.connect() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO identity_resolution_attempts(
                  started_at, finished_at, configured_account_name,
                    configured_public_biz, outcome, provisional_match_origin,
                    observed_account_name, provisional_fakeid
                ) VALUES (
                  '2026-08-13T00:00:00+00:00', '2026-08-13T00:01:00+00:00',
                  'A', 'Qml6QQ==', 'provisional_match',
                  'searchbiz_unique_normalized_name', NULL, NULL
                )
                """
            )
        conn.rollback()
    resolution_id = store.reserve_identity_resolution(
        _account(),
        config=_config(_account()),
        started_at=datetime(2026, 8, 13, tzinfo=UTC),
    )
    store.complete_identity_resolution(
        resolution_id,
        state=IdentityResolutionState.PROVISIONAL_MATCH,
        finished_at=datetime(2026, 8, 13, 0, 1, tzinfo=UTC),
        provisional=ProvisionalIdentity("测试号", "fakeid"),
    )
    with store.connect() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE identity_resolution_attempts SET invalidated_at=?, "
                "invalidation_reason='' WHERE id=?",
                ("2026-08-13T00:02:00+00:00", resolution_id),
            )


def test_store_normalizes_request_times_to_utc_before_latest_ordering(tmp_path) -> None:  # noqa: ANN001
    account = _account()
    config = DiscoveryConfig(3, True, timedelta(0), (account,))
    store = DiscoveryStore(tmp_path / "state.db")

    first_id = store.reserve_identity_resolution(
        account,
        config=config,
        started_at=datetime.fromisoformat("2026-08-13T08:30:00+08:00"),
    )
    store.complete_identity_resolution(
        first_id,
        state=IdentityResolutionState.AUTH_REQUIRED,
        finished_at=datetime.fromisoformat("2026-08-13T08:31:00+08:00"),
        platform_error_ret=200003,
    )
    second_id = store.reserve_identity_resolution(
        account,
        config=config,
        started_at=datetime.fromisoformat("2026-08-13T01:00:00+00:00"),
    )

    latest = store.latest_backend_request()

    assert latest is not None
    assert latest.id == second_id
    with store.connect() as conn:
        persisted = conn.execute(
            "SELECT started_at FROM identity_resolution_attempts WHERE id=?", (first_id,)
        ).fetchone()[0]
    assert persisted == "2026-08-13T00:30:00+00:00"


def test_store_rejects_naive_request_timestamps(tmp_path) -> None:  # noqa: ANN001
    with pytest.raises(ValueError, match="timezone-aware"):
        DiscoveryStore(tmp_path / "state.db").reserve_identity_resolution(
            _account(),
            config=DiscoveryConfig(3, True, timedelta(0), (_account(),)),
            started_at=datetime(2026, 8, 13),
        )


def test_concurrent_identity_requests_create_only_one_backend_reservation(tmp_path) -> None:  # noqa: ANN001
    account = _account()
    config = _config(account)
    store = DiscoveryStore(tmp_path / "state.db")
    started = datetime(2026, 8, 13, tzinfo=UTC)

    def reserve() -> str:
        try:
            store.reserve_identity_resolution(account, config=config, started_at=started)
        except DiscoveryCooldownActive:
            return "cooldown"
        return "reserved"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(lambda _index: reserve(), range(2)))

    assert outcomes == ["cooldown", "reserved"]
    with store.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM identity_resolution_attempts"
        ).fetchone()[0] == 1


def test_identity_mismatch_invalidates_mapping_and_keeps_candidates_empty(tmp_path) -> None:  # noqa: ANN001
    account = _account()
    config = _config(account)
    store = DiscoveryStore(tmp_path / "state.db")
    started = datetime(2026, 8, 13, tzinfo=UTC)
    resolution_id = store.reserve_identity_resolution(account, config=config, started_at=started)
    store.complete_identity_resolution(
        resolution_id,
        state=IdentityResolutionState.PROVISIONAL_MATCH,
        finished_at=started + timedelta(minutes=1),
        provisional=ProvisionalIdentity("测试号", "verified-fakeid"),
    )
    probe = store.reserve_probe(
        account,
        config=config,
        started_at=started + timedelta(hours=2, minutes=1),
        requested_page_size=5,
    )

    completion = store.complete_probe(
        probe.attempt_id,
        finished_at=started + timedelta(hours=2, minutes=2),
        state=DiscoveryState.IDENTITY_MISMATCH,
        target_identity_evidence=(
            TargetIdentityEvidence.ARTICLE_URL_PUBLIC_BIZ_MISMATCH
        ),
    )

    assert completion.returned_article_count == 0
    resolution = store.identity_resolution(resolution_id)
    assert resolution is not None
    assert resolution.invalidated_at is not None
    assert resolution.invalidation_reason == "article_url_biz_mismatch"
    assert store.attempt_candidates(probe.attempt_id) == []
    assert "did not complete successfully" in (
        store.attempt_identity_issue(probe.attempt_id) or ""
    )


def test_explicit_compare_identity_check_rejects_a_missing_resolution_reference(
    tmp_path,
) -> None:  # noqa: ANN001
    store = DiscoveryStore(tmp_path / "state.db")
    with store.connect() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO discovery_attempts(
                  started_at, finished_at, outcome,
                  legacy_target_account_name, legacy_target_public_biz,
                  requested_page_size, requested_page_size_origin,
                  identity_resolution_id, identity_resolution_origin
                ) VALUES (
                  '2026-08-13T00:00:00+00:00', '2026-08-13T00:01:00+00:00',
                  'success', NULL, NULL, 5, 'recorded', 999, 'verified_resolution'
                )
                """
            )
    assert store.attempt(1) is None
