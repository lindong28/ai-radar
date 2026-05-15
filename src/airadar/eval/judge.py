from __future__ import annotations

import json
import math
import os
import random
import re
import sqlite3
import statistics
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
import json_repair

from ..curator.score import weighted_score as compute_weighted_score
from ..curator.weights import DEFAULT_WEIGHTS
from ..db import PROJECT_ROOT
from ..enrich.schema import EnrichOutput
from ..provider.deepseek_chat import chat_json
from ..topics import CONTROLLED_VOCABULARY, deterministic_tags, is_in_vocabulary, topic_tags
from .compare_renderer import render_compare_html
from .distribution import display_score, score_distribution

PLAN_DIR = PROJECT_ROOT / "plans" / "ai-radar-alignment-20260512"
DEFAULT_AIHOT_MARKDOWN = PLAN_DIR / "baseline" / "aihot-before.md"
DEFAULT_OUTPUT_DIR = PLAN_DIR
ITERATION_COUNTER = PLAN_DIR / "iteration-counter.json"
AIHOT_SOURCES = PLAN_DIR / "aihot-sources.json"
STATE_FILE = PLAN_DIR / "state.md"

DATE_RE = re.compile(r"^\d{1,2}月\d{1,2}日$")
TIME_RE = re.compile(r"^\d{2}:\d{2}$")
SCORE_RE = re.compile(r"^精选(?P<score>\d+)$")
RECOMMEND_RE = re.compile(r"^推荐理由：(?P<why>.+)$")
URL_RE = re.compile(r"https?[：:]//[^\s)）]+")
TEMPLATE_RE = re.compile(r"属于.*来自.*包含.*信号")
SUMMARY_DIMS = ("information", "insight", "fluency", "brevity")
RECOMMENDATION_DIMS = ("uniqueness", "insight", "audience")


@dataclass(frozen=True)
class AihotItem:
    index: int
    date_label: str | None
    time: str
    source: str
    score: int
    title: str
    summary: str
    why_recommend: str
    tags: list[str]
    url: str | None
    raw_text: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "date_label": self.date_label,
            "time": self.time,
            "source": self.source,
            "score": self.score,
            "title": self.title,
            "summary": self.summary,
            "why_recommend": self.why_recommend,
            "tags": self.tags,
            "url": self.url,
            "raw_text": self.raw_text,
        }


@dataclass(frozen=True)
class RadarItem:
    id: str
    run_id: str
    rank: int
    weighted_score: float
    url: str
    title: str
    title_zh: str
    summary_zh: str
    why_recommend: str
    tags: list[str]
    source_id: str
    source_name: str
    content_text: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "rank": self.rank,
            "weighted_score": self.weighted_score,
            "display_score": display_score(self.weighted_score),
            "url": self.url,
            "title": self.title,
            "title_zh": self.title_zh,
            "summary_zh": self.summary_zh,
            "why_recommend": self.why_recommend,
            "tags": self.tags,
            "source_id": self.source_id,
            "source_name": self.source_name,
            "content_text": self.content_text,
        }


@dataclass(frozen=True)
class MatchedPair:
    aihot: AihotItem
    airadar: RadarItem
    match_method: str
    match_score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "aihot": self.aihot.to_dict(),
            "airadar": self.airadar.to_dict(),
            "match_method": self.match_method,
            "match_score": round(self.match_score, 3),
        }


@dataclass(frozen=True)
class JudgeScores:
    summary_aihot: dict[str, float]
    summary_airadar: dict[str, float]
    recommendation_aihot: dict[str, float]
    recommendation_airadar: dict[str, float]
    suggestions: list[str]
    raw: dict[str, Any]

    def summary_average(self, side: str) -> float:
        values = self.summary_aihot if side == "aihot" else self.summary_airadar
        return _average(values.values())

    def recommendation_average(self, side: str) -> float:
        values = self.recommendation_aihot if side == "aihot" else self.recommendation_airadar
        return _average(values.values())


@dataclass(frozen=True)
class EvaluationArtifacts:
    report_path: Path
    compare_path: Path
    report_date: str
    metrics: dict[str, Any]
    matched_count: int
    sample_count: int
    audit_path: Path | None = None


class JudgeProvider(Protocol):
    model_id: str

    def judge_pair(self, pair: MatchedPair) -> JudgeScores: ...


class CompareAuditProvider(Protocol):
    model_id: str

    def audit_compare(self, payload: dict[str, Any]) -> dict[str, Any]: ...


def _deepseek_base_url() -> str:
    configured = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
    if configured.endswith("/chat/completions"):
        configured = configured[: -len("/chat/completions")]
    if configured == "https://api.deepseek.com":
        configured = f"{configured}/v1"
    return configured


class DeepSeekV4ProJudge:
    model_id = "deepseek-v4-pro"

    def judge_pair(self, pair: MatchedPair) -> JudgeScores:
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is required for eval judge")
        system, user = render_judge_prompt(pair)
        last_error: Exception | None = None
        for _ in range(2):
            try:
                response = httpx.post(
                    f"{_deepseek_base_url()}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": os.environ.get("AI_RADAR_DEEPSEEK_JUDGE_MODEL", self.model_id),
                        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                        "response_format": {"type": "json_object"},
                        "temperature": 0,
                        "max_tokens": int(os.environ.get("AI_RADAR_EVAL_MAX_TOKENS", "1200")),
                    },
                    timeout=float(os.environ.get("AI_RADAR_DEEPSEEK_TIMEOUT", "60")),
                )
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                if content is None:
                    raise ValueError("DeepSeek judge response did not include message content")
                return parse_judge_response(_loads_json_object(content))
            except (json.JSONDecodeError, ValueError) as exc:
                last_error = exc
        raise ValueError(f"DeepSeek judge returned invalid JSON after retry: {last_error}")


