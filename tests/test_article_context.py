from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from airadar.enrich.article_context import UnsafeTarget, assert_public_http, input_digest, prepare_article_context


@pytest.mark.parametrize("host", ["localhost", "localhost.", "127.1", "127.0.0.1", "2130706433", "0x7f000001", "[::1]", "169.254.169.254", "10.0.0.1", "service.local"])
def test_actual_url_guard_rejects_internal_targets(host):
    with pytest.raises(UnsafeTarget):
        assert_public_http("https://" + host + "/article")


def test_url_guard_accepts_public_and_rejects_non_http():
    assert_public_http("https://techcrunch.com/article")
    assert_public_http("https://8.8.8.8/article")
    with pytest.raises(UnsafeTarget):
        assert_public_http("file:///etc/passwd")


def test_http_helper_checks_redirect_and_bounds_decoded_body(monkeypatch):
    import httpx

    from airadar.enrich import article_context

    visited = []
    def redirect(request):
        visited.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.1/private"})

    monkeypatch.setattr(article_context, "selector_httpx_client", lambda **_: httpx.Client(transport=httpx.MockTransport(redirect)))
    body, detail = article_context.fetch_article("https://example.com/article", 1)
    assert not body and detail.startswith("UNSAFE")
    assert visited == ["https://example.com/article"]
    monkeypatch.setattr(article_context, "MAX_BYTES", 10)
    monkeypatch.setattr(article_context, "selector_httpx_client", lambda **_: httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, content=b"x" * 11, headers={"content-type": "text/html"}))))
    body, detail = article_context.fetch_article("https://example.com/article", 1)
    assert not body and detail.startswith("过大")


def test_context_fallback_cache_and_input_binding(tmp_path):
    raw = {"source_kind": "feed", "url": "https://example.com/a", "content_text": "short", "title": "标题"}
    calls = []

    def fetch(url, timeout):
        calls.append(url)
        return "", "HTTP 403"

    failed = prepare_article_context(raw, cache_dir=tmp_path, fetcher=fetch)
    assert failed["status"] == "unavailable" and failed["content_text"] == "short"
    assert failed["detail"] == "HTTP 403"
    assert prepare_article_context(raw, cache_dir=tmp_path, fetcher=fetch) == failed
    assert len(calls) == 1
    cached_path = next(tmp_path.glob("*.json"))
    cached = json.loads(cached_path.read_text())
    cached["fetched_at"] = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    cached_path.write_text(json.dumps(cached))
    prepare_article_context(raw, cache_dir=tmp_path, fetcher=fetch)
    assert len(calls) == 2
    changed = {**raw, "content_text": "changed"}
    prepare_article_context(changed, cache_dir=tmp_path, fetcher=fetch)
    assert len(calls) == 3
    assert raw["content_text"] == "short"


@pytest.mark.parametrize("kind", ["x", "wechat", "web", None])
def test_non_feed_never_fetches(kind):
    def forbidden(*args):
        pytest.fail("non-feed fetched")
    assert prepare_article_context({"source_kind": kind}, fetcher=forbidden)["status"] == "not_applicable"


def test_available_retains_full_body_and_hash_matches_eval_assets(tmp_path):
    from evals._shared.assets import digest
    raw = {"source_kind": "feed", "url": "https://example.com/a", "content_text": "short", "title": "标题"}
    body = "first paragraph " * 600 + "end of article"
    result = prepare_article_context(raw, cache_dir=tmp_path, fetcher=lambda *_: (body, "ok"))
    assert result["status"] == "available" and result["content_text"].endswith("end of article")
    assert result["original_input_sha256"] == digest(raw) == input_digest(raw)
    assert datetime.fromisoformat(result["fetched_at"]).tzinfo is not None


