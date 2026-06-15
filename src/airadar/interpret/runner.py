from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .. import db
from ..wechat_text import has_wechat_title_artifacts, normalize_wechat_title, wechat_slug_seed

DEFAULT_INTERPRET_USER = "default"
DISABLED_MESSAGE = "interpret disabled (set AI_RADAR_ENABLE_INTERPRET=true)"
MISSING_ROOT_MESSAGE = "interpret enabled but AI_ASSISTANT_ROOT is not set"
SUMMARY_AGENT_DIR = Path("agents") / "summary-agent"


@dataclass(frozen=True)
class InterpretSummary:
    processed: int = 0
    errors: int = 0
    skipped: bool = False
    message: str = ""


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _env_flag_enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _assistant_root(value: str | Path | None) -> Path | None:
    configured = value or os.environ.get("AI_ASSISTANT_ROOT")
    if not configured:
        return None
    return Path(configured).expanduser().resolve()


def _interpret_user(value: str | None) -> str:
    return value or os.environ.get("AI_RADAR_INTERPRET_USER") or DEFAULT_INTERPRET_USER


def _summary_agent_scripts(root: Path) -> tuple[Path, Path]:
    agent_dir = root / SUMMARY_AGENT_DIR
    return agent_dir / "summarize.sh", agent_dir / "run.sh"


def _preflight(root: Path) -> tuple[bool, str]:
    summarize_script, run_script = _summary_agent_scripts(root)
    if not root.exists():
        return False, f"skip interpret: AI_ASSISTANT_ROOT does not exist: {root}"
    if not summarize_script.exists() or not os.access(summarize_script, os.X_OK):
        return False, f"skip interpret: summarize.sh is missing or not executable: {summarize_script}"
    if not run_script.exists() or not os.access(run_script, os.X_OK):
        return False, f"skip interpret: run.sh is missing or not executable: {run_script}"
    return True, "ok"