class DeepSeekV4ProCompareAudit:
    model_id = "deepseek-v4-pro"

    def audit_compare(self, payload: dict[str, Any]) -> dict[str, Any]:
        system = (
            "你是严格的 HTML 对比评审。只判断这个 AI Radar × AI Hot V6 对比页是否适合交给用户审核："
            "必须有足够同篇文章样本、同篇证据必须是 URL/status-id、左右卡片字段应保留原站视觉语义、"
            "AI Radar 推荐语应短而接近 AI Hot、source/brand 标签不能明显缺失。"
            '输出 JSON：{"verdict":"PASS|FAIL","reasons":[...],"required_fixes":[...]}。'
        )
        result = chat_json(
            system=system,
            user=json.dumps(payload, ensure_ascii=False),
            default_model=self.model_id,
            model_env="AI_RADAR_DEEPSEEK_AUDIT_MODEL",
            ark_model_env="AI_RADAR_ARK_AUDIT_MODEL",
            temperature=0.0,
            max_tokens=1200,
        )
        return {"provider": result.provider, "model": result.model, **result.json}


def _loads_json_object(content: str) -> dict[str, Any]:
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        data = json_repair.loads(content)
    if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
        data = data[0]
    if not isinstance(data, dict):
        raise ValueError("judge response JSON must be an object")
    return data


def render_judge_prompt(pair: MatchedPair) -> tuple[str, str]:
    system = (
        "你是严格的中文 AI 内容编辑评审。比较同一篇文章在 AI Hot 和 AI Radar 中的摘要与推荐理由，"
        "只按给定维度打 0-10 分。不要因来源品牌或站点偏好加分。输出必须是 JSON 对象，"
        "必须以 { 开头、以 } 结尾，不要 Markdown，不要解释。"
    )
    payload = {
        "schema": {
            "v2_summary": {
                "aihot": dict.fromkeys(SUMMARY_DIMS, 0),
                "airadar": dict.fromkeys(SUMMARY_DIMS, 0),
            },
            "v3_recommendation": {
                "aihot": dict.fromkeys(RECOMMENDATION_DIMS, 0),
                "airadar": dict.fromkeys(RECOMMENDATION_DIMS, 0),
            },
            "suggestions": ["针对 AI Radar 的具体改进建议"],
        },
        "dimensions": {
            "summary": {
                "information": "是否覆盖核心事实与关键信息",
                "insight": "是否解释 why / so what，而不只是转述",
                "fluency": "中文是否自然、准确、无机翻感",
                "brevity": "是否简洁，信息密度高",
            },
            "recommendation": {
                "uniqueness": "是否有独特判断，避免模板化",
                "insight": "是否指出为什么值得读",
                "audience": "是否明确适合谁读或读者收益",
            },
        },
        "aihot": {
            "title": pair.aihot.title,
            "summary": pair.aihot.summary,
            "why_recommend": pair.aihot.why_recommend,
            "tags": pair.aihot.tags,
        },
        "airadar": {
            "title": pair.airadar.title_zh or pair.airadar.title,
            "summary": pair.airadar.summary_zh,
            "why_recommend": pair.airadar.why_recommend,
            "tags": pair.airadar.tags,
        },
    }
    return system, json.dumps(payload, ensure_ascii=False)


def _average(values: Any) -> float:
    values_list = [float(value) for value in values]
    return sum(values_list) / len(values_list) if values_list else 0.0


def _coerce_score(value: object) -> float:
    if not isinstance(value, (str, int, float)):
        return 0.0
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(score):
        return 0.0
    return max(0.0, min(10.0, score))


def _dimension_scores(data: dict[str, Any], dims: tuple[str, ...]) -> dict[str, float]:
    return {dimension: _coerce_score(data.get(dimension)) for dimension in dims}


def parse_judge_response(data: dict[str, Any]) -> JudgeScores:
    summary = data.get("v2_summary") or data.get("summary") or {}
    recommendation = data.get("v3_recommendation") or data.get("recommendation") or {}
    suggestions = data.get("suggestions") or []
    return JudgeScores(
        summary_aihot=_dimension_scores(summary.get("aihot", {}), SUMMARY_DIMS),
        summary_airadar=_dimension_scores(summary.get("airadar", {}), SUMMARY_DIMS),
        recommendation_aihot=_dimension_scores(recommendation.get("aihot", {}), RECOMMENDATION_DIMS),
        recommendation_airadar=_dimension_scores(recommendation.get("airadar", {}), RECOMMENDATION_DIMS),
        suggestions=[str(item) for item in suggestions],
        raw=data,
    )


def _split_tags(tag_line: str) -> list[str]:
    found: list[tuple[int, int, str]] = []
    for tag in sorted(CONTROLLED_VOCABULARY, key=len, reverse=True):
        start = 0
        while True:
            index = tag_line.find(tag, start)
            if index < 0:
                break
            found.append((index, -len(tag), tag))
            start = index + len(tag)
    tags: list[str] = []
    for _, _, tag in sorted(found):
        if tag not in tags:
            tags.append(tag)
    return tags


def _extract_urls(text: str) -> list[str]:
    urls: list[str] = []
    for match in URL_RE.findall(text.replace("：//", "://")):
        cleaned = match.rstrip(".,，。；;）)")
        normalized = normalize_url(cleaned)
        if normalized and normalized not in urls:
            urls.append(normalized)
    return urls


