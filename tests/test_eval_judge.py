from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import airadar.eval.judge as judge_module
from airadar.db import migrate
from airadar.eval.judge import (
    TEMPLATE_RE,
    AihotItem,
    JudgeScores,
    MatchedPair,
    RadarItem,
    evaluate_pairs,
    load_aihot_items_from_path,
    load_airadar_items,
    load_airadar_items_by_aihot_urls,
    load_iteration_counter,
    load_known_limit_list,
    match_items,
    parse_aihot_markdown,
    parse_judge_response,
    run_eval,
    write_compare_audit,
)


class FakeJudge:
    model_id = "fake"

    def judge_pair(self, pair):  # noqa: ANN001
        return JudgeScores(
            summary_aihot={key: 9 for key in ("information", "insight", "fluency", "brevity")},
            summary_airadar={key: 8 for key in ("information", "insight", "fluency", "brevity")},
            recommendation_aihot={key: 9 for key in ("uniqueness", "insight", "audience")},
            recommendation_airadar={key: 8 for key in ("uniqueness", "insight", "audience")},
            suggestions=["收紧摘要"],
            raw={"provider": "fake"},
        )


class FakeAudit:
    model_id = "fake-audit"

    def audit_compare(self, payload):  # noqa: ANN001
        assert payload["deterministic_checks"]["matched_pair_count"]["pass"] is True
        return {"verdict": "PASS", "reasons": ["ok"], "required_fixes": []}


def _isolate_eval_plan_inputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "state.md"
    sources_path = tmp_path / "aihot-sources.json"
    counter_path = tmp_path / "iteration-counter.json"
    state_path.write_text("# Test state\n", encoding="utf-8")
    sources_path.write_text(
        json.dumps([{"slug": "openai_blog", "name": "OpenAI Blog"}]) + "\n",
        encoding="utf-8",
    )
    counter_path.write_text('{"step3_6":0,"step4_6":0}\n', encoding="utf-8")
    monkeypatch.setattr(judge_module, "STATE_FILE", state_path)
    monkeypatch.setattr(judge_module, "AIHOT_SOURCES", sources_path)
    monkeypatch.setattr(
        judge_module,
        "load_known_limit_list",
        lambda: load_known_limit_list(state_path),
    )
    monkeypatch.setattr(
        judge_module,
        "load_iteration_counter",
        lambda: load_iteration_counter(counter_path),
    )


def _aihot_markdown() -> str:
    return """
# AIHOT

5月12日

08:00

OpenAI：官网动态（RSS · 排除企业/客户案例）

精选90

OpenAI 发布新工具

这是一段 AI Hot 摘要，介绍 OpenAI 新工具的核心能力、发布背景和开发者价值。 https://example.com/a

OpenAI模型发布教程/实践

---

推荐理由：做 AI 应用的你应该读这篇，因为它把产品变化和开发者收益说清楚了。
"""


