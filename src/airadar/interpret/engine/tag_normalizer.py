"""Save-time tag normalization for summary-agent metadata."""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import re
import sys
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np

from .paths import tags_path

TagAction = Literal["merged", "created", "unchanged", "created_pending_classification"]

DEFAULT_SIMILARITY_THRESHOLD = 0.85
TAGS_DOC_RELATIVE_PATH = Path("agents/summary-agent/docs/tags.md")
TAG_DIMENSIONS = ("构建", "评测", "进化", "应用方向")

CLASSIFY_SYSTEM_PROMPT = "你是 tag 维度分类助手。"
CLASSIFY_USER_PROMPT = """给定一个新 tag、一篇文章的标题和摘要，把 tag 归入下面 4 个维度之一，
并写一句覆盖范围描述（不超过 25 字）。

维度（参考 docs/tags.md）：
- 构建：怎么造 Agent（架构、工具、训练等）
- 评测：怎么评 Agent
- 进化：Agent 怎么变强
- 应用方向：做什么应用产品

新 tag：{tag}
文章标题：{article_title}
文章摘要节选：{summary_excerpt}

只输出一行 JSON：{{"dimension": "...", "覆盖范围": "..."}}
"""

_VECTOR_CACHE: dict[Path, tuple[float, list[tuple[str, np.ndarray]]]] = {}


@dataclass(frozen=True)
class TagDecision:
    original: str
    normalized: str
    action: TagAction
    similarity: float | None
    dimension: str | None


@dataclass(frozen=True)
class NormalizationReport:
    decisions: list[TagDecision]
    normalization_failed: bool
    failure_reason: str | None
    tags_md_written: bool


def normalize_meta_tags(
    meta: dict,
    *,
    project_root: Path,
    article_title: str,
    article_summary_excerpt: str,
    threshold: float | None = None,
    embedding_client_factory: Callable[[], Any] | None = None,
    llm_config: Any | None = None,
) -> NormalizationReport:
    """Normalize ``meta['tags']`` and ``meta['projects'][*]['tags']`` in place.

    External dependency failures are best-effort: the original metadata remains
    unchanged, a warning is emitted, and the report records the failure.
    """
    resolved_root = project_root.resolve()
    resolved_threshold = _resolve_threshold(threshold)
    candidates = _collect_candidate_tags(meta)
    if not candidates:
        return NormalizationReport([], False, None, False)

    decisions: list[TagDecision] = []
    tags_md_written = False
    try:
        client = embedding_client_factory() if embedding_client_factory else _get_default_embedding_client()
        with _global_tags_lock(resolved_root):
            known_vectors = _load_known_tag_vectors(resolved_root, client)
            known_tags = [tag for tag, _ in known_vectors]
            known_set = set(known_tags)
            unknown_tags = [tag for tag in candidates if tag not in known_set]
            unknown_vectors = _embed_tags(client, unknown_tags) if unknown_tags else {}

            mapping: dict[str, str] = {}
            current_known_vectors = list(known_vectors)

            for tag in candidates:
                if tag in known_set:
                    mapping[tag] = tag
                    decisions.append(TagDecision(tag, tag, "unchanged", None, None))
                    continue

                tag_vector = unknown_vectors[tag]
                best_tag, best_score = _best_match(tag_vector, current_known_vectors)
                if best_tag is not None and best_score >= resolved_threshold:
                    mapping[tag] = best_tag
                    decisions.append(TagDecision(tag, best_tag, "merged", best_score, None))
                    continue

                classification = _classify_unknown(
                    tag,
                    article_title=article_title,
                    article_summary_excerpt=article_summary_excerpt,
                    llm_config=llm_config,
                )
                if classification is None:
                    mapping[tag] = tag
                    decisions.append(TagDecision(tag, tag, "created_pending_classification", None, None))
                    _apply_mapping(meta, mapping)
                    if tags_md_written:
                        _invalidate_cache(resolved_root)
                    return _failed_report(
                        decisions,
                        "tag classification failed",
                        tags_md_written=tags_md_written,
                    )

                dimension = classification["dimension"]
                coverage = classification["coverage"]
                _append_to_tags_md(resolved_root, tag, dimension, coverage, article_title)
                tags_md_written = True
                known_set.add(tag)
                current_known_vectors.append((tag, tag_vector))
                mapping[tag] = tag
                decisions.append(TagDecision(tag, tag, "created", None, dimension))

            _apply_mapping(meta, mapping)
            if tags_md_written:
                _invalidate_cache(resolved_root)
            return NormalizationReport(decisions, False, None, tags_md_written)
    except Exception as exc:  # noqa: BLE001 - best-effort save path must not throw.
        if tags_md_written:
            _invalidate_cache(resolved_root)
        return _failed_report(decisions, str(exc), tags_md_written=tags_md_written)