def parse_aihot_markdown(markdown: str) -> list[AihotItem]:
    lines = [line.strip() for line in markdown.splitlines() if line.strip()]
    items: list[AihotItem] = []
    date_label: str | None = None
    index = 0
    while index < len(lines):
        line = lines[index]
        if DATE_RE.fullmatch(line):
            date_label = line
            index += 1
            continue
        if not TIME_RE.fullmatch(line) or index + 2 >= len(lines) or not SCORE_RE.fullmatch(lines[index + 2]):
            index += 1
            continue
        time_value = line
        source = lines[index + 1]
        score = int(SCORE_RE.fullmatch(lines[index + 2]).group("score"))  # type: ignore[union-attr]
        cursor = index + 3
        before_separator: list[str] = []
        while cursor < len(lines) and lines[cursor] != "---":
            before_separator.append(lines[cursor])
            cursor += 1
        recommendation = ""
        if cursor + 1 < len(lines):
            match = RECOMMEND_RE.fullmatch(lines[cursor + 1])
            if match:
                recommendation = match.group("why").strip()
        tag_cursor = len(before_separator) - 1
        while tag_cursor >= 0 and before_separator[tag_cursor].startswith("关联讨论"):
            tag_cursor -= 1
        tag_line = before_separator[tag_cursor] if tag_cursor >= 0 else ""
        tags = _split_tags(tag_line)
        content_lines = before_separator[: max(tag_cursor, 0)]
        if content_lines and content_lines[-1].startswith("关联讨论"):
            content_lines = [line for line in content_lines if not line.startswith("关联讨论")]
        title = ""
        summary_lines = content_lines
        if len(content_lines) > 1 and len(content_lines[0]) <= 90:
            title = content_lines[0]
            summary_lines = content_lines[1:]
        summary = " ".join(summary_lines).strip()
        raw_text = " ".join([source, *before_separator, recommendation]).strip()
        urls = _extract_urls(raw_text)
        items.append(
            AihotItem(
                index=len(items) + 1,
                date_label=date_label,
                time=time_value,
                source=source,
                score=score,
                title=title,
                summary=summary,
                why_recommend=recommendation,
                tags=tags,
                url=urls[0] if urls else None,
                raw_text=raw_text,
            )
        )
        index = cursor + 2
    return items


def normalize_url(url: str | None) -> str | None:
    if not url:
        return None
    value = url.strip().replace("：//", "://")
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    if not parts.scheme or not parts.netloc:
        return None
    netloc = parts.netloc.lower()
    if netloc in {"x.com", "twitter.com", "www.twitter.com", "nitter.net"}:
        status_match = re.match(r"^/([^/]+)/status/(\d+)", parts.path)
        if status_match:
            handle, status_id = status_match.groups()
            return urlunsplit(("https", "x.com", f"/{handle.lower()}/status/{status_id}", "", ""))
    query = [(key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True) if not key.startswith("utm_")]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), netloc, path, urlencode(query), ""))


def _aihot_item_from_mapping(data: dict[str, Any], index: int) -> AihotItem:
    tags = data.get("tags") or []
    if not isinstance(tags, list):
        tags = []
    source = str(data.get("source") or "")
    title = str(data.get("title") or "")
    summary = str(data.get("summary") or "")
    why_recommend = str(data.get("why_recommend") or data.get("reason") or "")
    raw_text = str(data.get("raw_text") or " ".join([source, title, summary, why_recommend]).strip())
    return AihotItem(
        index=int(data.get("index") or index),
        date_label=str(data["date_label"]) if data.get("date_label") else None,
        time=str(data.get("time") or ""),
        source=source,
        score=int(data.get("score") or 0),
        title=title,
        summary=summary,
        why_recommend=why_recommend,
        tags=[str(tag) for tag in tags],
        url=normalize_url(str(data["url"])) if data.get("url") else None,
        raw_text=raw_text,
    )


def load_aihot_items_from_path(path: Path) -> list[AihotItem]:
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        raw_items = payload.get("items", payload) if isinstance(payload, dict) else payload
        if not isinstance(raw_items, list):
            raise ValueError("AI Hot JSON snapshot must be a list or an object with an items list")
        return [
            _aihot_item_from_mapping(item, index)
            for index, item in enumerate(raw_items, start=1)
            if isinstance(item, dict)
        ]
    return parse_aihot_markdown(path.read_text(encoding="utf-8"))


def _parse_enrichment(output_json: str | None) -> EnrichOutput | None:
    if not output_json:
        return None
    try:
        return EnrichOutput.model_validate(json.loads(output_json))
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def _score_from_numeric(row: sqlite3.Row) -> float:
    numeric_json = row["numeric_json"] if "numeric_json" in row.keys() else None
    if not numeric_json:
        return 0.0
    try:
        numeric = json.loads(numeric_json)
    except json.JSONDecodeError:
        return 0.0
    return compute_weighted_score(numeric, DEFAULT_WEIGHTS, row["tier"])


def _radar_item_from_row(
    row: sqlite3.Row,
    *,
    run_id: str,
    rank: int,
    weighted_score: float | None,
) -> RadarItem:
    enrichment = _parse_enrichment(row["output_json"])
    tags = (
        topic_tags(
            enrichment.tags,
            source_id=row["source_id"],
            source_name=row["source_name"],
            url=row["url"],
            title=row["title"],
            content_text=row["content_text"] or "",
        )
        if enrichment
        else []
    )
    return RadarItem(
        id=row["id"],
        run_id=run_id,
        rank=rank,
        weighted_score=float(weighted_score if weighted_score is not None else _score_from_numeric(row)),
        url=row["url"],
        title=row["title"],
        title_zh=enrichment.title_zh if enrichment else row["title"],
        summary_zh=enrichment.summary_zh if enrichment else "",
        why_recommend=enrichment.why_recommend if enrichment else "",
        tags=tags,
        source_id=row["source_id"],
        source_name=row["source_name"],
        content_text=row["content_text"] or "",
    )