def _seed_db(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        INSERT INTO sources (
          id, name, url, tier, enabled, kind, homepage_url, icon_url, meta_json, synced_at
        )
        VALUES (
          'openai_blog', 'OpenAI Blog', 'https://example.com/feed.xml', 'T1', 1, 'feed',
          'https://example.com/', 'https://example.com/favicon.ico', '{}', '2026-05-12T00:00:00Z'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO items (
          id, source_id, url, title, author, published_at, fetched_at,
          content_text, content_html, content_hash, extra_json
        )
        VALUES (
          'item-1', 'openai_blog', 'https://example.com/a?utm_source=test',
          'OpenAI launches a new tool', 'Ada', '2026-05-12T00:00:00Z', '2026-05-12T00:01:00Z',
          'OpenAI new tool developer release', NULL, 'h1', '{}'
        )
        """
    )
    enrich = {
        "title_zh": "OpenAI 发布新工具",
        "summary_zh": "这是一段中文摘要，覆盖核心事实、原因和开发者实际意义，便于快速判断是否继续阅读。",
        "why_recommend": "做 AI 应用的你应该读这篇，因为它提供了明确的开发者实践信号。",
        "tags": ["模型发布", "教程/实践"],
    }
    conn.execute(
        """
        INSERT INTO item_evaluations (
          item_id, stage, ruleset_version, model_id, input_json, output_json,
          numeric_json, latency_ms, cost_usd, evaluated_at, error
        )
        VALUES ('item-1', 'enrich', 'test.r1', 'fake', '{}', ?, NULL, 1, 0, '2026-05-12T00:02:00Z', NULL)
        """,
        (json.dumps(enrich),),
    )
    conn.execute(
        """
        INSERT INTO curation_runs (
          id, ruleset_version, weights_json, threshold, input_eval_ids, output_curated_ids, created_at
        )
        VALUES ('run-1', 'test.r1', '{}', 6.0, '[]', '["item-1"]', '2026-05-12T00:03:00Z')
        """
    )
    conn.execute(
        """
        INSERT INTO curated_items (run_id, item_id, weighted_score, rank, reason_json)
        VALUES ('run-1', 'item-1', 9.2, 1, '{}')
        """
    )
    conn.commit()
    return conn


def _insert_enriched_item(
    conn: sqlite3.Connection,
    *,
    item_id: str,
    url: str,
    title: str,
    source_id: str = "openai_blog",
    content_text: str = "OpenAI developer release",
    tags: list[str] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO items (
          id, source_id, url, title, author, published_at, fetched_at,
          content_text, content_html, content_hash, extra_json
        )
        VALUES (?, ?, ?, ?, 'Ada', '2026-05-12T00:00:00Z', '2026-05-12T00:01:00Z', ?, NULL, ?, '{}')
        """,
        (item_id, source_id, url, title, content_text, f"h-{item_id}"),
    )
    enrich = {
        "title_zh": title,
        "summary_zh": "这是一段中文摘要，覆盖核心事实、原因和开发者实际意义，便于快速判断是否继续阅读。",
        "why_recommend": "开发者应关注这条变化，它直接影响近期 AI 应用和工具链选择。",
        "tags": tags or ["模型发布", "教程/实践"],
    }
    conn.execute(
        """
        INSERT INTO item_evaluations (
          item_id, stage, ruleset_version, model_id, input_json, output_json,
          numeric_json, latency_ms, cost_usd, evaluated_at, error
        )
        VALUES (?, 'enrich', 'test.r1', 'fake', '{}', ?, NULL, 1, 0, '2026-05-12T00:02:00Z', NULL)
        """,
        (
            item_id,
            json.dumps(enrich),
        ),
    )


def test_parse_aihot_markdown_extracts_url_tags_and_reason() -> None:
    items = parse_aihot_markdown(_aihot_markdown())

    assert len(items) == 1
    assert items[0].url == "https://example.com/a"
    assert items[0].tags == ["OpenAI", "模型发布", "教程/实践"]
    assert items[0].why_recommend.startswith("做 AI 应用")


def test_url_matching_pairs_same_article(tmp_path: Path) -> None:
    conn = _seed_db(tmp_path)
    aihot_items = parse_aihot_markdown(_aihot_markdown())

    _, _, radar_items = load_airadar_items(conn, "2026-05-12")
    pairs, unmatched_radar, unmatched_aihot = match_items(aihot_items, radar_items)

    assert len(pairs) == 1
    assert pairs[0].match_method == "url"
    assert unmatched_radar == []
    assert unmatched_aihot == []


def test_match_items_does_not_fuzzy_pair_different_articles_from_same_source() -> None:
    aihot = AihotItem(
        index=1,
        date_label="5月12日",
        time="08:00",
        source="Berryxia.AI@berryxia",
        score=80,
        title="",
        summary="这是一条关于图像生成提示词结构的 AI Hot 摘要。",
        why_recommend="做图像生成的人应该看。",
        tags=["图像生成", "教程/实践"],
        url=None,
        raw_text="Berryxia.AI 图像生成提示词",
    )
    radar = RadarItem(
        id="radar-1",
        run_id="run",
        rank=1,
        weighted_score=9.2,
        url="https://nitter.net/berryxia/status/2053978304181567961",
        title="小模型微调实战指南",
        title_zh="小模型微调实战指南",
        summary_zh="这是一篇关于微调小模型的文章，和图像生成提示词不是同一篇。",
        why_recommend="做模型微调的人应该看。",
        tags=["教程/实践", "开源生态"],
        source_id="berryxia",
        source_name="Berryxia.AI",
        content_text="fine tuning small models",
    )

    pairs, unmatched_radar, unmatched_aihot = match_items([aihot], [radar])

    assert pairs == []
    assert unmatched_radar == [radar]
    assert unmatched_aihot == [aihot]


def test_match_items_pairs_same_x_status_across_x_and_nitter_domains() -> None:
    aihot = AihotItem(
        index=1,
        date_label="5月12日",
        time="05:10",
        source="OpenAI@OpenAI",
        score=60,
        title="",
        summary="推出Daybreak：面向网络防御者的前沿AI。",
        why_recommend="安全团队可以关注。",
        tags=["产品更新", "安全/对齐"],
        url="https://x.com/OpenAI/status/2053939702110269822",
        raw_text="OpenAI Daybreak",
    )
    radar = RadarItem(
        id="radar-1",
        run_id="run",
        rank=4,
        weighted_score=8.9,
        url="https://nitter.net/OpenAI/status/2053939702110269822#m",
        title="Introducing Daybreak",
        title_zh="OpenAI 发布 Daybreak",
        summary_zh="OpenAI 发布 Daybreak。",
        why_recommend="安全工程师可以关注。",
        tags=["产品更新", "安全/对齐"],
        source_id="openai_x",
        source_name="OpenAI",
        content_text="Daybreak frontier AI for cyber defenders",
    )

    pairs, unmatched_radar, unmatched_aihot = match_items([aihot], [radar])

    assert len(pairs) == 1
    assert pairs[0].match_method == "url"
    assert unmatched_radar == []
    assert unmatched_aihot == []


def test_load_airadar_items_by_aihot_urls_expands_beyond_latest_curated_run(tmp_path: Path) -> None:
    conn = _seed_db(tmp_path)
    _insert_enriched_item(
        conn,
        item_id="item-2",
        url="https://example.com/b",
        title="OpenAI 发布第二个工具",
        content_text="Second OpenAI developer release",
    )
    conn.commit()
    snapshot = tmp_path / "aihot.json"
    snapshot.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "index": 1,
                        "source": "OpenAI Blog",
                        "score": 90,
                        "title": "OpenAI 发布新工具",
                        "summary": "摘要 A https://example.com/a",
                        "why_recommend": "推荐 A",
                        "tags": ["OpenAI", "模型发布"],
                        "url": "https://example.com/a",
                    },
                    {
                        "index": 2,
                        "source": "OpenAI Blog",
                        "score": 88,
                        "title": "OpenAI 发布第二个工具",
                        "summary": "摘要 B https://example.com/b",
                        "why_recommend": "推荐 B",
                        "tags": ["OpenAI", "产品更新"],
                        "url": "https://example.com/b",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    aihot_items = load_aihot_items_from_path(snapshot)

    _, _, curated_items = load_airadar_items(conn, "2026-05-12")
    all_db_items = load_airadar_items_by_aihot_urls(conn, aihot_items)

    assert [item.id for item in curated_items] == ["item-1"]
    assert [item.id for item in all_db_items] == ["item-1", "item-2"]
    assert all_db_items[1].rank == 2


def test_load_aihot_items_from_json_preserves_original_visual_fields(tmp_path: Path) -> None:
    snapshot = tmp_path / "aihot.json"
    snapshot.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "date_label": "5月12日",
                        "time": "05:10",
                        "source": "OpenAI@OpenAI",
                        "score": 60,
                        "title": "",
                        "summary": "推出Daybreak：面向网络防御者的前沿AI。",
                        "why_recommend": "安全团队可以关注。",
                        "tags": ["产品更新", "安全/对齐"],
                        "url": "https://x.com/OpenAI/status/2053939702110269822",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    items = load_aihot_items_from_path(snapshot)

    assert len(items) == 1
    assert items[0].title == ""
    assert items[0].summary == "推出Daybreak：面向网络防御者的前沿AI。"
    assert items[0].url == "https://x.com/openai/status/2053939702110269822"


def test_parse_judge_response_and_borderline_median() -> None:
    parsed = parse_judge_response(
        {
            "v2_summary": {
                "aihot": {"information": 9, "insight": 9, "fluency": 9, "brevity": 9},
                "airadar": {"information": 8, "insight": 8, "fluency": 8, "brevity": 8},
            },
            "v3_recommendation": {
                "aihot": {"uniqueness": 9, "insight": 9, "audience": 9},
                "airadar": {"uniqueness": 8, "insight": 8, "audience": 8},
            },
            "suggestions": ["减少模板化"],
        }
    )

    assert parsed.summary_average("airadar") == 8
    assert parsed.recommendation_average("aihot") == 9
    assert parsed.suggestions == ["减少模板化"]


def test_template_phrase_detection() -> None:
    assert TEMPLATE_RE.search("属于OpenAI产品，来自官网；包含安全信号")


def test_run_eval_writes_report_and_compare_html(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolate_eval_plan_inputs(monkeypatch, tmp_path)
    conn = _seed_db(tmp_path)
    markdown_path = tmp_path / "aihot.md"
    markdown_path.write_text(_aihot_markdown(), encoding="utf-8")

    artifacts = run_eval(
        conn,
        selected_date="2026-05-12",
        aihot_markdown_path=markdown_path,
        output_dir=tmp_path,
        provider=FakeJudge(),
    )

    assert artifacts.report_path.exists()
    assert artifacts.compare_path.exists()
    report = artifacts.report_path.read_text(encoding="utf-8")
    html = artifacts.compare_path.read_text(encoding="utf-8")
    assert "## V1 Source Coverage" in report
    assert "## V2 Summary Quality" in report
    assert "## V5 Score Distribution" in report
    assert 'class="compare-pair"' in html
    assert artifacts.matched_count == 1
    assert artifacts.sample_count == 1


def test_run_eval_all_db_url_match_scope_writes_expanded_pairs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolate_eval_plan_inputs(monkeypatch, tmp_path)
    conn = _seed_db(tmp_path)
    _insert_enriched_item(
        conn,
        item_id="item-2",
        url="https://example.com/b",
        title="OpenAI 发布第二个工具",
        content_text="Second OpenAI developer release",
    )
    conn.commit()
    snapshot = tmp_path / "aihot.json"
    snapshot.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "index": 1,
                        "source": "OpenAI Blog",
                        "score": 90,
                        "title": "OpenAI 发布新工具",
                        "summary": "摘要 A",
                        "why_recommend": "推荐 A",
                        "tags": ["OpenAI", "模型发布"],
                        "url": "https://example.com/a",
                    },
                    {
                        "index": 2,
                        "source": "OpenAI Blog",
                        "score": 88,
                        "title": "OpenAI 发布第二个工具",
                        "summary": "摘要 B",
                        "why_recommend": "推荐 B",
                        "tags": ["OpenAI", "产品更新"],
                        "url": "https://example.com/b",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    artifacts = run_eval(
        conn,
        selected_date="2026-05-12",
        aihot_markdown_path=snapshot,
        output_dir=tmp_path,
        provider=FakeJudge(),
        match_scope="all-db-url",
    )

    html = artifacts.compare_path.read_text(encoding="utf-8")
    assert artifacts.matched_count == 2
    assert html.count('class="compare-pair"') == 2
    assert "DB-wide URL-proven same-article comparison set" in html


def test_evaluate_pairs_uses_provider() -> None:
    aihot = parse_aihot_markdown(_aihot_markdown())[0]

    radar = RadarItem(
        id="item",
        run_id="run",
        rank=1,
        weighted_score=9.2,
        url="https://example.com/a",
        title="OpenAI launches",
        title_zh="OpenAI 发布",
        summary_zh="这是一段足够长的中文摘要，覆盖事实和意义。",
        why_recommend="做 AI 应用的你应该读这篇，因为它很有价值。",
        tags=["模型发布", "教程/实践"],
        source_id="openai_blog",
        source_name="OpenAI Blog",
        content_text="OpenAI launches",
    )
    result = evaluate_pairs([MatchedPair(aihot, radar, "url", 1.0)], FakeJudge())

    assert len(result) == 1
    assert result[0][1].summary_average("airadar") == 8


def test_write_compare_audit_requires_url_pairs_and_short_reasons(tmp_path: Path) -> None:
    aihot = AihotItem(
        index=1,
        date_label="5月12日",
        time="08:00",
        source="OpenAI",
        score=90,
        title="OpenAI 发布新工具",
        summary="AI Hot 摘要",
        why_recommend="工程团队和产品负责人可以快速判断这项更新是否会影响近期工具链选择。",
        tags=["OpenAI", "模型发布"],
        url="https://example.com/a",
        raw_text="OpenAI 发布新工具",
    )
    pairs = [
        MatchedPair(
            aihot=AihotItem(**{**aihot.to_dict(), "index": index, "url": f"https://example.com/{index}"}),
            airadar=RadarItem(
                id=f"item-{index}",
                run_id="run",
                rank=index,
                weighted_score=9.0,
                url=f"https://example.com/{index}",
                title="OpenAI launches a new tool",
                title_zh="OpenAI 发布新工具",
                summary_zh="这是一段足够长的中文摘要，覆盖核心事实和工程意义。",
                why_recommend="工程团队和产品负责人应关注这项更新，它会影响近期 AI 工具链和产品判断。",
                tags=["OpenAI", "模型发布", "教程/实践"],
                source_id="openai_blog",
                source_name="OpenAI Blog",
                content_text="OpenAI developer tool",
            ),
            match_method="url",
            match_score=1.0,
        )
        for index in range(1, 11)
    ]
    compare_path = tmp_path / "compare.html"
    compare_path.write_text('class="compare-pair"' * 10, encoding="utf-8")
    audit_path = tmp_path / "audit.md"

    passed = write_compare_audit(
        audit_path=audit_path,
        matched_pairs=pairs,
        compare_path=compare_path,
        report_date="20260512",
        comparison_note="DB-wide URL-proven same-article comparison set",
        provider=FakeAudit(),
    )

    assert passed is True
    assert "Verdict: PASS" in audit_path.read_text(encoding="utf-8")