def test_enrich_consumer_receives_body_preserves_raw_and_records_context(tmp_path, monkeypatch):
    from test_enrich_runner import _db

    from airadar.enrich import article_context, runner_v2
    from airadar.enrich.prompts_v2 import render_enrich_prompt

    conn = _db(tmp_path)
    original = conn.execute("SELECT content_text FROM items").fetchone()[0]
    body = "actual article " * 600 + "END_MARKER"
    calls = []
    monkeypatch.setattr(article_context, "fetch_article", lambda *_: (calls.append(1) or body, "ok"))

    class Provider:
        model_id = "fake"
        def enrich(self, item):
            assert item.content_text.startswith(original)
            assert item.content_text.endswith(body)
            assert "END_MARKER" in render_enrich_prompt(item)["user"]
            raise RuntimeError("deliberate provider failure")

    for _ in range(2):
        summary = runner_v2.run_enrich(conn, provider=Provider(), since="24h")
        assert summary.processed == summary.errors == 1
    assert len(calls) == 1
    assert conn.execute("SELECT content_text FROM items").fetchone()[0] == original
    stored = json.loads(conn.execute("SELECT input_json FROM item_evaluations WHERE stage='enrich' LIMIT 1").fetchone()[0])
    assert stored["article_context"]["status"] == "available"
    assert stored["article_context"]["content_text"] == body
    assert "END_MARKER" in stored["user"]


def test_legacy_helper_import_is_same_implementation():
    from airadar.enrich import article_context
    from scripts.eval import build_body_overlay
    assert build_body_overlay.fetch_article is article_context.fetch_article
    assert build_body_overlay.assert_public_http is article_context.assert_public_http


def test_context_cli_preserves_unicode_line_separators(tmp_path, monkeypatch):
    from scripts.eval.category_body_context import main
    cases = tmp_path / "cases.jsonl"
    output = tmp_path / "context.jsonl"
    body = "paragraph\u2028next\u0085last"
    rows = [{"case_id": str(i), "input": {"source_kind": "x", "content_text": body}} for i in range(2)]
    cases.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
    monkeypatch.setattr("sys.argv", ["category_body_context", "--cases", str(cases), "--output", str(output)])
    main()
    with output.open() as stream:
        contexts = [json.loads(line) for line in stream]
    assert len(contexts) == 2
    assert all(row["content_text"] == body for row in contexts)


def test_oversize_extraction_and_legacy_cache_preserve_original(tmp_path):
    from airadar.enrich.article_context import MAX_ARTICLE_CHARS
    raw = {"source_kind": "feed", "url": "https://example.com/changelog", "content_text": "one release"}
    oversized = "a" * (MAX_ARTICLE_CHARS + 1)
    result = prepare_article_context(raw, cache_dir=tmp_path, fetcher=lambda *_: (oversized, "ok"))
    assert result["status"] == "unavailable"
    assert result["detail"] == "article exceeds extraction bound"
    assert result["content_text"] == raw["content_text"]
    path = next(tmp_path.glob("*.json"))
    legacy = {**result, "status": "available", "content_text": oversized}
    path.write_text(json.dumps(legacy))
    def no_fetch(*_):
        pytest.fail("cached oversize body should be rejected without a new request")
    cached = prepare_article_context(raw, cache_dir=tmp_path, fetcher=no_fetch)
    assert cached["status"] == "unavailable" and cached["content_text"] == raw["content_text"]
    assert cached["detail"] == result["detail"]
    boundary = prepare_article_context(raw, fetcher=lambda *_: ("b" * MAX_ARTICLE_CHARS, "ok"))
    assert boundary["status"] == "available" and len(boundary["content_text"]) == MAX_ARTICLE_CHARS


def test_login_redirect_and_password_form_are_not_article_context(monkeypatch):
    import httpx

    from airadar.enrich import article_context
    raw = {"source_kind": "feed", "url": "https://example.com/story", "title": "New model release",
           "content_text": "A model was released with measured gains."}
    login = '<html><body><article><h1>Sign in</h1><p>' + ('Sign in or subscribe to continue reading. ' * 30) + '</p></article></body></html>'
    def serve(request):
        if request.url.path == "/story":
            return httpx.Response(302, headers={"Location": "/login"})
        return httpx.Response(200, headers={"content-type": "text/html"}, text=login)
    monkeypatch.setattr(article_context, "selector_httpx_client", lambda **_: httpx.Client(transport=httpx.MockTransport(serve)))
    result = prepare_article_context(raw)
    assert result["status"] == "unavailable" and result["detail"] == "authentication redirect"
    assert result["content_text"] == raw["content_text"]
    monkeypatch.setattr(article_context, "selector_httpx_client", lambda **_: httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, headers={"content-type": "text/html"}, text=login + '<form><input type="password"></form>'))))
    result = prepare_article_context(raw)
    assert result["status"] == "unavailable" and result["detail"] == "authentication form"


