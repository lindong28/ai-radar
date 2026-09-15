"""Opt-in, pre-upsert input archive. It never imports legacy evaluation outputs."""
from __future__ import annotations

import fcntl
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import uuid
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..sources.loader import SourceConfig
from ..sources.x_state import without_x_runtime_meta
from .dedup import FetchedItem


def _json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _write(path: Path, value: Any) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("wb") as stream:
        stream.write(_json(value))
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def configuration_digest(source: SourceConfig) -> str:
    value = asdict(source)
    value["meta"] = {k: v for k, v in without_x_runtime_meta(source.meta).items()
                     if k not in {"etag", "last_modified"}}
    return _hash(_json(value))


def validators_digest(meta: dict[str, Any]) -> str | None:
    values = {k: str(meta[k]) for k in ("etag", "last_modified") if meta.get(k)}
    return _hash(_json(values)) if values else None


def read_run(root: Path, run_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
        raise ValueError("invalid raw capture run id")
    directory = root / "runs" / run_id
    manifest = json.loads((directory / "manifest.json").read_bytes())
    if manifest.get("format") != "radar_raw_v1" or manifest.get("run_id") != run_id:
        raise ValueError("raw capture identity mismatch")
    if manifest.get("state") != "completed":
        raise ValueError("raw capture incomplete")
    payload = (directory / "items.jsonl.gz").read_bytes()
    if _hash(payload) != manifest["items_sha256"]:
        raise ValueError("raw capture payload hash mismatch")
    rows = [json.loads(line) for line in gzip.decompress(payload).splitlines()]
    if len(rows) != manifest["item_count"]:
        raise ValueError("raw capture item count mismatch")
    counts: dict[str, int] = {}
    for row in rows:
        FetchedItem(**row)
        counts[row["source_id"]] = counts.get(row["source_id"], 0) + 1
    for slug, record in manifest["sources"].items():
        if counts.pop(slug, 0) != record.get("item_count", 0):
            raise ValueError("raw capture source count mismatch")
    if counts:
        raise ValueError("unplanned raw source")
    return manifest, rows


class RawCapture:
    def __init__(self, root: Path, sources: list[SourceConfig], *, code_root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.lock = (root / ".writer.lock").open("a+b")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock.close()
            raise
        self.run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ-") + uuid.uuid4().hex[:8]
        self.directory = root / "runs" / self.run_id
        self.directory.mkdir(parents=True)
        self.references: dict[str, dict[str, Any]] = {}
        self.pending_cache: dict[str, dict[str, Any]] = {}
        self.write_failed = False
        self.cached_runs: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
        cache_path = root / "cache.json"
        self.cache = json.loads(cache_path.read_bytes()) if cache_path.exists() else {}
        def git(*args: str) -> str:
            return subprocess.check_output(["git", "-C", str(code_root), *args], text=True).strip()
        self.manifest: dict[str, Any] = {
            "format": "radar_raw_v1", "run_id": self.run_id, "started_at": _now(),
            "state": "started", "code_commit": git("rev-parse", "HEAD"),
            "code_dirty": bool(git("status", "--porcelain", "--untracked-files=normal")),
            "sources": {s.slug: {"configuration_sha256": configuration_digest(s),
                                 "status": "pending", "item_count": 0} for s in sources},
        }
        self._save()
        self.stream = (self.directory / "items.jsonl.gz").open("wb")
        # Independent gzip members allow every successful source to reach disk immediately.
        self.stream.write(gzip.compress(b"", mtime=0))

    def _save(self) -> None:
        _write(self.directory / "manifest.json", self.manifest)

    def prepare(self, source: SourceConfig) -> SourceConfig:
        record = self.manifest["sources"][source.slug]
        expected = validators_digest(source.meta)
        cached = self.cache.get(source.slug)
        if expected and cached and cached["validators_sha256"] == expected:
            try:
                run_id = cached["run_id"]
                if run_id not in self.cached_runs:
                    self.cached_runs[run_id] = read_run(self.root, run_id)
                manifest, _ = self.cached_runs[run_id]
                previous = manifest["sources"][source.slug]
                if (previous["status"] == "success"
                        and previous["configuration_sha256"] == record["configuration_sha256"]
                        and previous.get("validators_sha256") == expected
                        and cached["items_sha256"] == manifest["items_sha256"]):
                    self.references[source.slug] = cached
                    return source
            except (OSError, ValueError, KeyError, TypeError):
                pass
        # No proven cache representation: bootstrap on the original scheduled request.
        return replace(source, meta={k: v for k, v in source.meta.items()
                                    if k not in {"etag", "last_modified"}})

    def record(self, source: SourceConfig, items: list[FetchedItem], *,
               status: str, response_meta: dict[str, Any] | None = None,
               http_status: int | None = None) -> None:
        try:
            self._record(source, items, status=status, response_meta=response_meta, http_status=http_status)
        except Exception:
            self.write_failed = True
            raise

    def _record(self, source: SourceConfig, items: list[FetchedItem], *,
                status: str, response_meta: dict[str, Any] | None,
                http_status: int | None) -> None:
        entry = self.manifest["sources"][source.slug]
        entry.update(status=status, observed_at=_now(), http_status=http_status)
        self.pending_cache.pop(source.slug, None)
        if status == "not_modified":
            reference = self.references.get(source.slug)
            if reference is None:
                entry["status"] = "unresolved"
            else:
                entry["payload_ref"] = reference
        elif status == "success":
            payload = b"".join(_json(asdict(item)) + b"\n" for item in items)
            self.stream.write(gzip.compress(payload, mtime=0))
            self.stream.flush()
            os.fsync(self.stream.fileno())
            entry["item_count"] = len(items)
            entry["validators_sha256"] = validators_digest(response_meta or {})
            if entry["validators_sha256"]:
                self.pending_cache[source.slug] = {"run_id": self.run_id,
                                                  "validators_sha256": entry["validators_sha256"]}
        self._save()

    def finish(self) -> None:
        if self.write_failed:
            raise OSError("raw archive write failed")
        self.stream.flush()
        os.fsync(self.stream.fileno())
        self.stream.close()
        digest = _hash((self.directory / "items.jsonl.gz").read_bytes())
        self.manifest.update(state="completed", completed_at=_now(), items_sha256=digest,
                             item_count=sum(s["item_count"] for s in self.manifest["sources"].values()))
        self._save()
        for slug, entry in self.pending_cache.items():
            self.cache[slug] = {**entry, "items_sha256": digest}
        _write(self.root / "cache.json", self.cache)
        self.lock.close()

    def close(self) -> None:
        self.stream.close()
        self.lock.close()


def dependency_closure(root: Path, run_ids: set[str]) -> set[str]:
    result: set[str] = set()
    todo = list(run_ids)
    while todo:
        run_id = todo.pop()
        if run_id in result:
            continue
        manifest, _ = read_run(root, run_id)
        result.add(run_id)
        for slug, entry in manifest["sources"].items():
            reference = entry.get("payload_ref")
            if reference:
                parent, _ = read_run(root, reference["run_id"])
                source = parent["sources"][slug]
                if (parent["items_sha256"] != reference["items_sha256"]
                        or source["status"] != "success"
                        or source.get("validators_sha256") != reference["validators_sha256"]
                        or source["configuration_sha256"] != entry["configuration_sha256"]):
                    raise ValueError("invalid 304 dependency")
                todo.append(reference["run_id"])
    return result


def freeze(root: Path, destination: Path, run_ids: set[str]) -> None:
    if destination.resolve().is_relative_to((root / "runs").resolve()):
        raise ValueError("freeze destination must be outside rolling runs")
    closure = dependency_closure(root, run_ids)
    destination.mkdir(parents=True, exist_ok=False)
    for run_id in sorted(closure):
        shutil.copytree(root / "runs" / run_id, destination / "runs" / run_id)
    dependency_closure(destination, run_ids)
    _write(destination / "frozen.json", {"format": "radar_raw_frozen_v1", "roots": sorted(run_ids),
                                        "dependencies": sorted(closure)})


def retention_candidates(root: Path, *, now: datetime, days: int = 30) -> list[str]:
    if days < 1:
        raise ValueError("retention days must be positive")
    cutoff = now - timedelta(days=days)
    retained: set[str] = set()
    old: set[str] = set()
    for path in (root / "runs").glob("*/manifest.json"):
        manifest = json.loads(path.read_bytes())
        if datetime.fromisoformat(manifest["started_at"]) >= cutoff or manifest["state"] != "completed":
            retained.add(path.parent.name)
        else:
            old.add(path.parent.name)
    # Incomplete runs cannot be validated; conservatively keep all until investigated.
    if any(json.loads((root / "runs" / run / "manifest.json").read_bytes())["state"] != "completed"
           for run in retained):
        return []
    return sorted(old - dependency_closure(root, retained))


def prune(root: Path, *, now: datetime, days: int = 30) -> list[str]:
    """Explicit maintenance operation; never invoked just by constructing an archive."""
    root = root.resolve()
    with (root / ".writer.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        candidates = retention_candidates(root, now=now, days=days)
        for run_id in candidates:
            read_run(root, run_id)
            target = root / "runs" / run_id
            if target.is_symlink() or target.resolve().parent != root / "runs":
                raise ValueError("unsafe retention target")
        for run_id in candidates:
            shutil.rmtree(root / "runs" / run_id)
        return candidates


def coverage(root: Path, *, start: datetime, end: datetime, enabled_at: datetime,
             cadence_seconds: int, source_ids: set[str]) -> dict[str, Any]:
    if cadence_seconds <= 0 or not source_ids or start >= end:
        raise ValueError("provide positive cadence, nonempty sources and ordered window")
    if any(value.tzinfo is None for value in (start, end, enabled_at)):
        raise ValueError("coverage timestamps must include timezone")
    observed: list[tuple[datetime, str, dict[str, Any]]] = []
    for path in (root / "runs").glob("*/manifest.json"):
        manifest = json.loads(path.read_bytes())
        stamp = datetime.fromisoformat(manifest["started_at"])
        if start <= stamp < end:
            observed.append((stamp, path.parent.name, manifest))
    gaps: list[dict[str, str]] = []
    if start < enabled_at:
        gaps.append({"at": start.isoformat(), "reason": "before_archive_enabled"})
    slot = max(start, enabled_at)
    while slot < end:
        slot_end = min(slot + timedelta(seconds=cadence_seconds), end)
        runs = [(run_id, m) for t, run_id, m in observed if slot <= t < slot_end]
        if not runs:
            gaps.append({"at": slot.isoformat(), "reason": "missing_run"})
        for run_id, manifest in runs:
            try:
                dependency_closure(root, {run_id})
                if not source_ids.issubset(manifest["sources"]):
                    raise ValueError("missing_source")
                if any(manifest["sources"][s]["status"] not in {"success", "not_modified"} for s in source_ids):
                    raise ValueError("failed_or_unresolved_source")
            except (OSError, ValueError, KeyError, TypeError) as error:
                gaps.append({"at": slot.isoformat(), "reason": type(error).__name__, "run_id": run_id})
        slot = slot_end
    return {"complete": not gaps, "observed_runs": len(observed), "gaps": gaps}
