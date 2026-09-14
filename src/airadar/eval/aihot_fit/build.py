"""Build the aihot-fit evalset: AIHOT reference outputs joined to our ``items`` rows."""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

from ... import db
from .common import (
    BUILDER_VERSION,
    BUILDER_VERSION_V2,
    CATEGORY_SLUG_TO_PRIMARY,
    read_json,
    read_jsonl,
    readonly_db_uri,
    sha256_file,
    sha256_text,
    utc_now,
    write_json,
    write_jsonl,
)

# The AIHOT benchmark repo is this repo's ``benchmarks/aihot`` submodule; point
# AI_RADAR_AIHOT_DATASET_ROOT at another checkout of it when the submodule is not
# initialised here. Every default stays repo-relative so no maintainer path is baked in.
_AIHOT_DATASET_ROOT = Path(os.environ.get("AI_RADAR_AIHOT_DATASET_ROOT") or db.PROJECT_ROOT / "benchmarks" / "aihot")
_T5_RAW_DEFAULT = db.PROJECT_ROOT / ".label-serve" / "round45-human" / "t5" / "r2_raw" / "aihot_items_raw_r2.json"
DEFAULT_SOURCES: tuple[tuple[str, Path], ...] = (
    (
        "t2-window-2026-08-19",
        _AIHOT_DATASET_ROOT / "windows" / "2026-08-19T000000Z--2026-08-20T000000Z" / "items.jsonl",
    ),
    (
        "t2-window-2026-08-20",
        _AIHOT_DATASET_ROOT / "windows" / "2026-08-20T000000Z--2026-08-21T000000Z" / "items.jsonl",
    ),
    ("t5-raw-r2-20260905", Path(os.environ.get("AI_RADAR_AIHOT_T5_RAW") or _T5_RAW_DEFAULT)),
)

_X_STATUS_RE = re.compile(
    r"^https?://(?:www\.|mobile\.)?(?:x\.com|twitter\.com)/.*?/status(?:es)?/(\d+)",
    re.IGNORECASE,
)


