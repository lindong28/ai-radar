from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request

import pytest

from airadar.wechat_discovery.models import AccountConfig, IdentityProof
from airadar.wechat_discovery.protocol import (
    DiscoveryAuthRequired,
    DiscoveryCredentials,
    DiscoveryIdentityMismatch,
    DiscoveryIdentityUnverified,
    DiscoveryPlatformRejected,
    DiscoveryRateLimited,
    DiscoveryRequestFailed,
    DiscoveryResponseInvalid,
    WeChatAdminClient,
    _default_request_json,
    bootstrap_biz,
    load_credentials,
    parse_appmsgpublish,
    verify_account_identity,
)


def _payload(*articles: dict[str, object]) -> dict[str, object]:
    return {
        "base_resp": {"ret": 0, "err_msg": "ok"},
        "publish_page": json.dumps(
            {
                "publish_list": [
                    {
                        "publish_info": json.dumps(
                            {"appmsgex": list(articles)}, ensure_ascii=False
                        )
                    }
                ]
            },
            ensure_ascii=False,
        ),
    }


def test_parse_appmsgpublish_flattens_multi_article_publish_and_normalizes_fields() -> None:
    payload = _payload(
        {
            "aid": "1",
            "title": "主文章",
            "link": "https://mp.weixin.qq.com/s?__biz=YWJj&mid=1#frag",
            "author": "作者 A",
            "update_time": 1786500000,
        },
        {
            "aid": "2",
            "title": "次条",
            "link": "https://mp.weixin.qq.com/s?__biz=YWJj&mid=2&idx=1&sn=second",
            "create_time": "1786500060",
        },
    )

    articles = parse_appmsgpublish(payload, account_name="测试号", expected_biz="YWJj")

    assert [article.title for article in articles] == ["主文章", "次条"]
    assert articles[0].url == "https://mp.weixin.qq.com/s?__biz=YWJj&mid=1"
    assert articles[0].author == "作者 A"
    assert articles[1].author == "测试号"
    assert articles[0].published_at == datetime.fromtimestamp(1786500000, UTC)
    assert articles[1].published_at == datetime.fromtimestamp(1786500060, UTC)
    assert all(article.biz == "YWJj" for article in articles)


def test_parse_appmsgpublish_rejects_duplicate_article_urls_before_snapshot() -> None:
    duplicate = {
        "aid": "1",
        "title": "重复文章",
        "link": "https://mp.weixin.qq.com/s?__biz=YWJj&mid=1&idx=1&sn=duplicate",
        "update_time": 1786500000,
    }

    with pytest.raises(DiscoveryResponseInvalid, match="duplicate article URL"):
        parse_appmsgpublish(
            _payload(duplicate, {**duplicate, "aid": "2", "title": "重复槽位"}),
            account_name="测试号",
            expected_biz="YWJj",
        )


def test_parse_appmsgpublish_rejects_a_partially_skipped_publish_group() -> None:
    valid = {
        "aid": "1",
        "title": "可解析文章",
        "link": "https://mp.weixin.qq.com/s?__biz=YWJj&mid=1&idx=1&sn=valid",
        "update_time": 1786500000,
    }
    payload = {
        "base_resp": {"ret": 0, "err_msg": "ok"},
        "publish_page": {
            "publish_list": [
                {"publish_info": {"appmsgex": [valid]}},
                {"unexpected_shape": True},
            ]
        },
    }

    with pytest.raises(DiscoveryResponseInvalid, match="publish_info"):
        parse_appmsgpublish(
            payload,
            account_name="测试号",
            expected_biz="YWJj",
        )


def test_parse_appmsgpublish_keeps_empty_page_distinct_from_invalid_shape() -> None:
    articles = parse_appmsgpublish(
        {
            "base_resp": {"ret": 0, "err_msg": "ok"},
            "publish_page": {"publish_list": []},
        },
        account_name="测试号",
        expected_biz="YWJj",
    )

    assert articles == []


@pytest.mark.parametrize(
    "url",
    (
        "https://mp.weixin.qq.com/s?mid=1&idx=1&sn=missing",
        "https://mp.weixin.qq.com/s?__biz=&mid=1&idx=1&sn=empty",
        "https://mp.weixin.qq.com/s?__biz=YWJj&__biz=YWJj&mid=1&idx=1&sn=duplicate",
    ),
)
def test_parse_appmsgpublish_marks_missing_unique_url_biz_as_unverified(url: str) -> None:
    with pytest.raises(
        DiscoveryIdentityUnverified, match="unique observed account biz"
    ) as caught:
        parse_appmsgpublish(
            _payload(
                {
                    "title": "身份不可验证",
                    "link": url,
                    "update_time": 1786500000,
                }
            ),
            account_name="测试号",
            expected_biz="YWJj",
        )
    assert type(caught.value) is DiscoveryIdentityUnverified