def report_to_json(report: NormalizationReport) -> dict[str, Any]:
    decisions = []
    for decision in report.decisions:
        item: dict[str, Any] = {
            "original": decision.original,
            "normalized": decision.normalized,
            "action": decision.action,
        }
        if decision.similarity is not None:
            item["similarity"] = round(decision.similarity, 4)
        if decision.dimension is not None:
            item["dimension"] = decision.dimension
        decisions.append(item)

    payload: dict[str, Any] = {
        "decisions": decisions,
        "normalization_failed": report.normalization_failed,
        "tags_md_written": report.tags_md_written,
    }
    if report.failure_reason is not None:
        payload["failure_reason"] = report.failure_reason
    return payload


def load_known_tags(project_root: Path) -> set[str]:
    tags_doc = _tags_doc_path(project_root)
    if not tags_doc.exists():
        return set()
    pattern = re.compile(r"^\|\s*`([^`]+)`\s*\|", re.MULTILINE)
    return {match.group(1) for match in pattern.finditer(tags_doc.read_text(encoding="utf-8"))}


def get_embeddings(client: Any, texts: list[str]) -> np.ndarray:
    from .embedding import get_embeddings as embedding_get_embeddings

    return embedding_get_embeddings(client, texts)


def _resolve_threshold(threshold: float | None) -> float:
    if threshold is not None:
        return threshold
    raw = os.environ.get("SUMMARIZER_TAG_SIMILARITY_THRESHOLD", str(DEFAULT_SIMILARITY_THRESHOLD))
    try:
        return float(raw)
    except ValueError:
        print(
            f"Warning: Invalid SUMMARIZER_TAG_SIMILARITY_THRESHOLD={raw!r}; using {DEFAULT_SIMILARITY_THRESHOLD}.",
            file=sys.stderr,
        )
        return DEFAULT_SIMILARITY_THRESHOLD


def _collect_candidate_tags(meta: dict) -> list[str]:
    ordered: list[str] = []
    for tag in _string_list(meta.get("tags")):
        if tag not in ordered:
            ordered.append(tag)
    for project in meta.get("projects", []) or []:
        if not isinstance(project, dict):
            continue
        for tag in _string_list(project.get("tags")):
            if tag not in ordered:
                ordered.append(tag)
    return ordered


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item.strip()]


def _load_known_tag_vectors(project_root: Path, client: Any) -> list[tuple[str, np.ndarray]]:
    tags_doc = _tags_doc_path(project_root)
    mtime = tags_doc.stat().st_mtime if tags_doc.exists() else 0.0
    cached = _VECTOR_CACHE.get(tags_doc)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    tags = sorted(load_known_tags(project_root))
    if not tags:
        vectors: list[tuple[str, np.ndarray]] = []
    else:
        embedded = get_embeddings(client, tags)
        vectors = [(tag, vector) for tag, vector in zip(tags, embedded)]
    _VECTOR_CACHE[tags_doc] = (mtime, vectors)
    return vectors


def _embed_tags(client: Any, tags: list[str]) -> dict[str, np.ndarray]:
    if not tags:
        return {}
    vectors = get_embeddings(client, tags)
    return {tag: vector for tag, vector in zip(tags, vectors)}


def _best_match(tag_vector: np.ndarray, known_vectors: list[tuple[str, np.ndarray]]) -> tuple[str | None, float]:
    if not known_vectors:
        return None, 0.0
    tags = [tag for tag, _ in known_vectors]
    matrix = np.vstack([vector for _, vector in known_vectors])
    similarities = _cosine_similarity(tag_vector, matrix)
    best_index = int(np.argmax(similarities))
    return tags[best_index], float(similarities[best_index])