def _select_run(conn: sqlite3.Connection, selected_date: str | None) -> sqlite3.Row | None:
    if selected_date:
        return conn.execute(
            "SELECT * FROM curation_runs WHERE substr(created_at, 1, 10)=? ORDER BY created_at DESC LIMIT 1",
            (selected_date,),
        ).fetchone()
    return conn.execute("SELECT * FROM curation_runs ORDER BY created_at DESC LIMIT 1").fetchone()


def load_airadar_items(
    conn: sqlite3.Connection, selected_date: str | None = None
) -> tuple[str | None, str | None, list[RadarItem]]:
    run = _select_run(conn, selected_date)
    if run is None:
        return None, selected_date, []
    rows = conn.execute(
        """
        SELECT i.id, i.url, i.title, i.content_text, s.id AS source_id, s.name AS source_name,
               s.tier, c.run_id, c.rank, c.weighted_score, ie.output_json, se.numeric_json
        FROM curated_items c
        JOIN items i ON i.id = c.item_id
        JOIN sources s ON s.id = i.source_id
        LEFT JOIN item_evaluations ie ON ie.id = (
          SELECT MAX(latest.id)
          FROM item_evaluations latest
          WHERE latest.item_id = i.id
            AND latest.stage = 'enrich'
            AND latest.error IS NULL
        )
        LEFT JOIN item_evaluations se ON se.id = (
          SELECT MAX(latest_score.id)
          FROM item_evaluations latest_score
          WHERE latest_score.item_id = i.id
            AND latest_score.stage = 'scoring'
            AND latest_score.error IS NULL
        )
        WHERE c.run_id = ?
        ORDER BY c.rank
        """,
        (run["id"],),
    ).fetchall()
    items = [
        _radar_item_from_row(
            row,
            run_id=row["run_id"],
            rank=int(row["rank"]),
            weighted_score=float(row["weighted_score"]),
        )
        for row in rows
    ]
    return run["id"], str(run["created_at"])[:10], items


def load_airadar_items_by_aihot_urls(conn: sqlite3.Connection, aihot_items: list[AihotItem]) -> list[RadarItem]:
    rows = conn.execute(
        """
        SELECT i.id, i.url, i.title, i.content_text, i.published_at, i.fetched_at,
               s.id AS source_id, s.name AS source_name, s.tier,
               c.run_id, c.rank AS curated_rank, c.weighted_score,
               ie.output_json, se.numeric_json
        FROM items i
        JOIN sources s ON s.id = i.source_id
        LEFT JOIN curated_items c ON c.item_id = i.id
        LEFT JOIN item_evaluations ie ON ie.id = (
          SELECT MAX(latest.id)
          FROM item_evaluations latest
          WHERE latest.item_id = i.id
            AND latest.stage = 'enrich'
            AND latest.error IS NULL
        )
        LEFT JOIN item_evaluations se ON se.id = (
          SELECT MAX(latest_score.id)
          FROM item_evaluations latest_score
          WHERE latest_score.item_id = i.id
            AND latest_score.stage = 'scoring'
            AND latest_score.error IS NULL
        )
        ORDER BY i.fetched_at DESC, i.published_at DESC, i.id DESC
        """
    ).fetchall()
    rows_by_url: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        normalized = normalize_url(row["url"])
        if normalized:
            rows_by_url.setdefault(normalized, []).append(row)

    items: list[RadarItem] = []
    used: set[str] = set()
    for aihot in aihot_items:
        normalized = normalize_url(aihot.url)
        if not normalized:
            continue
        candidates = [
            row
            for row in rows_by_url.get(normalized, [])
            if row["id"] not in used and _parse_enrichment(row["output_json"]) is not None
        ]
        if not candidates:
            continue
        candidates.sort(
            key=lambda row: (
                1 if row["output_json"] else 0,
                1 if row["curated_rank"] is not None else 0,
                str(row["fetched_at"] or ""),
            ),
            reverse=True,
        )
        selected = candidates[0]
        used.add(selected["id"])
        items.append(
            _radar_item_from_row(
                selected,
                run_id=selected["run_id"] or "aihot-url-overlap",
                rank=aihot.index,
                weighted_score=selected["weighted_score"],
            )
        )
    return items


def _norm_text(value: str) -> str:
    return re.sub(r"\s+", "", value.lower())


def _text_terms(value: str) -> set[str]:
    text = value.lower()
    ascii_terms = {term for term in re.findall(r"[a-z][a-z0-9_+-]{2,}", text)}
    cjk = re.findall(r"[\u4e00-\u9fff]", text)
    cjk_terms = {"".join(cjk[index : index + 2]) for index in range(max(0, len(cjk) - 1))}
    return ascii_terms | cjk_terms


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _similarity(left: str, right: str) -> float:
    left_terms = _text_terms(left)
    right_terms = _text_terms(right)
    return _jaccard(left_terms, right_terms)


def _source_similarity(aihot_source: str, radar: RadarItem) -> float:
    source = _norm_text(aihot_source)
    source_name = _norm_text(radar.source_name)
    source_id = _norm_text(radar.source_id)
    if source_name and source_name in source:
        return 1.0
    if source_id and source_id in source:
        return 1.0
    return max(_similarity(source, source_name), _similarity(source, source_id))


