from __future__ import annotations

import html
import json
from typing import Any


def _escape(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _tag_list(tags: list[str] | tuple[str, ...]) -> str:
    return "".join(f'<span class="tag">{_escape(tag)}</span>' for tag in tags)


def _metric_table(metrics: dict[str, Any]) -> str:
    rows: list[str] = []
    for key, value in metrics.items():
        if isinstance(value, dict) and "pass" in value:
            status = "PASS" if value["pass"] else "FAIL"
            detail = value.get("detail", "")
        else:
            status = "INFO"
            detail = value
        rows.append(
            "<tr>"
            f"<th>{_escape(key)}</th>"
            f'<td><span class="status {status.lower()}">{status}</span></td>'
            f"<td>{_escape(detail)}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def _link_or_span(*, href: object, css_class: str, text: object) -> str:
    label = _escape(text)
    if not label:
        return ""
    url = _escape(href)
    if url:
        return f'<a class="{css_class}" href="{url}" target="_blank" rel="noopener noreferrer">{label}</a>'
    return f'<div class="{css_class}">{label}</div>'


def _aihot_card(aihot: dict[str, Any]) -> str:
    title = _link_or_span(href=aihot.get("url"), css_class="compare-title", text=aihot.get("title"))
    body_class = "compare-summary compare-linked-body" if not title and aihot.get("url") else "compare-summary"
    body = _link_or_span(href=aihot.get("url") if not title else "", css_class=body_class, text=aihot.get("summary"))
    return (
        '<article class="compare-card aihot-card">'
        '<div class="compare-card-head">'
        f'<span class="compare-source">{_escape(aihot.get("source"))}</span>'
        '<span class="compare-score-wrap"><span class="compare-selected">精选</span>'
        f'<span class="compare-score">{_escape(aihot.get("score"))}</span></span>'
        "</div>"
        f"{title}"
        f"{body}"
        f'<div class="tags">{_tag_list(aihot.get("tags", []))}</div>'
        f'<div class="reason"><span>推荐理由：</span>{_escape(aihot.get("why_recommend"))}</div>'
        "</article>"
    )


def _airadar_card(radar: dict[str, Any]) -> str:
    title = _link_or_span(
        href=radar.get("url"),
        css_class="compare-title",
        text=radar.get("title_zh") or radar.get("title"),
    )
    return (
        '<article class="compare-card airadar-card">'
        '<div class="compare-card-head">'
        f'<span class="compare-source">{_escape(radar.get("source_name"))}</span>'
        '<span class="compare-score-wrap"><span class="compare-selected">精选</span>'
        f'<span class="compare-score">{_escape(radar.get("display_score"))}</span></span>'
        "</div>"
        f"{title}"
        f'<p class="compare-summary">{_escape(radar.get("summary_zh"))}</p>'
        f'<div class="tags">{_tag_list(radar.get("tags", []))}</div>'
        f'<div class="reason"><span>推荐理由：</span>{_escape(radar.get("why_recommend"))}</div>'
        "</article>"
    )


def _pair_blocks(matched_pairs: list[dict[str, Any]]) -> str:
    rows: list[str] = []
    for pair in matched_pairs:
        aihot = pair["aihot"]
        radar = pair["airadar"]
        rows.append(
            '<section class="compare-pair">'
            '<div class="compare-proof">'
            f"同篇依据：{_escape(pair.get('match_method'))} · AI Radar #{_escape(radar.get('rank'))} · "
            f"match {_escape(pair.get('match_score'))}"
            "</div>"
            '<div class="compare-grid">'
            '<div><div class="compare-side-label">AI Hot 原卡片</div>'
            f"{_aihot_card(aihot)}"
            "</div>"
            '<div><div class="compare-side-label">AI Radar 原卡片</div>'
            f"{_airadar_card(radar)}"
            "</div>"
            "</div>"
            "</section>"
        )
    return "\n".join(rows)


def _unmatched_list(title: str, items: list[dict[str, Any]], fields: tuple[str, ...]) -> str:
    rows = []
    for item in items[:10]:
        bits = [str(item.get(field) or "") for field in fields if item.get(field)]
        rows.append(f"<li>{_escape(' · '.join(bits))}</li>")
    if not rows:
        rows.append("<li>无</li>")
    return f"<section><h2>{_escape(title)}</h2><ul>{''.join(rows)}</ul></section>"


def render_compare_html(
    *,
    matched_pairs: list[dict[str, Any]],
    unmatched_airadar: list[dict[str, Any]],
    unmatched_aihot: list[dict[str, Any]],
    metrics: dict[str, Any],
    iteration_counter: dict[str, int],
    known_limit_list: list[str],
    report_date: str,
    comparison_note: str = "",
) -> str:
    known_limit = bool(known_limit_list)
    known_limit_attr = "true" if known_limit else "false"
    known_limit_options = ""
    if known_limit:
        known_limit_options = """
          <fieldset class="known-limit-options">
            <legend>决策轴 2：known-limit</legend>
            <label><input type="radio" name="known_limit_decision" value="accept" required> 接受 known-limit 现状</label>
            <label><input type="radio" name="known_limit_decision" value="fix_required" required> 要求修复 known-limit</label>
          </fieldset>
        """
    else:
        known_limit_options = '<input type="hidden" name="known_limit_decision" value="none">'
    known_limit_html = "".join(f"<li>{_escape(item)}</li>" for item in known_limit_list) or "<li>无</li>"
    comparison_note_html = f'<p class="comparison-note">{_escape(comparison_note)}</p>' if comparison_note else ""
    iteration_round = f"{iteration_counter.get('step3_6', 0)}/3 + {iteration_counter.get('step4_6', 0)}/3"
    ballot_schema = {
        "continue_iterating": False,
        "known_limit_decision": "none",
        "scores": {"sources_overlap": 0, "ranking": 0, "editorial_voice": 0, "quality_gap": 0},
        "notes": {"sources_overlap": "", "ranking": "", "editorial_voice": "", "quality_gap": ""},
        "fail_routing": None,
        "iteration_round": iteration_round,
        "submitted_at": "",
    }
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AI Radar × AI Hot V6 Compare {html.escape(report_date)}</title>
  <style>
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #1f2933; background: #f6f7f9; }}
    header {{ padding: 28px 36px; background: #111827; color: white; }}
    main {{ padding: 24px 36px 48px; max-width: 1440px; margin: 0 auto; }}
    h1 {{ margin: 0 0 10px; font-size: 28px; }}
    h2 {{ margin-top: 28px; }}
    table {{ width: 100%; border-collapse: collapse; background: white; }}
    th, td {{ border: 1px solid #d9dee7; padding: 12px; vertical-align: top; }}
    th {{ text-align: left; background: #eef2f7; width: 180px; }}
    .status {{ display: inline-block; min-width: 54px; padding: 3px 8px; border-radius: 4px; font-weight: 700; font-size: 12px; }}
    .pass {{ background: #dcfce7; color: #166534; }}
    .fail {{ background: #fee2e2; color: #991b1b; }}
    .info {{ background: #e0f2fe; color: #075985; }}
    .compare-pair {{ margin: 18px 0 24px; }}
    .compare-proof {{ margin: 0 0 8px; color: #697386; font-size: 13px; }}
    .compare-grid {{ display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 16px; align-items: start; }}
    .compare-side-label {{ margin-bottom: 6px; color: #4b5563; font-weight: 700; font-size: 13px; }}
    .compare-card {{ background: white; border: 1px solid #d9dee7; border-radius: 8px; padding: 16px; min-height: 100%; }}
    .compare-card-head {{ display: flex; justify-content: space-between; gap: 12px; align-items: center; margin-bottom: 12px; }}
    .compare-source {{ color: #4b5563; font-weight: 700; font-size: 13px; }}
    .compare-score-wrap {{ display: inline-flex; align-items: center; gap: 6px; }}
    .compare-selected {{ background: #111827; color: white; border-radius: 999px; padding: 2px 7px; font-size: 12px; }}
    .compare-score {{ color: #111827; font-weight: 800; font-variant-numeric: tabular-nums; }}
    .compare-title {{ display: block; color: #111827; font-size: 18px; line-height: 1.35; font-weight: 800; text-decoration: none; margin: 0 0 10px; }}
    .compare-summary {{ display: block; color: #1f2933; line-height: 1.65; margin: 0 0 12px; text-decoration: none; white-space: pre-line; }}
    .compare-linked-body:hover, .compare-title:hover {{ text-decoration: underline; }}
    .reason {{ color: #374151; font-weight: 600; line-height: 1.6; border-top: 1px solid #e5e7eb; margin-top: 12px; padding-top: 12px; }}
    .reason span {{ color: #111827; }}
    .tags {{ display: flex; gap: 6px; flex-wrap: wrap; margin: 10px 0; }}
    .tag {{ background: #edf2f7; border: 1px solid #d8dee9; border-radius: 4px; padding: 2px 7px; font-size: 12px; }}
    section {{ margin-top: 24px; }}
    form {{ background: white; border: 1px solid #d9dee7; padding: 18px; }}
    fieldset {{ border: 1px solid #d9dee7; margin: 14px 0; padding: 12px; }}
    label {{ display: inline-flex; gap: 6px; align-items: center; margin: 6px 12px 6px 0; }}
    textarea {{ display: block; width: min(720px, 100%); min-height: 54px; margin-top: 8px; }}
    button {{ padding: 9px 14px; border: 1px solid #111827; background: #111827; color: white; border-radius: 4px; cursor: pointer; }}
    .iteration-badge {{ margin-top: 8px; color: #cbd5e1; }}
    .comparison-note {{ background: #fff7ed; border: 1px solid #fed7aa; padding: 10px 12px; color: #7c2d12; }}
    @media (max-width: 860px) {{ .compare-grid {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body data-known-limit="{known_limit_attr}" data-ballot-schema='{html.escape(json.dumps(ballot_schema, ensure_ascii=False), quote=True)}'>
  <header>
    <h1>AI Radar × AI Hot V6 Compare</h1>
    <div>{_escape(report_date)} · matched {len(matched_pairs)} · AI Radar only {len(unmatched_airadar)} · AI Hot only {len(unmatched_aihot)}</div>
    <div class="iteration-badge">当前迭代：{_escape(iteration_round)} 轮（Step 3↔6 + Step 4↔6）</div>
  </header>
  <main>
    <section>
      <h2>V1-V5 量化结论</h2>
      <table>{_metric_table(metrics)}</table>
    </section>
    <section>
      <h2>Known Limitations</h2>
      <ul>{known_limit_html}</ul>
    </section>
    <section>
      <h2>Matched Articles</h2>
      {comparison_note_html}
      {_pair_blocks(matched_pairs[:30]) or "<p>无可证明为同一篇的 matched article。请看 unmatched 列表。</p>"}
    </section>
    {_unmatched_list("AI Radar 精选 / AI Hot 未匹配", unmatched_airadar, ("rank", "title_zh", "source_name"))}
    {_unmatched_list("AI Hot 精选 / AI Radar 未匹配", unmatched_aihot, ("score", "title", "source"))}
    <section>
      <h2>V6 Ballot</h2>
      <form id="ballot">
        <fieldset>
          <legend>信源重叠</legend>
          <label><input type="radio" name="score_sources_overlap" value="0" required>0</label>
          <label><input type="radio" name="score_sources_overlap" value="1">1</label>
          <label><input type="radio" name="score_sources_overlap" value="2">2</label>
          <label><input type="radio" name="score_sources_overlap" value="3">3</label>
          <textarea name="note_sources_overlap" placeholder="可选：具体例子"></textarea>
        </fieldset>
        <fieldset>
          <legend>排序合理</legend>
          <label><input type="radio" name="score_ranking" value="0" required>0</label>
          <label><input type="radio" name="score_ranking" value="1">1</label>
          <label><input type="radio" name="score_ranking" value="2">2</label>
          <label><input type="radio" name="score_ranking" value="3">3</label>
          <textarea name="note_ranking" placeholder="可选：具体例子"></textarea>
        </fieldset>
        <fieldset>
          <legend>摘要/推荐理由编辑风格</legend>
          <label><input type="radio" name="score_editorial_voice" value="0" required>0</label>
          <label><input type="radio" name="score_editorial_voice" value="1">1</label>
          <label><input type="radio" name="score_editorial_voice" value="2">2</label>
          <label><input type="radio" name="score_editorial_voice" value="3">3</label>
          <textarea name="note_editorial_voice" placeholder="可选：具体例子"></textarea>
        </fieldset>
        <fieldset>
          <legend>内容质量落差</legend>
          <label><input type="radio" name="score_quality_gap" value="0" required>0</label>
          <label><input type="radio" name="score_quality_gap" value="1">1</label>
          <label><input type="radio" name="score_quality_gap" value="2">2</label>
          <label><input type="radio" name="score_quality_gap" value="3">3</label>
          <textarea name="note_quality_gap" placeholder="可选：具体例子"></textarea>
        </fieldset>
        <fieldset>
          <legend>FAIL routing</legend>
          <label><input type="radio" name="fail_routing" value="">无</label>
          <label><input type="radio" name="fail_routing" value="V1">V1</label>
          <label><input type="radio" name="fail_routing" value="V2">V2</label>
          <label><input type="radio" name="fail_routing" value="V3">V3</label>
          <label><input type="radio" name="fail_routing" value="V4">V4</label>
          <label><input type="radio" name="fail_routing" value="V5">V5</label>
          <label><input type="radio" name="fail_routing" value="holistic">holistic</label>
        </fieldset>
        <fieldset>
          <legend>决策轴 1</legend>
          <label><input type="radio" name="continue_iterating" value="true" required> 继续迭代</label>
          <label><input type="radio" name="continue_iterating" value="false" required> 停止 - 进 Step 8</label>
        </fieldset>
        {known_limit_options}
        <button type="submit">Submit Ballot JSON</button>
      </form>
    </section>
  </main>
  <script>
    document.getElementById('ballot').addEventListener('submit', async (event) => {{
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      const pick = (name) => form.get(name);
      const payload = {{
        continue_iterating: pick('continue_iterating') === 'true',
        known_limit_decision: pick('known_limit_decision') || 'none',
        scores: {{
          sources_overlap: Number(pick('score_sources_overlap')),
          ranking: Number(pick('score_ranking')),
          editorial_voice: Number(pick('score_editorial_voice')),
          quality_gap: Number(pick('score_quality_gap'))
        }},
        notes: {{
          sources_overlap: String(pick('note_sources_overlap') || ''),
          ranking: String(pick('note_ranking') || ''),
          editorial_voice: String(pick('note_editorial_voice') || ''),
          quality_gap: String(pick('note_quality_gap') || '')
        }},
        fail_routing: pick('fail_routing') || null,
        iteration_round: '{_escape(iteration_round)}',
        submitted_at: new Date().toISOString()
      }};
      const text = JSON.stringify(payload, null, 2);
      try {{ navigator.clipboard?.writeText(text).catch(error => console.warn(error)); }} catch (error) {{ console.warn(error); }}
      alert(text);
    }});
  </script>
</body>
</html>
"""