@pytest.mark.parametrize("success", [True, False])
def test_unwritable_cache_does_not_discard_fetch_result(tmp_path, monkeypatch, success):
    from pathlib import Path
    raw = {"source_kind": "feed", "url": "https://example.com/story", "content_text": "original"}
    def denied(*_, **__):
        raise PermissionError("not writable")
    monkeypatch.setattr(Path, "mkdir", denied)
    result = prepare_article_context(raw, cache_dir=tmp_path / "cache", fetcher=lambda *_: ("long article material", "ok") if success else ("", "HTTP 403"))
    assert result["status"] == ("available" if success else "unavailable")
    assert result["content_text"] == ("long article material" if success else "original")
    assert "cache write failed: PermissionError" in result["detail"]


def test_unexpected_fetch_exception_isolated():
    def broken(*_):
        raise RuntimeError("fixture failure")
    raw = {"source_kind": "feed", "url": "https://example.com/story", "content_text": "original"}
    result = prepare_article_context(raw, fetcher=broken)
    assert result["status"] == "unavailable" and result["content_text"] == "original"
    assert "RuntimeError" in result["detail"]


def test_production_enrich_gets_collected_quotes_and_missing_status(tmp_path, monkeypatch):
    from test_enrich_runner import _add_prefiltered_item, _db

    from airadar.enrich import article_context, runner_v2
    conn = _db(tmp_path)
    original_id, original_body = conn.execute("SELECT id,content_text FROM items").fetchone()
    quoted_id = _add_prefiltered_item(conn, "Quoted evidence", 40)
    quote_body = "Evidence from the collected original post."
    conn.execute("UPDATE sources SET kind='x'")
    conn.execute("UPDATE items SET extra_json=?, content_text=? WHERE id=?",
                 (json.dumps({"x_post_id": "42"}), quote_body, quoted_id))
    conn.execute("UPDATE items SET extra_json=? WHERE id=?", (json.dumps({"referenced_tweets": [
        {"id": "42", "type": "quoted"}, {"id": "43", "type": "quoted"},
        {"id": "44", "type": "retweeted"}]}), original_id))
    conn.commit()
    def forbidden(*_):
        pytest.fail("X quote preparation must not fetch")
    monkeypatch.setattr(article_context, "fetch_article", forbidden)
    class Provider:
        model_id = "fake"
        def enrich(self, item):
            assert item.content_text.startswith(original_body)
            assert quote_body in item.content_text
            assert '"status": "missing_in_database"' in item.content_text
            assert '"post_id": "44"' not in item.content_text
            raise RuntimeError("deliberate provider failure")
    result = runner_v2.run_enrich(conn, provider=Provider(), item_ids=[original_id], workers=2)
    assert result.processed == result.errors == 1
    saved = json.loads(conn.execute("SELECT input_json FROM item_evaluations WHERE stage='enrich'").fetchone()[0])
    assert [(q["post_id"], q["status"]) for q in saved["quote_context"]] == [("42", "available"), ("43", "missing_in_database")]
    assert saved["quote_context"][0]["content_text"] == quote_body
    assert saved["quote_context"][0]["url"] and saved["quote_context"][0]["fetched_at"]
    assert conn.execute("SELECT content_text FROM items WHERE id=?", (original_id,)).fetchone()[0] == original_body


def test_quote_lookup_scope_empty_and_conflicting_material(tmp_path):
    from test_enrich_runner import _add_prefiltered_item, _db

    from airadar.enrich.quote_context import collected_quotes
    conn = _db(tmp_path)
    refs = json.dumps({"referenced_tweets": [{"id": "42", "type": "quoted"}]})
    assert collected_quotes(conn, "feed", refs) == []
    assert collected_quotes(conn, "x", "broken") == [{"status": "invalid_reference_metadata"}]
    conn.execute("UPDATE sources SET kind='x'")
    conn.execute("UPDATE items SET extra_json=?,content_text=''", (json.dumps({"x_post_id": "42"}),))
    assert collected_quotes(conn, "x", refs)[0]["status"] == "empty_body"
    other = _add_prefiltered_item(conn, "Conflicting snapshot", 40)
    conn.execute("UPDATE items SET extra_json=? WHERE id=?", (json.dumps({"x_post_id": "42"}), other))
    assert collected_quotes(conn, "x", refs)[0]["status"] == "ambiguous"