def match_items(
    aihot_items: list[AihotItem], radar_items: list[RadarItem]
) -> tuple[list[MatchedPair], list[RadarItem], list[AihotItem]]:
    radar_by_url = {normalize_url(item.url): item for item in radar_items if normalize_url(item.url)}
    used_radar: set[str] = set()
    used_aihot: set[int] = set()
    pairs: list[MatchedPair] = []
    for aihot in aihot_items:
        normalized = normalize_url(aihot.url)
        radar = radar_by_url.get(normalized)
        if radar and radar.id not in used_radar:
            pairs.append(MatchedPair(aihot, radar, "url", 1.0))
            used_radar.add(radar.id)
            used_aihot.add(aihot.index)
    unmatched_radar = [item for item in radar_items if item.id not in used_radar]
    unmatched_aihot = [item for item in aihot_items if item.index not in used_aihot]
    pairs.sort(key=lambda pair: pair.airadar.rank)
    return pairs, unmatched_radar, unmatched_aihot


def _median_dict(results: list[dict[str, float]], dims: tuple[str, ...]) -> dict[str, float]:
    return {dimension: statistics.median([result[dimension] for result in results]) for dimension in dims}


def _median_judge(results: list[JudgeScores]) -> JudgeScores:
    return JudgeScores(
        summary_aihot=_median_dict([result.summary_aihot for result in results], SUMMARY_DIMS),
        summary_airadar=_median_dict([result.summary_airadar for result in results], SUMMARY_DIMS),
        recommendation_aihot=_median_dict([result.recommendation_aihot for result in results], RECOMMENDATION_DIMS),
        recommendation_airadar=_median_dict([result.recommendation_airadar for result in results], RECOMMENDATION_DIMS),
        suggestions=[suggestion for result in results for suggestion in result.suggestions],
        raw={"runs": [result.raw for result in results]},
    )


def _is_borderline(result: JudgeScores) -> bool:
    values = [
        result.summary_average("aihot") / 10,
        result.summary_average("airadar") / 10,
        result.recommendation_average("aihot") / 10,
        result.recommendation_average("airadar") / 10,
    ]
    return any(0.77 <= value <= 0.93 for value in values)


def evaluate_pairs(
    pairs: list[MatchedPair], provider: JudgeProvider, *, sample_size: int = 10, seed: int = 42
) -> list[tuple[MatchedPair, JudgeScores]]:
    if len(pairs) > sample_size:
        sample = random.Random(seed).sample(pairs, sample_size)
        sample.sort(key=lambda pair: pair.airadar.rank)
    else:
        sample = list(pairs)
    evaluated: list[tuple[MatchedPair, JudgeScores]] = []
    repeat_borderline = os.environ.get("AI_RADAR_EVAL_REPEAT_BORDERLINE", "1") != "0"
    for pair in sample:
        first = provider.judge_pair(pair)
        if repeat_borderline and _is_borderline(first):
            results = [first, provider.judge_pair(pair), provider.judge_pair(pair)]
            evaluated.append((pair, _median_judge(results)))
        else:
            evaluated.append((pair, first))
    return evaluated


def _aggregate_scores(evaluated: list[tuple[MatchedPair, JudgeScores]]) -> dict[str, Any]:
    summary_aihot = _average([scores.summary_average("aihot") for _, scores in evaluated])
    summary_airadar = _average([scores.summary_average("airadar") for _, scores in evaluated])
    recommendation_aihot = _average([scores.recommendation_average("aihot") for _, scores in evaluated])
    recommendation_airadar = _average([scores.recommendation_average("airadar") for _, scores in evaluated])
    return {
        "sample_count": len(evaluated),
        "v2_aihot_avg": summary_aihot,
        "v2_airadar_avg": summary_airadar,
        "v2_ratio": summary_airadar / summary_aihot if summary_aihot else 0.0,
        "v2_pass": bool(summary_aihot and summary_airadar >= summary_aihot * 0.85),
        "v3_aihot_avg": recommendation_aihot,
        "v3_airadar_avg": recommendation_airadar,
        "v3_ratio": recommendation_airadar / recommendation_aihot if recommendation_aihot else 0.0,
        "v3_pass": bool(recommendation_aihot and recommendation_airadar >= recommendation_aihot * 0.80),
    }


def _load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


def load_iteration_counter(path: Path = ITERATION_COUNTER) -> dict[str, int]:
    if not path.exists():
        path.write_text('{"step3_6":0,"step4_6":0}\n', encoding="utf-8")
    data = _load_json(path, {"step3_6": 0, "step4_6": 0})
    return {"step3_6": int(data.get("step3_6", 0)), "step4_6": int(data.get("step4_6", 0))}


def load_known_limit_list(state_path: Path = STATE_FILE) -> list[str]:
    if not state_path.exists():
        return []
    issues: list[str] = []
    for line in state_path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"- \[open\] (ISSUE-\d+ .+)", line.strip())
        if match:
            issues.append(match.group(1))
    return issues