def test_parse_appmsgpublish_marks_distinct_url_biz_as_mismatch() -> None:
    with pytest.raises(DiscoveryIdentityMismatch, match="does not match"):
        parse_appmsgpublish(
            _payload(
                {
                    "title": "身份冲突",
                    "link": "https://mp.weixin.qq.com/s?__biz=ZGlmZmVyZW50&mid=1&idx=1&sn=wrong",
                    "update_time": 1786500000,
                }
            ),
            account_name="测试号",
            expected_biz="YWJj",
        )


def test_parse_appmsgpublish_keeps_non_wechat_url_as_response_invalid() -> None:
    with pytest.raises(DiscoveryResponseInvalid, match="invalid public article URL"):
        parse_appmsgpublish(
            _payload(
                {
                    "title": "坏链接",
                    "link": "https://evil.example/s?__biz=YWJj",
                    "update_time": 1786500000,
                }
            ),
            account_name="测试号",
            expected_biz="YWJj",
        )


@pytest.mark.parametrize(
    ("base_resp", "error_type"),
    [
        ({"ret": 200003, "err_msg": "session expired token=secret"}, DiscoveryAuthRequired),
        ({"ret": -3, "err_msg": "no session cookie=secret"}, DiscoveryAuthRequired),
        ({"ret": 200013, "err_msg": "freq control cookie=secret"}, DiscoveryRateLimited),
    ],
)
def test_parse_classifies_platform_failures_without_echoing_sensitive_response(
    base_resp: dict[str, object],
    error_type: type[Exception],
) -> None:
    with pytest.raises(error_type) as caught:
        parse_appmsgpublish(
            {"base_resp": base_resp}, account_name="测试号", expected_biz="YWJj"
        )

    message = str(caught.value).lower()
    assert "secret" not in message
    assert "cookie" not in message
    assert "token" not in message


def test_parse_keeps_invalid_args_distinct_from_frequency_control() -> None:
    with pytest.raises(DiscoveryPlatformRejected) as caught:
        parse_appmsgpublish(
            {"base_resp": {"ret": 200002, "err_msg": "invalid args token=secret"}},
            account_name="测试号",
            expected_biz="YWJj",
        )

    assert caught.value.platform_error_ret == 200002
    assert "secret" not in str(caught.value).lower()

    with pytest.raises(DiscoveryRateLimited) as rate_limited:
        parse_appmsgpublish(
            {"base_resp": {"ret": 200013, "err_msg": "frequency control"}},
            account_name="测试号",
            expected_biz="YWJj",
        )

    assert rate_limited.value.platform_error_ret == 200013


@pytest.mark.parametrize("ret", [True, False, "200013", 200013.5])
def test_parse_rejects_noninteger_platform_ret(ret: object) -> None:
    with pytest.raises(DiscoveryResponseInvalid, match="invalid base_resp.ret"):
        parse_appmsgpublish(
            {"base_resp": {"ret": ret, "err_msg": "fixture"}},
            account_name="测试号",
            expected_biz="YWJj",
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"base_resp": {"ret": 0}, "publish_page": "not-json"},
        {"base_resp": {"ret": 0}},
        {"base_resp": {"ret": 0}, "publish_page": {"publish_list": "wrong"}},
        _payload({"title": "缺链接", "update_time": 1786500000}),
    ],
)
def test_parse_rejects_success_shaped_but_unusable_payload(payload: dict[str, object]) -> None:
    with pytest.raises(DiscoveryResponseInvalid):
        parse_appmsgpublish(payload, account_name="测试号", expected_biz="YWJj")


def test_bootstrap_biz_tries_multiple_seed_pages() -> None:
    pages = [
        "<html><body>没有 biz</body></html>",
        "<script>var biz = \"TXpJeU16QTVOakV5TUE9PQ==\";</script>",
    ]

    assert bootstrap_biz(pages) == "TXpJeU16QTVOakV5TUE9PQ=="