def _json_dumps(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("VIRTUAL_ENV", None)
    return env


def _run_json(cmd: list[str], *, cwd: Path) -> dict[str, Any]:
    completed = subprocess.run(
        cmd,
        cwd=str(cwd),
        check=True,
        text=True,
        capture_output=True,
        env=_subprocess_env(),
    )
    stdout = (completed.stdout or "").strip()
    if not stdout:
        return {}
    try:
        payload = json.loads(stdout)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    for line in reversed(lines):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise ValueError(f"subprocess did not return JSON: {stdout[:500]}")


def _write_input_file(tmp_root: Path, row: sqlite3.Row) -> Path:
    tmp_root.mkdir(parents=True, exist_ok=True)
    path = tmp_root / f"{row['id']}.md"
    title = normalize_wechat_title(row["title"])
    content = str(row["content_text"] or "").strip()
    path.write_text(f"# {title}\n\n{content}\n", encoding="utf-8")
    return path


def _summary_path(batch_dir: Path, slug: str) -> Path:
    return batch_dir / f"{slug}_summary.md"


def _meta_path(batch_dir: Path, slug: str) -> Path:
    return batch_dir / f"{slug}_meta.json"


def _section_body(summary_md: str, title_fragment: str) -> str:
    pattern = re.compile(
        rf"^###\s*[^\n]*{re.escape(title_fragment)}[^\n]*\n(?P<body>.*?)(?=^###\s+|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(summary_md)
    return match.group("body").strip() if match else ""


def _plain_first_paragraph(markdown_text: str) -> str:
    for block in re.split(r"\n\s*\n", markdown_text):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        text = " ".join(lines)
        text = re.sub(r"^[>*\-\d.\s]+", "", text)
        text = re.sub(r"[`*_#]+", "", text)
        return text.strip()
    return ""


def _abstract_from_summary(summary_md: str, fallback: str = "") -> str:
    abstract = _plain_first_paragraph(_section_body(summary_md, "文章概况"))
    if abstract:
        return abstract
    fallback = re.sub(r"\s+", " ", fallback).strip()
    return fallback[:240]


def _recommendation_from_summary(summary_md: str) -> str | None:
    patterns = [
        r"推荐等级\*\*?\s*[:：]\s*(必读|值得一看|可跳过)",
        r"推荐等级\s*[:：]\s*(必读|值得一看|可跳过)",
        r"(必读|值得一看|可跳过)",
    ]
    for pattern in patterns:
        match = re.search(pattern, summary_md)
        if match:
            return match.group(1)
    return None


def _safe_tags(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    tags = []
    for tag in value:
        if isinstance(tag, str) and tag.strip():
            tags.append(tag.strip())
    return tags


def _slug_seed(value: str) -> str:
    return wechat_slug_seed(value)


def _result_slug_for_row(row: sqlite3.Row, result_slug: str | None) -> str:
    if has_wechat_title_artifacts(row["title"]):
        return _slug_seed(str(row["title"] or ""))
    return _slug_seed(str(result_slug or row["title"] or ""))


def _unique_slug(conn: sqlite3.Connection, base_slug: str, item_id: str) -> str:
    base = _slug_seed(base_slug)
    slug = base
    suffix = 2
    while True:
        row = conn.execute(
            "SELECT item_id FROM wechat_interpretations WHERE slug=?",
            (slug,),
        ).fetchone()
        if row is None or row["item_id"] == item_id:
            return slug
        slug = f"{base}-{suffix}"
        suffix += 1


def _check_url_hit(payload: dict[str, Any]) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = [payload]
    for key in ("dedup", "result", "data"):
        value = payload.get(key)
        if isinstance(value, dict):
            candidates.append(value)
    for candidate in candidates:
        summary_path = candidate.get("summary_file_path") or candidate.get("summary_file")
        slug = candidate.get("slug")
        exists = candidate.get("exists") is True or candidate.get("found") is True
        if exists or (summary_path and slug):
            return candidate
    return None


def _read_summary_file(path: Path) -> str:
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(str(path))
    return path.read_text(encoding="utf-8")


def _path_from_ai_assistant(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _summary_agent_index_path(root: Path, user: str) -> Path:
    return root / "data" / "summary_agent" / user / "index.json"


def _summary_file_slug(value: str) -> str:
    stem = Path(value).stem
    return stem.removesuffix("_output")


def _summary_agent_index_entries(root: Path, user: str) -> list[dict[str, Any]]:
    index_path = _summary_agent_index_path(root, user)
    if not index_path.exists():
        return []
    entries = _json_loads(index_path.read_text(encoding="utf-8"), [])
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def _entry_summary_file(entry: dict[str, Any]) -> str | None:
    output = entry.get("output")
    if not isinstance(output, dict):
        return None
    summary_file = output.get("summary_file_path") or output.get("summary_file")
    return summary_file if isinstance(summary_file, str) and summary_file else None


def _same_summary_file(root: Path, left: str, right: str) -> bool:
    if left == right:
        return True
    return _path_from_ai_assistant(root, left).resolve() == _path_from_ai_assistant(root, right).resolve()


def _index_entry_for_slug(root: Path, user: str, slug: str) -> dict[str, Any] | None:
    for entry in _summary_agent_index_entries(root, user):
        summary_file = _entry_summary_file(entry)
        if summary_file and _summary_file_slug(summary_file) == slug:
            return entry
    return None


def _index_entry_for_url(root: Path, user: str, url: str) -> dict[str, Any] | None:
    for entry in _summary_agent_index_entries(root, user):
        metadata = entry.get("metadata")
        if isinstance(metadata, dict) and metadata.get("url") == url:
            return entry
    return None


def _index_entry_for_summary_file(root: Path, user: str, summary_file: str) -> dict[str, Any] | None:
    for entry in _summary_agent_index_entries(root, user):
        entry_summary_file = _entry_summary_file(entry)
        if entry_summary_file and _same_summary_file(root, entry_summary_file, summary_file):
            return entry
    return None


def _index_entry_for_summary_file_any_user(root: Path, summary_file: str) -> dict[str, Any] | None:
    summary_agent_root = root / "data" / "summary_agent"
    if not summary_agent_root.is_dir():
        return None
    for index_path in sorted(summary_agent_root.glob("*/index.json")):
        entry = _index_entry_for_summary_file(root, index_path.parent.name, summary_file)
        if entry is not None:
            return entry
    return None


def _duplicate_slug_from_save_error(error: subprocess.CalledProcessError) -> str | None:
    stderr = error.stderr.decode("utf-8", errors="replace") if isinstance(error.stderr, bytes) else error.stderr
    text = stderr or str(error)
    match = re.search(r"Slug '([^']+)' already exists in index\.json", text)
    return match.group(1) if match else None


def _kb_unique_slug(root: Path, user: str, base_slug: str, item_id: str) -> str:
    index_path = _summary_agent_index_path(root, user)
    entries = _json_loads(index_path.read_text(encoding="utf-8") if index_path.exists() else "[]", [])
    existing: set[str] = set()
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            output = entry.get("output")
            if not isinstance(output, dict):
                continue
            summary_file = output.get("summary_file_path") or output.get("summary_file")
            if isinstance(summary_file, str) and summary_file:
                existing.add(_summary_file_slug(summary_file))
    base = base_slug.strip() or _slug_seed(item_id)
    seed = f"{base}_radar_{item_id[:8]}"
    slug = seed
    suffix = 2
    while slug in existing:
        slug = f"{seed}_{suffix}"
        suffix += 1
    return slug


def _copy_batch_files_for_slug(batch_dir: Path, source_slug: str, target_slug: str) -> None:
    for kind in ("article", "summary"):
        source = batch_dir / f"{source_slug}_{kind}.md"
        target = batch_dir / f"{target_slug}_{kind}.md"
        if not source.is_file():
            raise FileNotFoundError(str(source))
        shutil.copyfile(source, target)


def _patch_batch_meta(meta_path: Path, row: sqlite3.Row, result: dict[str, Any]) -> dict[str, Any]:
    meta = _json_loads(meta_path.read_text(encoding="utf-8") if meta_path.exists() else "{}", {})
    if not isinstance(meta, dict):
        meta = {}
    meta.update(result)
    meta["url"] = row["url"]
    meta["source"] = row["author"] or row["source_name"] or row["source_id"]
    meta["publish_date"] = row["published_at"]
    meta["title"] = normalize_wechat_title(row["title"])
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return meta


def _slug_needs_title_repair(slug: object) -> bool:
    value = str(slug or "")
    return "\\n" in value or "\n" in value or "-n-" in value


def repair_wechat_title_artifacts(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        """
        SELECT wi.item_id, wi.slug, i.title
        FROM wechat_interpretations wi
        JOIN items i ON i.id=wi.item_id
        JOIN sources s ON s.id=i.source_id
        WHERE COALESCE(s.kind, 'feed')='wechat'
        ORDER BY i.published_at DESC, i.fetched_at DESC, i.id DESC
        """
    ).fetchall()
    changed = 0
    for row in rows:
        clean_title = normalize_wechat_title(row["title"])
        title_changed = clean_title != str(row["title"] or "").strip()
        slug_changed = _slug_needs_title_repair(row["slug"])
        if not title_changed and not slug_changed:
            continue
        clean_slug = _unique_slug(conn, _slug_seed(clean_title), str(row["item_id"]))
        conn.execute("UPDATE items SET title=? WHERE id=?", (clean_title, row["item_id"]))
        conn.execute("UPDATE wechat_interpretations SET slug=? WHERE item_id=?", (clean_slug, row["item_id"]))
        changed += 1
    if changed:
        conn.commit()
    return changed


def _save_interpretation(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    slug: str,
    recommendation: str | None,
    save_decision: bool,
    save_reason: str | None,
    abstract: str,
    tags: list[str],
    summary_md: str,
    model: str | None,
    kb_synced: bool,
    error: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO wechat_interpretations (
          item_id, slug, recommendation, save_decision, save_reason, abstract,
          tags_json, summary_md, model, kb_synced, processed_at, error
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(item_id) DO UPDATE SET
          slug=excluded.slug,
          recommendation=excluded.recommendation,
          save_decision=excluded.save_decision,
          save_reason=excluded.save_reason,
          abstract=excluded.abstract,
          tags_json=excluded.tags_json,
          summary_md=excluded.summary_md,
          model=excluded.model,
          kb_synced=excluded.kb_synced,
          processed_at=excluded.processed_at,
          error=excluded.error
        """,
        (
            row["id"],
            slug,
            recommendation,
            1 if save_decision else 0,
            save_reason,
            abstract,
            _json_dumps(tags),
            summary_md,
            model,
            1 if kb_synced else 0,
            _utc_now(),
            error,
        ),
    )
    conn.commit()


def _record_error(conn: sqlite3.Connection, row: sqlite3.Row, error: BaseException | str) -> None:
    message = str(error)
    if isinstance(error, subprocess.CalledProcessError):
        stderr = error.stderr.decode("utf-8", errors="replace") if isinstance(error.stderr, bytes) else error.stderr
        message = stderr or str(error)
    slug = _unique_slug(conn, f"error-{row['id']}", row["id"])
    _save_interpretation(
        conn,
        row,
        slug=slug,
        recommendation=None,
        save_decision=False,
        save_reason=None,
        abstract="",
        tags=[],
        summary_md="",
        model=None,
        kb_synced=False,
        error=message[:2000],
    )


def _candidate_rows(conn: sqlite3.Connection, *, limit: int | None) -> list[sqlite3.Row]:
    query = """
        SELECT i.*, s.name AS source_name, s.kind AS source_kind, s.enabled AS source_enabled
        FROM items i
        JOIN sources s ON s.id=i.source_id
        WHERE COALESCE(s.kind, 'feed')='wechat'
          AND s.enabled=1
          AND NOT EXISTS (
            SELECT 1 FROM wechat_interpretations wi WHERE wi.item_id=i.id
          )
        ORDER BY i.published_at DESC, i.fetched_at DESC, i.id DESC
    """
    params: list[object] = []
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
    return conn.execute(query, params).fetchall()


def _summarize_item(
    *,
    root: Path,
    summarize_script: Path,
    run_script: Path,
    user: str,
    tmp_root: Path,
    row: sqlite3.Row,
) -> dict[str, Any]:
    input_path = _write_input_file(tmp_root, row)
    check_payload = _run_json([str(run_script), "--check-url", row["url"], "--user", user], cwd=root)
    hit = _check_url_hit(check_payload)
    if hit:
        summary_file = hit.get("summary_file_path") or hit.get("summary_file")
        if summary_file:
            try:
                summary_md = _read_summary_file(_path_from_ai_assistant(root, str(summary_file)))
            except OSError:
                summary_md = ""
            if summary_md:
                raw_slug = str(hit.get("slug") or _slug_seed(Path(str(summary_file)).stem.removesuffix("_output")))
                slug = _result_slug_for_row(row, raw_slug)
                entry = (
                    _index_entry_for_url(root, user, str(row["url"] or ""))
                    or _index_entry_for_summary_file(root, user, str(summary_file))
                    or _index_entry_for_summary_file_any_user(root, str(summary_file))
                )
                index_metadata = (
                    entry.get("metadata") if isinstance(entry, dict) and isinstance(entry.get("metadata"), dict) else {}
                )
                hit_metadata = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
                return {
                    "slug": slug,
                    "recommendation": (
                        hit.get("recommendation")
                        or hit_metadata.get("recommendation")
                        or index_metadata.get("recommendation")
                        or _recommendation_from_summary(summary_md)
                    ),
                    "save_decision": True,
                    "save_reason": hit.get("save_reason") or "URL already exists in ai-assistant KB",
                    "tags": (
                        _safe_tags(hit.get("tags"))
                        or _safe_tags(hit_metadata.get("tags"))
                        or _safe_tags(index_metadata.get("tags"))
                    ),
                    "summary_md": summary_md,
                    "model": (
                        hit.get("model")
                        or hit_metadata.get("model_name")
                        or hit_metadata.get("model")
                        or index_metadata.get("model_name")
                        or index_metadata.get("model")
                    ),
                    "kb_synced": True,
                    "saved": False,
                }

    summary_payload = _run_json([str(summarize_script), "--input", str(input_path), "--user", user], cwd=root)
    result = summary_payload.get("result")
    if not isinstance(result, dict):
        raise ValueError("summarize.sh JSON missing result object")
    batch_slug = str(result.get("slug") or _slug_seed(row["title"]))
    slug = _result_slug_for_row(row, batch_slug)
    batch_dir_raw = summary_payload.get("batch_dir")
    if not batch_dir_raw:
        raise ValueError("summarize.sh JSON missing batch_dir")
    batch_dir = Path(str(batch_dir_raw))
    if not batch_dir.is_absolute():
        batch_dir = root / batch_dir
    summary_md = _read_summary_file(_summary_path(batch_dir, batch_slug))
    save_decision = bool(result.get("save_decision"))
    kb_synced = False
    if save_decision:
        meta = _patch_batch_meta(_meta_path(batch_dir, batch_slug), row, result)
        save_slug = slug
        if save_slug != batch_slug:
            _copy_batch_files_for_slug(batch_dir, batch_slug, save_slug)
            meta["slug"] = save_slug
        save_cmd = [
            str(run_script),
            "--save-from-batch",
            save_slug,
            "--user",
            user,
            "--batch-dir",
            str(batch_dir),
            "--meta-json",
            json.dumps(meta, ensure_ascii=False),
        ]
        try:
            _run_json(save_cmd, cwd=root)
        except subprocess.CalledProcessError as exc:
            duplicate_slug = _duplicate_slug_from_save_error(exc)
            if not duplicate_slug:
                raise
            retry_slug = _kb_unique_slug(root, user, duplicate_slug, str(row["id"]))
            _copy_batch_files_for_slug(batch_dir, save_slug, retry_slug)
            meta["slug"] = retry_slug
            _run_json(
                [
                    str(run_script),
                    "--save-from-batch",
                    retry_slug,
                    "--user",
                    user,
                    "--batch-dir",
                    str(batch_dir),
                    "--meta-json",
                    json.dumps(meta, ensure_ascii=False),
                ],
                cwd=root,
            )
            slug = retry_slug
        kb_synced = True
    return {
        "slug": slug,
        "recommendation": result.get("recommendation") or _recommendation_from_summary(summary_md),
        "save_decision": save_decision,
        "save_reason": result.get("save_reason"),
        "tags": _safe_tags(result.get("tags")),
        "summary_md": summary_md,
        "model": result.get("model"),
        "kb_synced": kb_synced,
        "saved": kb_synced,
    }


def run_interpret(
    conn: sqlite3.Connection,
    *,
    backfill: bool = False,
    limit: int | None = None,
    assistant_root: str | Path | None = None,
    user: str | None = None,
    tmp_root: str | Path | None = None,
) -> InterpretSummary:
    del backfill  # Existing rows are always skipped; backfill selects the same unprocessed enabled scope.
    if not _env_flag_enabled("AI_RADAR_ENABLE_INTERPRET"):
        return InterpretSummary(skipped=True, message=DISABLED_MESSAGE)

    root = _assistant_root(assistant_root)
    if root is None:
        return InterpretSummary(skipped=True, message=MISSING_ROOT_MESSAGE)

    ready, message = _preflight(root)
    if not ready:
        print(message)
        return InterpretSummary(skipped=True, message=message)

    summarize_script, run_script = _summary_agent_scripts(root)
    interpret_user = _interpret_user(user)
    tmp_path = Path(tmp_root) if tmp_root is not None else db.PROJECT_ROOT / "tmp" / "interpret"
    rows = _candidate_rows(conn, limit=limit)
    processed = 0
    errors = 0
    for row in rows:
        try:
            result = _summarize_item(
                root=root,
                summarize_script=summarize_script,
                run_script=run_script,
                user=interpret_user,
                tmp_root=tmp_path,
                row=row,
            )
            slug = _unique_slug(conn, str(result["slug"]), row["id"])
            summary_md = str(result.get("summary_md") or "")
            _save_interpretation(
                conn,
                row,
                slug=slug,
                recommendation=str(result["recommendation"]) if result.get("recommendation") else None,
                save_decision=bool(result.get("save_decision")),
                save_reason=str(result["save_reason"]) if result.get("save_reason") else None,
                abstract=_abstract_from_summary(summary_md, str(row["content_text"] or "")),
                tags=_safe_tags(result.get("tags")),
                summary_md=summary_md,
                model=str(result["model"]) if result.get("model") else None,
                kb_synced=bool(result.get("kb_synced")),
                error=None,
            )
            processed += 1
        except Exception as exc:  # noqa: BLE001 - per-item fail-safe is the contract.
            _record_error(conn, row, exc)
            errors += 1
            print(f"interpret item={row['id']} error={exc}")
    return InterpretSummary(processed=processed, errors=errors, message=f"processed={processed} errors={errors}")