def source_coverage(conn: sqlite3.Connection, state_text: str | None = None) -> dict[str, Any]:
    expected = _load_json(AIHOT_SOURCES, [])
    enabled = {
        row["id"] if isinstance(row, sqlite3.Row) else row[0]
        for row in conn.execute("SELECT id FROM sources WHERE enabled=1").fetchall()
    }
    expected_slugs = {source["slug"] for source in expected}
    state_lower = (state_text if state_text is not None else STATE_FILE.read_text(encoding="utf-8")).lower()
    known_missing = [
        source["slug"]
        for source in expected
        if source["slug"] not in enabled
        and (source["slug"].lower() in state_lower or str(source.get("name", "")).lower() in state_lower)
    ]
    missing = sorted(expected_slugs - enabled)
    blocking_missing = [slug for slug in missing if slug not in known_missing]
    extra = sorted(enabled - expected_slugs)
    return {
        "pass": not blocking_missing and not extra,
        "missing": missing,
        "known_missing": sorted(known_missing),
        "blocking_missing": sorted(blocking_missing),
        "extra": extra,
        "enabled_count": len(enabled),
        "expected_count": len(expected_slugs),
    }


def tag_distribution(items: list[RadarItem]) -> dict[str, Any]:
    all_tags = [tag for item in items for tag in item.tags]
    counter = Counter(all_tags)
    top_tag, top_count = counter.most_common(1)[0] if counter else (None, 0)
    top_share = top_count / len(all_tags) if all_tags else 0.0
    per_item_ok = all(2 <= len(item.tags) <= 4 for item in items)
    all_in_vocab = all(is_in_vocabulary(tag) for tag in all_tags)
    deterministic_hits = sum(
        1
        for item in items
        if set(
            deterministic_tags(
                source_id=item.source_id,
                source_name=item.source_name,
                url=item.url,
                title=item.title,
                content_text=item.content_text,
            )
        )
        & set(item.tags)
    )
    return {
        "pass": bool(items) and per_item_ok and top_share <= 0.30 and all_in_vocab,
        "item_count": len(items),
        "tag_count": len(all_tags),
        "top_tag": top_tag,
        "top_share": top_share,
        "per_item_ok": per_item_ok,
        "all_in_vocab": all_in_vocab,
        "deterministic_hit_items": deterministic_hits,
        "counts": dict(counter.most_common()),
    }


def _compact_len(value: str) -> int:
    return len(re.sub(r"\s+", "", value))