def _cosine_similarity(query_vec: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    query_norm = np.linalg.norm(query_vec)
    if query_norm == 0:
        return np.zeros(matrix.shape[0])
    matrix_norms = np.linalg.norm(matrix, axis=1)
    matrix_norms = np.where(matrix_norms == 0, 1e-10, matrix_norms)
    return (matrix @ query_vec) / (matrix_norms * query_norm)


def _classify_unknown(
    tag: str,
    *,
    article_title: str,
    article_summary_excerpt: str,
    llm_config: Any | None,
) -> dict[str, str] | None:
    try:
        from airadar.interpret.engine.summarizer.llm import LLMConfig, complete_text

        config = llm_config or LLMConfig.from_values(
            model=None,
            api_key=None,
            base_url=None,
            temperature=0.1,
            max_tokens=500,
        )
        user_prompt = CLASSIFY_USER_PROMPT.format(
            tag=tag,
            article_title=article_title,
            summary_excerpt=article_summary_excerpt[:500],
        )
        response = asyncio.run(complete_text(CLASSIFY_SYSTEM_PROMPT, user_prompt, config))
        parsed = json.loads(response.content.strip())
    except Exception as exc:  # noqa: BLE001 - caller handles best-effort failure.
        print(f"Warning: Tag classification failed for {tag!r}: {exc}", file=sys.stderr)
        return None

    if not isinstance(parsed, dict):
        return None
    dimension = str(parsed.get("dimension", "")).strip()
    coverage = str(parsed.get("覆盖范围") or parsed.get("coverage") or "").strip()
    if dimension not in TAG_DIMENSIONS or not coverage:
        return None
    return {"dimension": dimension, "coverage": coverage}


def _append_to_tags_md(project_root: Path, tag: str, dimension: str, coverage: str, example: str) -> None:
    tags_doc = _tags_doc_path(project_root)
    text = tags_doc.read_text(encoding="utf-8")
    lines = text.splitlines()
    heading_re = re.compile(rf"^###\s+{re.escape(dimension)}(?:（.*）)?\s*$")
    heading_index = next((i for i, line in enumerate(lines) if heading_re.match(line)), None)
    if heading_index is None:
        raise ValueError(f"Could not find dimension section in tags.md: {dimension}")

    insert_at = None
    for idx in range(heading_index + 1, len(lines)):
        line = lines[idx]
        if line.startswith("### "):
            break
        if line.startswith("|") and not line.startswith("|------"):
            insert_at = idx + 1
    if insert_at is None:
        raise ValueError(f"Could not find tag table in dimension section: {dimension}")

    row = f"| `{_escape_cell(tag)}` | {_escape_cell(coverage)} | {_escape_cell(example)} |"
    updated = [*lines[:insert_at], row, *lines[insert_at:]]
    tags_doc.write_text("\n".join(updated).rstrip() + "\n", encoding="utf-8")


def _apply_mapping(meta: dict, mapping: dict[str, str]) -> None:
    meta["tags"] = _normalize_list(meta.get("tags"), mapping)
    projects = meta.get("projects")
    if not isinstance(projects, list):
        return
    for project in projects:
        if isinstance(project, dict) and "tags" in project:
            project["tags"] = _normalize_list(project.get("tags"), mapping)


def _normalize_list(value: Any, mapping: dict[str, str]) -> list[str]:
    normalized: list[str] = []
    for tag in _string_list(value):
        mapped = mapping.get(tag, tag)
        if mapped not in normalized:
            normalized.append(mapped)
    return normalized


@contextmanager
def _global_tags_lock(project_root: Path):
    vocabulary = _tags_doc_path(project_root).resolve()
    if vocabulary.parts[-4:] == ("agents", "summary-agent", "docs", "tags.md"):
        lock_path = vocabulary.parents[3] / "tmp/summary_agent_tags.lock"
    else:
        lock_path = vocabulary.with_name(vocabulary.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        yield


def _get_default_embedding_client() -> Any:
    from .embedding import _get_openai_client

    return _get_openai_client()


def _tags_doc_path(project_root: Path) -> Path:
    return tags_path(project_root if (project_root / "agents/summary-agent/docs/tags.md").is_file() else None)


def _invalidate_cache(project_root: Path) -> None:
    _VECTOR_CACHE.pop(_tags_doc_path(project_root), None)


def _failed_report(
    decisions: list[TagDecision],
    failure_reason: str,
    *,
    tags_md_written: bool,
) -> NormalizationReport:
    print(f"Warning: Tag normalization failed: {failure_reason}", file=sys.stderr)
    return NormalizationReport(
        decisions=decisions,
        normalization_failed=True,
        failure_reason=failure_reason,
        tags_md_written=tags_md_written,
    )


def _escape_cell(value: str) -> str:
    return value.replace("|", "／").replace("\n", " ").strip()
