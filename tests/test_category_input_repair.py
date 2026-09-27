import json
import sqlite3

import pytest

from airadar.enrich.quote_context import collected_quotes, render_collected_quotes
from airadar.fetcher.x_api import _post_extra
from evals._shared.aihot_inputs import original_body


def test_quote_expansion_survives_fetch_to_enrichment_without_database_row():
    post = {"id": "1", "referenced_tweets": [{"type": "quoted", "id": "2"},
                                             {"type": "replied_to", "id": "3"}]}
    payload = {"includes": {"tweets": [
        {"id": "2", "author_id": "a", "text": "truncated", "note_tweet": {"text": "full original"}},
        {"id": "3", "text": "reply must not enter"}],
        "users": [{"id": "a", "username": "author"}]}}
    extra = _post_extra(post, "parent", payload=payload)
    conn = sqlite3.connect(":memory:")  # no items table: successful path must use expansion
    quotes = collected_quotes(conn, "x", json.dumps(extra))
    assert len(quotes) == 1 and quotes[0]["content_text"] == "full original"
    assert quotes[0]["author"] == "@author"
    assert "reply must not enter" not in render_collected_quotes(quotes)


def test_missing_or_wrong_expansion_is_not_available():
    post = {"id": "1", "referenced_tweets": [{"type": "quoted", "id": "2"}]}
    extra = _post_extra(post, "parent", payload={"includes": {"tweets": [{"id": "3", "text": "wrong"}]}})
    assert extra["x_quoted_posts"][0]["status"] == "unavailable"
    assert not extra["x_quoted_posts"][0]["content_text"]


def test_original_tab_replaces_translation_without_reference_answer_leak():
    html = ('<div id="detail-article-a" class="dt-article-content"><div class="dt-article">翻译</div></div>'
            '<div>AI category: paper</div><template id="detail-rich-html-a-original"><p>Actual English</p>'
            '<script>bad</script></template><template id="detail-rich-html-other-original">WRONG</template>')
    assert original_body(html, "a") == "Actual English"
    with pytest.raises(ValueError, match="ambiguous"):
        original_body(html + '<template id="detail-rich-html-a-original">duplicate</template>', "a")


def test_repair_retains_longer_previous_body_and_materials(monkeypatch, tmp_path):
    from airadar.enrich.article_context import input_digest
    from scripts.eval import category_input_repair as repair
    raw = {"url": "https://example.org/article", "source_kind": "web", "content_text": "stub"}
    material = {"kind": "quoted-post", "status": "available", "content_text": "original quote", "fetched_at": "then"}
    old = {"a": {"url": raw["url"], "original_input_sha256": input_digest(raw), "status": "available",
                  "content_text": "long body " * 200 + "decisive tail", "repair_materials": [material]}}
    monkeypatch.setattr(repair, "prepare_article_context", lambda _: {"status": "available", "content_text": "short new summary"})
    got = repair.prepare({"case_id": "a", "input": raw, "provenance": {}}, old=old,
                         evidence_root=tmp_path, posts={}, lookup_time=None, fetch_web=True)
    assert "decisive tail" in got["content_text"]
    assert material in got["repair_materials"]


def test_linked_articles_use_longform_entities_and_direct_quotes():
    from airadar.enrich.article_context import prepare_linked_contexts, render_linked_contexts
    post = {"id": "1", "note_tweet": {"text": "full", "entities": {"urls": [
        {"expanded_url": "https://example.org/paper"}, {"expanded_url": "https://example.org/"},
        {"expanded_url": "https://x.com/a/status/2"}]}}}
    extra = _post_extra(post, "author")
    requested = []
    def fetch(url, timeout):
        requested.append(url)
        return "Full linked material", "ok"
    got = prepare_linked_contexts(extra, [], fetcher=fetch)
    assert requested == ["https://example.org/paper"]
    assert got[0]["parent_post_ids"] == ["1"]
    assert "Full linked material" in render_linked_contexts(got)


def test_database_quote_keeps_link_entities():
    from airadar.enrich.article_context import prepare_linked_contexts
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE sources(id,kind)")
    conn.execute("CREATE TABLE items(id,url,author,content_text,fetched_at,extra_json,source_id)")
    conn.execute("INSERT INTO sources VALUES(1,'x')")
    extra = {"x_post_id": "2", "entities": {"urls": [{"expanded_url": "https://example.org/paper"}]}}
    conn.execute("INSERT INTO items VALUES(1,'https://x.com/a/status/2','a','quote','2026-09-27',?,1)", (json.dumps(extra),))
    quotes = collected_quotes(conn, "x", json.dumps({"referenced_tweets": [{"type": "quoted", "id": "2"}]}))
    got = prepare_linked_contexts({}, quotes, fetcher=lambda u,t: ("linked body", "ok"))
    assert got[0]["content_text"] == "linked body"
    assert got[0]["parent_post_ids"] == ["2"]
