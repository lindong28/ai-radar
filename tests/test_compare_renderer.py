from __future__ import annotations

from typing import Any

from airadar.eval.compare_renderer import render_compare_html


def _pair(rank: int) -> dict[str, Any]:
    return {
        "aihot": {
            "score": 80 + rank,
            "title": f"AI Hot {rank}",
            "summary": "AI Hot 摘要",
            "why_recommend": "AI Hot 推荐理由",
            "tags": ["模型发布"],
            "source": "OpenAI",
        },
        "airadar": {
            "rank": rank,
            "title_zh": f"AI Radar {rank}",
            "summary_zh": "AI Radar 摘要",
            "why_recommend": "AI Radar 推荐理由",
            "tags": ["模型发布", "教程/实践"],
            "source_name": "OpenAI Blog",
            "display_score": 90,
        },
        "match_method": "url",
        "match_score": 1.0,
    }


def test_compare_renderer_outputs_pairs_and_ballot_schema() -> None:
    html = render_compare_html(
        matched_pairs=[_pair(1), _pair(2)],
        unmatched_airadar=[],
        unmatched_aihot=[],
        metrics={"V1": {"pass": True, "detail": "ok"}},
        iteration_counter={"step3_6": 1, "step4_6": 2},
        known_limit_list=[],
        report_date="20260512",
    )

    assert html.count('class="compare-pair"') == 2
    assert 'data-known-limit="false"' in html
    assert 'name="continue_iterating"' in html
    assert 'name="known_limit_decision" value="none"' in html
    assert "continue_iterating" in html
    assert "submitted_at" in html


def test_compare_renderer_does_not_fabricate_aihot_title_from_source() -> None:
    pair = _pair(1)
    pair["aihot"]["title"] = ""
    pair["aihot"]["summary"] = "这是 AI Hot 原卡片正文，不应被 source 替换成标题。"
    pair["aihot"]["source"] = "OpenAI@OpenAI"

    html = render_compare_html(
        matched_pairs=[pair],
        unmatched_airadar=[],
        unmatched_aihot=[],
        metrics={"V1": {"pass": True, "detail": "ok"}},
        iteration_counter={"step3_6": 0, "step4_6": 0},
        known_limit_list=[],
        report_date="20260512",
    )

    assert "<h3>OpenAI@OpenAI</h3>" not in html
    assert "这是 AI Hot 原卡片正文" in html
    assert "compare-card aihot-card" in html
    assert "compare-card airadar-card" in html


def test_compare_renderer_enables_known_limit_axis() -> None:
    html = render_compare_html(
        matched_pairs=[_pair(1)],
        unmatched_airadar=[],
        unmatched_aihot=[],
        metrics={"V1": {"pass": False, "detail": "known missing"}},
        iteration_counter={"step3_6": 0, "step4_6": 0},
        known_limit_list=["ISSUE-001 Claude Blog RSS unavailable"],
        report_date="20260512",
    )

    assert 'data-known-limit="true"' in html
    assert 'name="known_limit_decision" value="accept"' in html
    assert 'name="known_limit_decision" value="fix_required"' in html
    assert "ISSUE-001 Claude Blog RSS unavailable" in html