def normalize_url(url: str) -> tuple[str, str]:
    """Return ``(match_key, match_method)`` for an original URL.

    X / Twitter status links collapse onto the status id (our items store them as
    ``https://x.com/i/web/status/<id>``); everything else drops ``www.``, the fragment
    and the trailing slash, lower-cases scheme and host, and keeps the query sorted
    with ``utm_*`` trackers removed.

    The query has to stay: WeChat (``/s?__biz=..&mid=..&idx=..&sn=..``), Hacker News
    (``/item?id=``) and YouTube (``/watch?v=``) carry the article id there, so dropping
    it collapses every article on such a host onto one key and pairs an AIHOT reference
    with an unrelated item — silently, with ``match_method`` still reading ``url``.
    """
    candidate = url.strip()
    status = _X_STATUS_RE.match(candidate)
    if status:
        return f"x_status:{status.group(1)}", "x_status_id"
    parts = urlsplit(candidate)
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if parts.port and not (
        (parts.scheme == "https" and parts.port == 443) or (parts.scheme == "http" and parts.port == 80)
    ):
        host = f"{host}:{parts.port}"
    path = parts.path.rstrip("/")
    query = urlencode(
        sorted(
            (key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if not key.startswith("utm_")
        )
    )
    suffix = f"?{query}" if query else ""
    return f"{parts.scheme.lower()}://{host}{path}{suffix}", "url"


def question_id_for(match_key: str) -> str:
    return hashlib.sha256(match_key.encode("utf-8")).hexdigest()[:16]


def _portable_source_path(path: Path, batch_name: str) -> str:
    """Describe v2 authorities without committing a maintainer-local checkout path."""
    if batch_name.startswith("t2-window-") and "windows" in path.parts:
        index = path.parts.index("windows")
        return str(Path("benchmarks/aihot", *path.parts[index:]))
    if batch_name.startswith("t5-"):
        return ".label-serve/round45-human/t5/r2_raw/aihot_items_raw_r2.json"
    try:
        return str(path.resolve().relative_to(db.PROJECT_ROOT.resolve()))
    except ValueError:
        return f"external/{path.name}"


@dataclass(frozen=True)
class ReferenceRecord:
    aihot_id: str
    aihot_url: str | None
    original_url: str
    title: str | None
    category_slug: str | None
    tags: list[str] | None
    score_0_100: float | None
    selected: bool
    summary: str | None
    reason: str | None
    published_at: str | None

    def as_reference(self) -> dict[str, Any]:
        return {
            "provider": "aihot",
            "aihot_id": self.aihot_id,
            "aihot_url": self.aihot_url,
            "title": self.title,
            "category_slug": self.category_slug,
            "primary_category": CATEGORY_SLUG_TO_PRIMARY.get(self.category_slug or ""),
            "tags": self.tags,
            "score_0_100": self.score_0_100,
            "selected": self.selected,
            "summary": self.summary,
            "reason": self.reason,
            "published_at": self.published_at,
        }


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _from_window_record(record: dict[str, Any]) -> ReferenceRecord:
    tags = record.get("tags")
    return ReferenceRecord(
        aihot_id=str(record["id"]),
        aihot_url=record.get("aihot_url"),
        original_url=str(record["original_url"]),
        title=record.get("aihot_title"),
        category_slug=record.get("aihot_category_slug"),
        tags=[str(tag) for tag in tags] if isinstance(tags, list) else None,
        score_0_100=_float_or_none(record.get("aihot_score_0_to_100")),
        selected=bool(record.get("aihot_selected")),
        summary=record.get("aihot_summary"),
        reason=record.get("aihot_recommendation_reason"),
        published_at=record.get("published_at"),
    )


def _from_raw_record(record: dict[str, Any]) -> ReferenceRecord:
    return ReferenceRecord(
        aihot_id=str(record["id"]),
        aihot_url=record.get("aihot_url"),
        original_url=str(record["original_url"]),
        title=record.get("title"),
        category_slug=record.get("category"),
        tags=None,
        score_0_100=_float_or_none(record.get("score")),
        selected=bool(record.get("selected")),
        summary=record.get("summary"),
        reason=record.get("reason"),
        published_at=record.get("published_at"),
    )


def load_reference_batch(path: Path) -> list[ReferenceRecord]:
    """``.jsonl`` files are t2 window captures (aihot-item-v1); ``.json`` files are t5 raw lists."""
    if path.suffix == ".jsonl":
        return [_from_window_record(record) for record in read_jsonl(path) if record.get("original_url")]
    payload = read_json(path)
    if not isinstance(payload, list):
        raise ValueError(f"{path}: raw batch must be a JSON list")
    return [_from_raw_record(record) for record in payload if record.get("original_url")]


@dataclass(frozen=True)
class DbItem:
    item_id: str
    source_id: str
    tier: str
    source_name: str
    source_kind: str
    url: str
    title: str
    author: str | None
    published_at: str
    content_text: str

    def as_input(self, evalset_version: str) -> dict[str, Any]:
        payload = {
            "item_id": self.item_id,
            "source_id": self.source_id,
            "tier": self.tier,
            "url": self.url,
            "title": self.title,
            "author": self.author,
            "published_at": self.published_at,
            "content_text": self.content_text,
            "content_sha256": sha256_text(self.content_text),
        }
        if evalset_version == "v2":
            payload.update({"source_name": self.source_name, "source_kind": self.source_kind})
        return payload


def _index_item_urls(conn: sqlite3.Connection) -> tuple[dict[str, str], int]:
    """Map normalized url key -> item id (first fetched wins); return the duplicate count."""
    index: dict[str, str] = {}
    duplicates = 0
    for item_id, url in conn.execute("SELECT id, url FROM items ORDER BY fetched_at ASC, id ASC"):
        key, _ = normalize_url(str(url))
        if key in index:
            duplicates += 1
            continue
        index[key] = str(item_id)
    return index, duplicates


def _fetch_item(conn: sqlite3.Connection, item_id: str) -> DbItem:
    row = conn.execute(
        """
        SELECT i.id, i.source_id, COALESCE(s.tier, 'unknown'), COALESCE(s.name, i.source_id),
               COALESCE(s.kind, 'feed'), i.url, i.title, i.author, i.published_at, i.content_text
        FROM items i LEFT JOIN sources s ON s.id = i.source_id
        WHERE i.id = ?
        """,
        (item_id,),
    ).fetchone()
    if row is None:
        raise LookupError(item_id)
    return DbItem(
        item_id=str(row[0]),
        source_id=str(row[1]),
        tier=str(row[2]),
        source_name=str(row[3]),
        source_kind=str(row[4]),
        url=str(row[5]),
        title=str(row[6]),
        author=row[7],
        published_at=str(row[8]),
        content_text=str(row[9] or ""),
    )


def build_evalset(
    *,
    db_path: Path,
    out_dir: Path,
    sources: tuple[tuple[str, Path], ...] = DEFAULT_SOURCES,
    evalset_version: str = "v1",
    base_questions_path: Path | None = None,
) -> dict[str, Any]:
    """Write ``questions.jsonl`` + ``manifest.json`` into ``out_dir``; return the manifest."""
    if evalset_version not in {"v1", "v2"}:
        raise ValueError(f"unknown evalset version: {evalset_version}")
    builder_version = BUILDER_VERSION if evalset_version == "v1" else BUILDER_VERSION_V2
    built_at = utc_now()
    conn = sqlite3.connect(readonly_db_uri(db_path), uri=True)
    try:
        url_index, db_url_duplicates = _index_item_urls(conn)
        max_fetched_at = conn.execute("SELECT max(fetched_at) FROM items").fetchone()[0]
        questions: dict[str, dict[str, Any]] = {}
        batches: dict[str, dict[str, Any]] = {}
        duplicates: list[dict[str, str]] = []
        unknown_category_slugs: Counter[str] = Counter()
        for batch_name, source_path in sources:
            records = load_reference_batch(source_path)
            source_sha256 = sha256_file(source_path)
            recorded_source_path = (
                str(source_path) if evalset_version == "v1" else _portable_source_path(source_path, batch_name)
            )
            matched = 0
            unmatched = 0
            deduped = 0
            for record in records:
                key, method = normalize_url(record.original_url)
                item_id = url_index.get(key)
                if item_id is None:
                    unmatched += 1
                    continue
                matched += 1
                if record.category_slug not in CATEGORY_SLUG_TO_PRIMARY:
                    unknown_category_slugs[str(record.category_slug)] += 1
                question_id = question_id_for(key)
                existing = questions.get(question_id)
                if existing is not None:
                    keep_new = bool(record.tags) and not existing["reference"]["tags"]
                    duplicates.append(
                        {
                            "question_id": question_id,
                            "kept_batch": batch_name if keep_new else existing["provenance"]["batch"],
                            "dropped_batch": existing["provenance"]["batch"] if keep_new else batch_name,
                        }
                    )
                    deduped += 1
                    if not keep_new:
                        continue
                    batches[existing["provenance"]["batch"]]["kept"] -= 1
                item = _fetch_item(conn, item_id)
                questions[question_id] = {
                    "question_id": question_id,
                    "input": item.as_input(evalset_version),
                    "reference": record.as_reference(),
                    "provenance": {
                        "batch": batch_name,
                        "source_file": recorded_source_path,
                        "source_sha256": source_sha256,
                        "match_method": method,
                        "built_at": built_at,
                        "builder_version": builder_version,
                    },
                }
                batches.setdefault(batch_name, {"kept": 0})
                batches[batch_name]["kept"] += 1
            batches.setdefault(batch_name, {"kept": 0})
            batches[batch_name].update(
                {
                    "source_file": recorded_source_path,
                    "source_sha256": source_sha256,
                    "read": len(records),
                    "matched": matched,
                    "unmatched": unmatched,
                    "deduped": deduped,
                }
            )
    finally:
        conn.close()

    ordered = [questions[question_id] for question_id in sorted(questions)]
    base_sha256: str | None = None
    if evalset_version == "v2":
        base_path = base_questions_path or (_AIHOT_DATASET_ROOT / "evalsets/aihot-fit-v1/questions.jsonl")
        base_questions = {str(row["question_id"]): row for row in read_jsonl(base_path)}
        if set(base_questions) != set(questions):
            raise ValueError("v2 question ids differ from the frozen v1 authority")
        for question in ordered:
            base = base_questions[str(question["question_id"])]
            if base["reference"] != question["reference"]:
                raise ValueError(f"v2 reference differs from v1 for {question['question_id']}")
            question["input"] = {
                **base["input"],
                "source_name": question["input"]["source_name"],
                "source_kind": question["input"]["source_kind"],
            }
            question["reference"] = base["reference"]
        base_sha256 = sha256_file(base_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    questions_path = out_dir / "questions.jsonl"
    write_jsonl(questions_path, ordered)
    by_category: Counter[str] = Counter(str(question["reference"]["primary_category"]) for question in ordered)
    by_selected: Counter[str] = Counter(str(bool(question["reference"]["selected"])) for question in ordered)
    manifest = {
        "evalset": f"aihot-fit-{evalset_version}",
        "builder_version": builder_version,
        "built_at": built_at,
        "db_path": str(db_path) if evalset_version == "v1" else "data/radar.db",
        "db_items_max_fetched_at": max_fetched_at,
        "db_url_index_duplicates": db_url_duplicates,
        "batches": batches,
        "duplicates": duplicates,
        "duplicate_count": len(duplicates),
        "unknown_category_slugs": dict(unknown_category_slugs),
        "question_count": len(ordered),
        "questions_sha256": sha256_file(questions_path),
        "by_primary_category": dict(sorted(by_category.items())),
        "by_selected": dict(sorted(by_selected.items())),
        "with_tags": sum(1 for question in ordered if question["reference"]["tags"]),
        "with_summary": sum(1 for question in ordered if question["reference"]["summary"]),
        "with_reason": sum(1 for question in ordered if question["reference"]["reason"]),
    }
    if evalset_version == "v2":
        manifest.update(
            {
                "thresholds_status": "absent",
                "baseline_status": "absent",
                "direct_presentation_inputs": [
                    "source_id",
                    "source_name",
                    "source_kind",
                    "url",
                    "title",
                    "content_text",
                ],
                "base_evalset": "aihot-fit-v1",
                "base_questions_sha256": base_sha256,
            }
        )
    write_json(out_dir / "manifest.json", manifest)
    return manifest


def validate_evalset(*, evalset_dir: Path, base_questions_path: Path | None = None) -> dict[str, Any]:
    """Validate a persisted v1/v2 evalset offline without rewriting either asset."""
    questions_path = evalset_dir / "questions.jsonl"
    manifest_path = evalset_dir / "manifest.json"
    manifest = read_json(manifest_path)
    if not isinstance(manifest, dict):
        raise ValueError(f"{manifest_path}: manifest must be a JSON object")
    questions = list(read_jsonl(questions_path))
    question_ids: list[str] = []
    for number, question in enumerate(questions, start=1):
        if not isinstance(question, dict):
            raise ValueError(f"{questions_path}:{number}: question must be an object")
        missing = {"question_id", "input", "reference", "provenance"} - set(question)
        if missing:
            raise ValueError(f"{questions_path}:{number}: missing fields {sorted(missing)}")
        if not all(isinstance(question[name], dict) for name in ("input", "reference", "provenance")):
            raise ValueError(f"{questions_path}:{number}: input/reference/provenance must be objects")
        question_ids.append(str(question["question_id"]))
    if question_ids != sorted(question_ids):
        raise ValueError(f"{questions_path}: question_id order is not deterministic")
    if len(question_ids) != len(set(question_ids)):
        raise ValueError(f"{questions_path}: duplicate question_id")
    actual_sha256 = sha256_file(questions_path)
    if manifest.get("questions_sha256") != actual_sha256:
        raise ValueError(f"{manifest_path}: questions_sha256 does not match questions.jsonl")
    if manifest.get("question_count") != len(questions):
        raise ValueError(f"{manifest_path}: question_count does not match questions.jsonl")

    evalset = str(manifest.get("evalset") or "")
    if evalset == "aihot-fit-v2":
        if manifest.get("thresholds_status") != "absent" or manifest.get("baseline_status") != "absent":
            raise ValueError(f"{manifest_path}: v2 thresholds/baseline must remain absent until approved")
        direct_inputs = set(manifest.get("direct_presentation_inputs") or [])
        required_inputs = {"source_id", "source_name", "source_kind", "url", "title", "content_text"}
        if direct_inputs != required_inputs:
            raise ValueError(f"{manifest_path}: v2 direct_presentation_inputs do not match the consumer contract")
        base_path = base_questions_path or (evalset_dir.parent / "aihot-fit-v1/questions.jsonl")
        base_rows = {str(row["question_id"]): row for row in read_jsonl(base_path)}
        if manifest.get("base_questions_sha256") != sha256_file(base_path):
            raise ValueError(f"{manifest_path}: base_questions_sha256 does not match v1 authority")
        if set(base_rows) != set(question_ids):
            raise ValueError(f"{questions_path}: v2 question ids differ from v1 authority")
        for question in questions:
            question_id = str(question["question_id"])
            base = base_rows[question_id]
            if question["reference"] != base["reference"]:
                raise ValueError(f"{questions_path}: v2 reference differs from v1 for {question_id}")
            input_payload = dict(question["input"])
            source_name = input_payload.pop("source_name", None)
            source_kind = input_payload.pop("source_kind", None)
            if (
                not isinstance(source_name, str)
                or not source_name
                or not isinstance(source_kind, str)
                or not source_kind
            ):
                raise ValueError(f"{questions_path}: v2 source fields missing for {question_id}")
            if input_payload != base["input"]:
                raise ValueError(f"{questions_path}: v2 input changed beyond source fields for {question_id}")
    elif evalset != "aihot-fit-v1":
        raise ValueError(f"{manifest_path}: unsupported evalset {evalset!r}")
    return {
        "evalset": evalset,
        "question_count": len(questions),
        "questions_sha256": actual_sha256,
        "base_questions_sha256": manifest.get("base_questions_sha256"),
        "result": "pass",
    }