def build_compare_audit_payload(
    *,
    matched_pairs: list[MatchedPair],
    html_text: str,
    report_date: str,
    comparison_note: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    radar_lengths = [_compact_len(pair.airadar.why_recommend) for pair in matched_pairs]
    aihot_lengths = [_compact_len(pair.aihot.why_recommend) for pair in matched_pairs]
    radar_avg = _average(radar_lengths)
    aihot_avg = _average(aihot_lengths)
    brand_missing: list[dict[str, Any]] = []
    for pair in matched_pairs:
        expected = deterministic_tags(
            source_id=pair.airadar.source_id,
            source_name=pair.airadar.source_name,
            url=pair.airadar.url,
            title=pair.airadar.title,
            content_text=pair.airadar.content_text,
        )
        missing = [tag for tag in dict.fromkeys(expected) if tag not in pair.airadar.tags]
        if missing:
            brand_missing.append({"id": pair.airadar.id, "title": pair.airadar.title_zh, "missing": missing})

    checks = {
        "matched_pair_count": {"pass": len(matched_pairs) >= 10, "value": len(matched_pairs), "target": ">=10"},
        "all_url_evidence": {
            "pass": all(pair.match_method == "url" for pair in matched_pairs),
            "value": dict(Counter(pair.match_method for pair in matched_pairs)),
        },
        "no_text_source_match": {"pass": "text+source" not in html_text, "value": "text+source" in html_text},
        "html_pair_count_matches_data": {
            "pass": html_text.count('class="compare-pair"') == len(matched_pairs),
            "value": html_text.count('class="compare-pair"'),
        },
        "recommendation_avg_length": {
            "pass": bool(aihot_avg) and radar_avg <= aihot_avg * 1.25,
            "value": {"airadar": round(radar_avg, 1), "aihot": round(aihot_avg, 1)},
            "target": "airadar <= aihot * 1.25",
        },
        "recommendation_single_length": {
            "pass": all(35 <= value <= 90 for value in radar_lengths),
            "value": {"min": min(radar_lengths or [0]), "max": max(radar_lengths or [0])},
            "target": "35-90 chars each",
        },
        "source_brand_tags": {"pass": not brand_missing, "value": brand_missing[:10]},
    }
    payload = {
        "report_date": report_date,
        "comparison_note": comparison_note,
        "deterministic_checks": checks,
        "sample_pairs": [
            {
                "match_method": pair.match_method,
                "match_score": pair.match_score,
                "url": pair.airadar.url,
                "aihot": {
                    "source": pair.aihot.source,
                    "title": pair.aihot.title,
                    "summary": pair.aihot.summary[:260],
                    "why_recommend": pair.aihot.why_recommend,
                    "tags": pair.aihot.tags,
                },
                "airadar": {
                    "source": pair.airadar.source_name,
                    "title": pair.airadar.title_zh,
                    "summary": pair.airadar.summary_zh[:260],
                    "why_recommend": pair.airadar.why_recommend,
                    "tags": pair.airadar.tags,
                },
            }
            for pair in matched_pairs[:12]
        ],
    }
    return payload, checks


def write_compare_audit(
    *,
    audit_path: Path,
    matched_pairs: list[MatchedPair],
    compare_path: Path,
    report_date: str,
    comparison_note: str,
    provider: CompareAuditProvider | None = None,
) -> bool:
    html_text = compare_path.read_text(encoding="utf-8")
    payload, checks = build_compare_audit_payload(
        matched_pairs=matched_pairs,
        html_text=html_text,
        report_date=report_date,
        comparison_note=comparison_note,
    )
    deterministic_pass = all(bool(check["pass"]) for check in checks.values())
    selected_provider = provider or DeepSeekV4ProCompareAudit()
    llm_result = selected_provider.audit_compare(payload)
    llm_pass = str(llm_result.get("verdict", "")).upper() == "PASS"
    passed = deterministic_pass and llm_pass
    lines = [
        f"# V6 HTML Audit — {report_date}",
        "",
        f"Verdict: {'PASS' if passed else 'FAIL'}",
        f"Provider: {getattr(selected_provider, 'model_id', 'unknown')}",
        "",
        "## Deterministic Checks",
        "",
    ]
    for name, check in checks.items():
        lines.append(f"- {name}: {'PASS' if check['pass'] else 'FAIL'} — {json.dumps(check, ensure_ascii=False)}")
    lines.extend(["", "## LLM Judge", "", f"```json\n{json.dumps(llm_result, ensure_ascii=False, indent=2)}\n```", ""])
    audit_path.write_text("\n".join(lines), encoding="utf-8")
    return passed


def _template_hits(items: list[RadarItem]) -> list[dict[str, Any]]:
    return [
        {"id": item.id, "rank": item.rank, "why_recommend": item.why_recommend}
        for item in items
        if TEMPLATE_RE.search(item.why_recommend)
    ]


def _metric_pack(
    source_stats: dict[str, Any],
    quality_stats: dict[str, Any],
    tag_stats: dict[str, Any],
    score_stats: Any,
    template_hits: list[dict[str, Any]],
) -> dict[str, Any]:
    v3_pass = bool(quality_stats.get("v3_pass")) and not template_hits
    return {
        "V1": {
            "pass": source_stats["pass"],
            "detail": (
                f"enabled={source_stats['enabled_count']}/{source_stats['expected_count']}; "
                f"blocking_missing={source_stats['blocking_missing']}; "
                f"known_missing={source_stats['known_missing']}; extra={source_stats['extra']}"
            ),
        },
        "V2": {
            "pass": quality_stats.get("v2_pass", False),
            "detail": (
                f"AI Radar {quality_stats.get('v2_airadar_avg', 0):.2f} / "
                f"AI Hot {quality_stats.get('v2_aihot_avg', 0):.2f}; "
                f"ratio={quality_stats.get('v2_ratio', 0):.2f}; samples={quality_stats.get('sample_count', 0)}"
            ),
        },
        "V3": {
            "pass": v3_pass,
            "detail": (
                f"AI Radar {quality_stats.get('v3_airadar_avg', 0):.2f} / "
                f"AI Hot {quality_stats.get('v3_aihot_avg', 0):.2f}; "
                f"ratio={quality_stats.get('v3_ratio', 0):.2f}; template_hits={len(template_hits)}"
            ),
        },
        "V4": {
            "pass": tag_stats["pass"],
            "detail": (
                f"items={tag_stats['item_count']}; top={tag_stats['top_tag']} "
                f"{tag_stats['top_share']:.0%}; per_item_ok={tag_stats['per_item_ok']}; "
                f"all_in_vocab={tag_stats['all_in_vocab']}; "
                f"deterministic_hit_items={tag_stats['deterministic_hit_items']}"
            ),
        },
        "V5": {
            "pass": score_stats.passes_v5,
            "detail": (
                f"run={score_stats.run_id}; count={score_stats.count}; span={score_stats.span}; "
                f"stdev={score_stats.stdev:.2f}; top10_unique={score_stats.top10_unique_count}"
            ),
        },
    }


def _worst_cases(evaluated: list[tuple[MatchedPair, JudgeScores]], limit: int = 5) -> list[dict[str, Any]]:
    ranked = sorted(
        evaluated,
        key=lambda item: item[1].summary_average("airadar") + item[1].recommendation_average("airadar"),
    )
    return [
        {
            "rank": pair.airadar.rank,
            "title": pair.airadar.title_zh,
            "summary_airadar": scores.summary_average("airadar"),
            "summary_aihot": scores.summary_average("aihot"),
            "recommendation_airadar": scores.recommendation_average("airadar"),
            "recommendation_aihot": scores.recommendation_average("aihot"),
            "suggestions": scores.suggestions[:3],
        }
        for pair, scores in ranked[:limit]
    ]


def render_markdown_report(
    *,
    report_date: str,
    aihot_count: int,
    airadar_count: int,
    matched_pairs: list[MatchedPair],
    unmatched_airadar: list[RadarItem],
    unmatched_aihot: list[AihotItem],
    metrics: dict[str, Any],
    source_stats: dict[str, Any],
    tag_stats: dict[str, Any],
    score_stats: Any,
    quality_stats: dict[str, Any],
    evaluated: list[tuple[MatchedPair, JudgeScores]],
    known_limit_list: list[str],
    comparison_note: str = "",
) -> str:
    lines = [
        f"# AI Radar Alignment Eval — {report_date}",
        "",
        f"> AI Hot items={aihot_count}; AI Radar items={airadar_count}; matched={len(matched_pairs)}; "
        f"judge_sample={quality_stats.get('sample_count', 0)}",
        "",
    ]
    if len(matched_pairs) < 10:
        lines.extend([f"> ⚠️ Matched pairs below target sample size: {len(matched_pairs)} / 10", ""])
    if comparison_note:
        lines.extend([f"> {comparison_note}", ""])
    lines.extend(["## Summary", ""])
    for key, value in metrics.items():
        lines.append(f"- {key}: {'PASS' if value['pass'] else 'FAIL'} — {value['detail']}")
    lines.extend(
        ["", "## V1 Source Coverage", "", f"```json\n{json.dumps(source_stats, ensure_ascii=False, indent=2)}\n```", ""]
    )
    lines.extend(["## V2 Summary Quality", "", metrics["V2"]["detail"], ""])
    lines.extend(["## V3 Recommendation Quality", "", metrics["V3"]["detail"], ""])
    lines.extend(["### Worst Cases", ""])
    for case in _worst_cases(evaluated):
        lines.append(
            f"- #{case['rank']} {case['title']} — summary {case['summary_airadar']:.1f}/{case['summary_aihot']:.1f}, "
            f"reason {case['recommendation_airadar']:.1f}/{case['recommendation_aihot']:.1f}; "
            f"suggestions={case['suggestions']}"
        )
    if not evaluated:
        lines.append("- No matched pairs were available for judge scoring.")
    lines.extend(
        ["", "## V4 Tag Distribution", "", f"```json\n{json.dumps(tag_stats, ensure_ascii=False, indent=2)}\n```", ""]
    )
    lines.extend(
        [
            "## V5 Score Distribution",
            "",
            f"- run_id: {score_stats.run_id}",
            f"- count: {score_stats.count}",
            f"- min/max/span: {score_stats.minimum}/{score_stats.maximum}/{score_stats.span}",
            f"- stdev: {score_stats.stdev:.2f}",
            f"- top10_scores: {score_stats.top10_scores}",
            "",
            "## Matched / Unmatched",
            "",
            f"- matched: {len(matched_pairs)}",
            f"- AI Radar only: {len(unmatched_airadar)}",
            f"- AI Hot only: {len(unmatched_aihot)}",
            "",
            "## Known Limitations",
            "",
        ]
    )
    lines.extend([f"- {item}" for item in known_limit_list] or ["- None"])
    lines.append("")
    return "\n".join(lines)


def run_eval(
    conn: sqlite3.Connection,
    *,
    selected_date: str | None = None,
    aihot_markdown_path: Path = DEFAULT_AIHOT_MARKDOWN,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    provider: JudgeProvider | None = None,
    match_scope: str = "curated",
    audit: bool = False,
    audit_provider: CompareAuditProvider | None = None,
) -> EvaluationArtifacts:
    aihot_items = load_aihot_items_from_path(aihot_markdown_path)
    run_id, run_date, radar_items = load_airadar_items(conn, selected_date)
    report_date = (selected_date or run_date or "").replace("-", "") or "latest"
    comparison_note = ""
    if match_scope == "curated":
        comparison_items = radar_items
    elif match_scope == "all-db-url":
        comparison_items = load_airadar_items_by_aihot_urls(conn, aihot_items)
        comparison_note = (
            "DB-wide URL-proven same-article comparison set; current curated ranking diagnostics remain in V4/V5 "
            "and AI Radar-only lists."
        )
    else:
        raise ValueError(f"unknown eval match_scope: {match_scope}")
    matched_pairs, _, unmatched_aihot = match_items(aihot_items, comparison_items)
    _, unmatched_airadar, _ = match_items(aihot_items, radar_items)
    selected_provider = provider or DeepSeekV4ProJudge()
    evaluated = evaluate_pairs(matched_pairs, selected_provider) if matched_pairs else []
    quality_stats = _aggregate_scores(evaluated)
    source_stats = source_coverage(conn)
    tag_stats = tag_distribution(radar_items)
    score_stats = score_distribution(conn, run_id)
    template_hits = _template_hits(radar_items)
    metrics = _metric_pack(source_stats, quality_stats, tag_stats, score_stats, template_hits)
    output_dir.mkdir(parents=True, exist_ok=True)
    known_limit_list = load_known_limit_list()
    iteration_counter = load_iteration_counter()
    report = render_markdown_report(
        report_date=report_date,
        aihot_count=len(aihot_items),
        airadar_count=len(radar_items),
        matched_pairs=matched_pairs,
        unmatched_airadar=unmatched_airadar,
        unmatched_aihot=unmatched_aihot,
        metrics=metrics,
        source_stats=source_stats,
        tag_stats=tag_stats,
        score_stats=score_stats,
        quality_stats=quality_stats,
        evaluated=evaluated,
        known_limit_list=known_limit_list,
        comparison_note=comparison_note,
    )
    report_path = output_dir / f"eval-report-{report_date}.md"
    compare_path = output_dir / f"v6-compare-{report_date}.html"
    report_path.write_text(report, encoding="utf-8")
    compare_path.write_text(
        render_compare_html(
            matched_pairs=[pair.to_dict() for pair in matched_pairs],
            unmatched_airadar=[item.to_dict() for item in unmatched_airadar],
            unmatched_aihot=[item.to_dict() for item in unmatched_aihot],
            metrics=metrics,
            iteration_counter=iteration_counter,
            known_limit_list=known_limit_list,
            report_date=report_date,
            comparison_note=comparison_note,
        ),
        encoding="utf-8",
    )
    audit_path: Path | None = None
    if audit:
        audit_path = output_dir / f"v6-html-audit-{report_date}.md"
        audit_passed = write_compare_audit(
            audit_path=audit_path,
            matched_pairs=matched_pairs,
            compare_path=compare_path,
            report_date=report_date,
            comparison_note=comparison_note,
            provider=audit_provider,
        )
        if not audit_passed:
            raise RuntimeError(f"V6 compare audit failed: {audit_path}")
    return EvaluationArtifacts(
        report_path=report_path,
        compare_path=compare_path,
        report_date=report_date,
        metrics=metrics,
        matched_count=len(matched_pairs),
        sample_count=quality_stats.get("sample_count", 0),
        audit_path=audit_path,
    )
