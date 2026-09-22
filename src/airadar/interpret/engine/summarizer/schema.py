from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from typing import Any

RECOMMENDATIONS = {"必读", "值得一看", "可跳过"}

REQUIRED_SUMMARY_MODULES: tuple[str, ...] = (
    "文章概况",
    "独特亮点",
    "可动手实践",
    "可复用认知",
    "关键词与分类标签",
    "价值判断",
)

# Some modules may render with split section headers (e.g. "关键词" + "分类标签"
# instead of one heading "关键词与分类标签"). The aliases let validation accept
# either form.
MODULE_ALIASES: dict[str, tuple[str, ...]] = {
    "关键词与分类标签": ("关键词", "分类标签"),
}


@dataclass(frozen=True)
class Validation:
    ok: bool
    modules_ok: bool
    keyword_format_ok: bool
    tags_ok: bool
    missing_modules: list[str] = field(default_factory=list)
    tag_violations: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ParsedSummary:
    summary_md: str
    recommendation: str
    criteria_reason: str
    criteria_reason_source: str
    save_decision: bool
    save_reason: str
    tags: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    projects: list[dict[str, Any]] = field(default_factory=list)
    validation: Validation | None = None


@dataclass(frozen=True)
class ArticleDocument:
    title: str
    content: str
    source: str = ""
    url: str = ""
    publish_date: str | None = None


@dataclass(frozen=True)
class SummaryResult:
    slug: str
    title: str
    source: str
    url: str
    publish_date: str | None
    saved_at: str
    summary_md: str
    recommendation: str
    criteria_reason: str
    save_decision: bool
    save_reason: str
    tags: list[str]
    keywords: list[str]
    projects: list[dict[str, Any]]
    model_name: str
    llm_metadata: dict[str, Any] = field(default_factory=dict)
    validation: Validation | None = None

    def to_meta_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("summary_md", None)
        return data


def parse_summary_output(output: str, *, known_tags: Iterable[str] | None = None) -> ParsedSummary:
    """Parse LLM Markdown output and compute the save decision deterministically.

    When ``known_tags`` is provided, also computes a Validation record so the
    orchestrating agent can trust the summary without re-reading it.
    """
    summary_md, metadata = _split_final_json_block(output)
    recommendation = _coerce_recommendation(metadata.get("recommendation")) or _extract_recommendation(summary_md)
    criteria_reason = metadata.get("criteria_reason")
    if isinstance(criteria_reason, str) and criteria_reason.strip():
        criteria_reason_source = "json"
    else:
        criteria_reason = _extract_value_judgment_reason(summary_md, recommendation)
        if criteria_reason is None:
            raise ValueError("summary JSON missing non-empty criteria_reason")
        criteria_reason_source = "markdown_value_judgment_line"
    tags = _string_list(metadata.get("tags")) or _extract_tags(summary_md)
    keywords = _string_list(metadata.get("keywords")) or _extract_keywords(summary_md)
    projects = metadata.get("projects") if isinstance(metadata.get("projects"), list) else []
    save_decision, save_reason = compute_save_decision(summary_md, recommendation)
    validation = (
        validate_summary(
            summary_md=summary_md,
            tags=tags,
            keywords=keywords,
            known_tags=set(known_tags),
        )
        if known_tags is not None
        else None
    )

    return ParsedSummary(
        summary_md=summary_md.strip() + "\n",
        recommendation=recommendation,
        criteria_reason=criteria_reason.strip(),
        criteria_reason_source=criteria_reason_source,
        save_decision=save_decision,
        save_reason=save_reason,
        tags=tags,
        keywords=keywords,
        projects=[project for project in projects if isinstance(project, dict)],
        validation=validation,
    )


def validate_summary(
    *,
    summary_md: str,
    tags: list[str],
    keywords: list[str],
    known_tags: set[str],
) -> Validation:
    """Self-check the summary against design-doc requirements."""
    missing_modules: list[str] = []
    for name in REQUIRED_SUMMARY_MODULES:
        if name in summary_md:
            continue
        aliases = MODULE_ALIASES.get(name)
        if aliases and all(alias in summary_md for alias in aliases):
            continue
        missing_modules.append(name)
    modules_ok = not missing_modules

    extracted_keywords = _extract_keywords(summary_md)
    keyword_format_ok = bool(extracted_keywords) or bool(keywords)

    tag_violations = [tag for tag in tags if tag not in known_tags]
    tags_ok = not tag_violations

    return Validation(
        ok=modules_ok and keyword_format_ok and tags_ok,
        modules_ok=modules_ok,
        keyword_format_ok=keyword_format_ok,
        tags_ok=tags_ok,
        missing_modules=missing_modules,
        tag_violations=tag_violations,
    )