def test_credentials_require_private_permissions_and_never_reveal_secrets(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "token": "top-secret-token",
                "cookies": [
                    {
                        "name": "session",
                        "value": "top-secret-cookie",
                        "domain": ".weixin.qq.com",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)

    credentials = load_credentials(path)

    assert credentials.token == "top-secret-token"
    assert credentials.cookie_header == "session=top-secret-cookie"
    rendered = f"{credentials!r} {credentials}"
    assert "top-secret" not in rendered

    path.chmod(0o644)
    with pytest.raises(PermissionError, match="private permissions"):
        load_credentials(path)


def test_credentials_reject_unknown_version_and_lookalike_cookie_domain(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    path.write_text(
        json.dumps(
            {
                "version": 2,
                "token": "secret",
                "cookies": [{"name": "session", "value": "secret", "domain": ".weixin.qq.com"}],
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)
    with pytest.raises(ValueError, match="session version"):
        load_credentials(path)

    path.write_text(
        json.dumps(
            {
                "version": 1,
                "token": "secret",
                "cookies": [
                    {"name": "session", "value": "secret", "domain": "notweixin.qq.com"}
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="no applicable cookies"):
        load_credentials(path)

    path.write_text(
        json.dumps(
            {
                "version": 1,
                "token": "secret",
                "cookies": [{"name": "session", "value": "secret", "domain": ".weixin.qq.com"}],
                "future_field": True,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown fields"):
        load_credentials(path)


def test_identity_verification_requires_matching_seed_name_and_biz() -> None:
    account = AccountConfig(
        "测试号",
        "Qml6QQ==",
        ("https://mp.weixin.qq.com/s/seed",),
        IdentityProof(
            "https://mp.weixin.qq.com/s/seed",
            "测试号",
            "Qml6QQ==",
            "2026-08-13",
        ),
    )
    verify_account_identity(account)
    with pytest.raises(DiscoveryIdentityUnverified, match="does not match"):
        verify_account_identity(
            AccountConfig(
                "测试号",
                "Qml6QQ==",
                ("https://mp.weixin.qq.com/s/seed",),
                IdentityProof(
                    "https://mp.weixin.qq.com/s/seed",
                    "另一个号",
                    "Qml6QQ==",
                    "2026-08-13",
                ),
            )
        )


def test_default_request_json_classifies_malformed_json_as_invalid(monkeypatch) -> None:  # noqa: ANN001
    class Response:
        def __enter__(self):  # noqa: ANN204
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return b"<html>login</html>"

    monkeypatch.setattr(
        "airadar.wechat_discovery.protocol.open_external_url",
        lambda *_args, **_kwargs: Response(),
    )
    with pytest.raises(DiscoveryResponseInvalid, match="not valid JSON"):
        _default_request_json(Request("https://mp.weixin.qq.com/"), 1)


def test_admin_client_builds_expected_request_without_leaking_credentials() -> None:
    captured: dict[str, object] = {}

    def request_json(request: Request, timeout: float) -> dict[str, object]:
        captured["url"] = request.full_url
        captured["cookie"] = request.get_header("Cookie")
        captured["timeout"] = timeout
        return _payload(
            {
                "title": "文章",
                "link": "https://mp.weixin.qq.com/s?__biz=YWJj&mid=1&idx=1&sn=article",
                "update_time": 1786500000,
            }
        )

    credentials = DiscoveryCredentials(token="top-secret-token", cookie_header="session=top-secret-cookie")
    client = WeChatAdminClient(credentials, request_json=request_json, timeout_seconds=7)

    articles = client.fetch_latest(account_name="测试号", biz="YWJj", fakeid="verified-fakeid")

    assert [article.title for article in articles] == ["文章"]
    assert "appmsgpublish" in str(captured["url"])
    assert "fakeid=verified-fakeid" in str(captured["url"])
    assert "count=5" in str(captured["url"])
    assert "token=top-secret-token" in str(captured["url"])
    assert captured["cookie"] == "session=top-secret-cookie"
    assert captured["timeout"] == 7
    assert "top-secret" not in repr(client)


def test_admin_client_classifies_transport_failure_without_echoing_exception() -> None:
    def request_json(_request: Request, _timeout: float) -> dict[str, object]:
        raise OSError("connection failed with token=top-secret")

    client = WeChatAdminClient(
        DiscoveryCredentials(token="top-secret-token", cookie_header="session=top-secret-cookie"),
        request_json=request_json,
    )

    with pytest.raises(DiscoveryRequestFailed) as caught:
        client.fetch_latest(account_name="测试号", biz="YWJj", fakeid="verified-fakeid")

    assert "top-secret" not in str(caught.value)