def compute_save_decision(summary_md: str, recommendation: str) -> tuple[bool, str]:
    primary_actionable, secondary_actionable = _actionable_counts(summary_md)
    if primary_actionable:
        return True, f"有 {primary_actionable} 个 primary ✅可实现项。"
    if recommendation in {"必读", "值得一看"}:
        return True, f"推荐等级为 {recommendation}。"
    if recommendation == "可跳过" and secondary_actionable:
        return True, f"推荐等级为可跳过，但有 {secondary_actionable} 个 secondary ✅可实现项。"
    return False, "推荐等级为可跳过，且无 meaningful ✅可实现项。"


def _split_final_json_block(output: str) -> tuple[str, dict[str, Any]]:
    matches = list(re.finditer(r"```json\s*(\{.*?\})\s*```", output, flags=re.DOTALL | re.IGNORECASE))
    if not matches:
        return output, {}

    match = matches[-1]
    metadata_text = match.group(1)
    summary_md = (output[: match.start()] + output[match.end() :]).strip()
    try:
        return summary_md, json.loads(metadata_text)
    except json.JSONDecodeError:
        try:
            from json_repair import repair_json

            repaired = repair_json(metadata_text)
            parsed = json.loads(repaired)
            return summary_md, parsed if isinstance(parsed, dict) else {}
        except Exception:
            return summary_md, {}


def _coerce_recommendation(value: Any) -> str | None:
    if isinstance(value, str):
        for recommendation in RECOMMENDATIONS:
            if recommendation in value:
                return recommendation
    return None


def _extract_recommendation(summary_md: str) -> str:
    match = re.search(r"推荐等级\*\*?\s*[:：]\s*(必读|值得一看|可跳过)", summary_md)
    if match:
        return match.group(1)
    match = re.search(r"推荐等级\s*[:：]\s*(必读|值得一看|可跳过)", summary_md)
    return match.group(1) if match else "可跳过"


def _extract_value_judgment_reason(summary_md: str, recommendation: str) -> str | None:
    sections = list(
        re.finditer(
        r"^###\s*[^\n]*价值判断[^\n]*\n(?P<body>.*?)(?=^###\s+|\Z)",
        summary_md,
        flags=re.MULTILINE | re.DOTALL,
        )
    )
    if len(sections) != 1:
        return None
    recommendation_lines = re.findall(
        r"^\s*(?:[-*]\s*)?\*{0,2}推荐等级\*{0,2}\s*[:：]\s*(.*?)\s*$",
        sections[0].group("body"),
        flags=re.MULTILINE,
    )
    if len(recommendation_lines) != 1:
        return None
    match = re.fullmatch(
        r"(必读|值得一看|可跳过)\s*[（(]([^()（）\r\n]+)[）)]",
        recommendation_lines[0],
    )
    if match is None or match.group(1) != recommendation:
        return None
    reason = match.group(2).strip()
    return reason or None


def _extract_keywords(summary_md: str) -> list[str]:
    return _dedupe([match.strip() for match in re.findall(r"`#([^`#]+)`", summary_md)])


def _extract_tags(summary_md: str) -> list[str]:
    tag_line = re.search(r"\*\*分类标签\*\*\s*[:：]\s*(.+)", summary_md)
    if not tag_line:
        tag_line = re.search(r"分类标签\s*[:：]\s*(.+)", summary_md)
    if not tag_line:
        return []
    line = tag_line.group(1)
    backtick_tags = re.findall(r"`([^`]+)`", line)
    if backtick_tags:
        return _dedupe([tag.strip() for tag in backtick_tags])
    return _dedupe([tag.strip() for tag in re.split(r"[,\s，、]+", line) if tag.strip()])


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return _dedupe([str(item).lstrip("#").strip() for item in value if str(item).strip()])


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _actionable_counts(summary_md: str) -> tuple[int, int]:
    primary = 0
    secondary = 0
    for line in summary_md.splitlines():
        if "✅可实现" not in line:
            continue
        if line.lstrip().startswith("|"):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            first_cell = cells[0] if cells else ""
            if re.match(r"^\[[^\]]+\]", first_cell):
                secondary += 1
            else:
                primary += 1
        elif re.search(r"\[[^\]]+\].*✅可实现", line):
            secondary += 1
        else:
            primary += 1
    return primary, secondary

