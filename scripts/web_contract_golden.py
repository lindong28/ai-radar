"""Reusable capture and verification mechanics for web contract golden assets."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import struct
import sys
import tempfile
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import Request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from airadar.egress import open_external_url  # noqa: E402


def urlopen(request: Request, timeout: float):  # noqa: ANN201
    return open_external_url(
        request,
        callsite_id="scripts.web_contract_golden",
        timeout=timeout,
    )

PRELOAD_RE = re.compile(rb'<script id="__PRELOAD__"[^>]*>(.*?)</script>', re.S)
SUPPORTED_KINDS = frozenset({"api-json", "ssr-preload-json", "html"})


@dataclass(frozen=True)
class HttpSpec:
    artifact: str
    path: str
    kind: str
    expected_status: int = 200
    expected_content_type: str | None = None


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _validate_specs(specs: Sequence[HttpSpec]) -> None:
    if not specs:
        raise ValueError("at least one HTTP spec is required")
    artifacts = [spec.artifact for spec in specs]
    if len(artifacts) != len(set(artifacts)):
        raise ValueError("duplicate artifact in HTTP specs")
    for spec in specs:
        artifact = Path(spec.artifact)
        if (
            not spec.artifact
            or artifact.is_absolute()
            or artifact.name != spec.artifact
            or "/" in spec.artifact
            or "\\" in spec.artifact
            or spec.artifact in {".", ".."}
        ):
            raise ValueError(f"{spec.artifact!r}: artifact must be a safe relative filename")
        if spec.kind not in SUPPORTED_KINDS:
            raise ValueError(f"{spec.artifact}: unsupported kind {spec.kind}")


def _json_bytes(value: object, *, api: bool) -> bytes:
    if api:
        return (json.dumps(value, indent=4, ensure_ascii=True) + "\n").encode()
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True).encode()


def _fetch(base_url: str, spec: HttpSpec) -> tuple[HttpSpec, bytes, dict[str, object]]:
    url = f"{base_url.rstrip('/')}{spec.path}"
    request = Request(url, headers={"Accept": "application/json,text/html"})
    with urlopen(request, timeout=180) as response:  # noqa: S310 - caller supplies a local verification URL
        raw = response.read()
        status = response.status
        content_type = response.headers.get("Content-Type", "")
        final_url = response.geturl()
    _require(final_url == url, f"{spec.artifact}: request redirected from {url} to {final_url}")
    _require(status == spec.expected_status, f"{spec.artifact}: status mismatch")
    _require(content_type == _expected_content_type(spec), f"{spec.artifact}: content_type mismatch")
    if spec.kind == "api-json":
        artifact = _json_bytes(json.loads(raw), api=True)
    elif spec.kind == "ssr-preload-json":
        match = PRELOAD_RE.search(raw)
        if match is None:
            raise AssertionError(f"missing __PRELOAD__ in {url}")
        artifact = _json_bytes(json.loads(match.group(1)), api=False)
    else:
        artifact = raw
    metadata: dict[str, object] = {
        "artifact": spec.artifact,
        "kind": spec.kind,
        "url": url,
        "status": status,
        "content_type": content_type,
        "bytes": len(artifact),
        "sha256": hashlib.sha256(artifact).hexdigest(),
    }
    return spec, artifact, metadata


def _atomic_write(path: Path, content: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _publish_capture(files: Sequence[tuple[Path, bytes]]) -> None:
    backups: dict[Path, bytes | None] = {}
    for path, _content in files:
        if path.is_symlink():
            raise ValueError(f"refusing to replace symbolic link: {path}")
        backups[path] = path.read_bytes() if path.exists() else None
    try:
        for path, content in files:
            _atomic_write(path, content)
    except Exception as error:
        try:
            for path, content in backups.items():
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    _atomic_write(path, content)
        except Exception as rollback_error:
            raise RuntimeError("capture publish failed and rollback was incomplete") from rollback_error
        raise error


def capture(base_url: str, output: Path, concurrency: int, *, specs: Sequence[HttpSpec]) -> None:
    """Capture and canonicalize the responses described by ``specs``."""
    _validate_specs(specs)
    if concurrency <= 0:
        raise ValueError("concurrency must be positive")
    if output.is_symlink():
        raise ValueError(f"refusing to capture into symbolic link: {output}")
    output.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        results = list(executor.map(lambda spec: _fetch(base_url, spec), specs))
    entries: list[dict[str, object]] = []
    files: list[tuple[Path, bytes]] = []
    for spec, artifact, metadata in results:
        files.append((output / spec.artifact, artifact))
        entries.append(metadata)
    manifest = {"version": 1, "artifacts": sorted(entries, key=lambda entry: str(entry["artifact"]))}
    files.append(
        (
            output / "request-manifest.json",
            (json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode(),
        )
    )
    _publish_capture(files)
    counts = {kind: sum(spec.kind == kind for spec in specs) for kind in SUPPORTED_KINDS}
    print(
        f"CAPTURE PASS http={len(results)} api={counts['api-json']} "
        f"ssr={counts['ssr-preload-json']} html={counts['html']}"
    )


def _put(hasher: Any, value: object) -> None:
    if value is None:
        tag, raw = b"n", b""
    elif isinstance(value, bytes):
        tag, raw = b"b", value
    elif isinstance(value, str):
        tag, raw = b"s", value.encode()
    elif isinstance(value, int):
        tag, raw = b"i", str(value).encode()
    elif isinstance(value, float):
        tag, raw = b"f", struct.pack(">d", value)
    else:
        raise TypeError(f"unsupported SQLite value: {type(value)!r}")
    hasher.update(tag)
    hasher.update(len(raw).to_bytes(8, "big"))
    hasher.update(raw)


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _rows(conn: sqlite3.Connection, table: str, columns: list[str]) -> Iterable[tuple[object, ...]]:
    table_sql = _quote_identifier(table)
    column_sql = ", ".join(_quote_identifier(column) for column in columns)
    try:
        conn.execute(f"SELECT rowid FROM {table_sql} LIMIT 0")
    except sqlite3.OperationalError:
        info = conn.execute(f"PRAGMA table_xinfo({table_sql})").fetchall()
        primary_key = [row[1] for row in sorted(info, key=lambda row: row[5]) if row[5]]
        order = primary_key or columns
        order_sql = ", ".join(_quote_identifier(column) for column in order)
        query = f"SELECT {column_sql} FROM {table_sql} ORDER BY {order_sql}"
    else:
        query = f"SELECT rowid, {column_sql} FROM {table_sql} ORDER BY rowid"
    yield from conn.execute(query)


def logical_db_invariant(db_path: Path) -> dict[str, object]:
    """Digest a SQLite database by logical schema and ordered row content."""
    wal = Path(f"{db_path}-wal")
    if wal.exists() and wal.stat().st_size:
        raise RuntimeError(f"refusing to digest database with WAL present: {wal}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        schema_rows = conn.execute(
            "SELECT type, name, tbl_name, COALESCE(sql, '') FROM sqlite_schema "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
        schema_hasher = hashlib.sha256()
        for row in schema_rows:
            for value in row:
                _put(schema_hasher, value)
        table_names = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        tables: dict[str, object] = {}
        for table in table_names:
            info = conn.execute(f"PRAGMA table_xinfo({_quote_identifier(table)})").fetchall()
            columns = [row[1] for row in info if row[6] == 0]
            hasher = hashlib.sha256()
            row_count = 0
            for row in _rows(conn, table, columns):
                for value in row:
                    _put(hasher, value)
                row_count += 1
            tables[table] = {"columns": columns, "rows": row_count, "sha256": hasher.hexdigest()}
    finally:
        conn.close()
    invariant: dict[str, object] = {
        "algorithm": "sha256-length-prefixed-sqlite-logical-v1",
        "schema_sha256": schema_hasher.hexdigest(),
        "tables": tables,
    }
    canonical = json.dumps(invariant, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    invariant["overall_sha256"] = hashlib.sha256(canonical).hexdigest()
    return invariant


def record_db(db_path: Path, output: Path) -> None:
    """Record the logical SQLite invariant used to anchor a golden dataset."""
    invariant = logical_db_invariant(db_path)
    output.write_text(json.dumps(invariant, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    tables = invariant["tables"]
    assert isinstance(tables, dict)
    print(f"DB RECORD PASS tables={len(tables)} digest={invariant['overall_sha256']}")


def _expected_content_type(spec: HttpSpec) -> str:
    if spec.expected_content_type is not None:
        return spec.expected_content_type
    return "application/json" if spec.kind == "api-json" else "text/html; charset=utf-8"


def _manifest_by_artifact(
    directory: Path,
    specs: Sequence[HttpSpec],
) -> dict[str, dict[str, object]]:
    manifest = json.loads((directory / "request-manifest.json").read_text())
    _require(manifest["version"] == 1, "manifest version mismatch")
    artifacts = manifest["artifacts"]
    artifact_names = [entry["artifact"] for entry in artifacts]
    _require(len(artifact_names) == len(set(artifact_names)), "duplicate artifact in manifest")
    entries = {entry["artifact"]: entry for entry in artifacts}
    expected_artifacts = {spec.artifact for spec in specs}
    _require(set(entries) == expected_artifacts, "manifest artifact set mismatch")
    for spec in specs:
        entry = entries[spec.artifact]
        raw = (directory / spec.artifact).read_bytes()
        _require(entry["kind"] == spec.kind, f"{spec.artifact}: kind mismatch")
        _require(entry["status"] == spec.expected_status, f"{spec.artifact}: status mismatch")
        _require(
            entry["content_type"] == _expected_content_type(spec),
            f"{spec.artifact}: content_type mismatch",
        )
        _require(entry["bytes"] == len(raw), f"{spec.artifact}: bytes mismatch")
        _require(
            entry["sha256"] == hashlib.sha256(raw).hexdigest(),
            f"{spec.artifact}: sha256 mismatch",
        )
        parsed_url = urlsplit(str(entry["url"]))
        request_target = parsed_url.path + (f"?{parsed_url.query}" if parsed_url.query else "")
        _require(
            bool(parsed_url.scheme and parsed_url.netloc and request_target == spec.path),
            f"{spec.artifact}: url mismatch",
        )
    return entries


def _json_equal(expected: object, observed: object) -> bool:
    if type(expected) is not type(observed):
        return False
    if isinstance(expected, dict):
        if not isinstance(observed, dict) or expected.keys() != observed.keys():
            return False
        return all(_json_equal(value, observed[key]) for key, value in expected.items())
    if isinstance(expected, list):
        if not isinstance(observed, list) or len(expected) != len(observed):
            return False
        return all(_json_equal(left, right) for left, right in zip(expected, observed, strict=True))
    return expected == observed


def verify(
    db_path: Path,
    golden: Path,
    actual: Path | None,
    *,
    specs: Sequence[HttpSpec],
    validate_nonempty: Callable[[Path], None] | None = None,
) -> None:
    """Verify a golden dataset and optionally compare a repeated capture."""
    _validate_specs(specs)
    expected_invariant = json.loads((golden / "db-invariants.json").read_text())
    current_invariant = logical_db_invariant(db_path)
    _require(current_invariant == expected_invariant, "logical DB digest mismatch; re-baseline required")
    golden_manifest = _manifest_by_artifact(golden, specs)
    if validate_nonempty is not None:
        validate_nonempty(golden)
    if actual is not None:
        actual_manifest = _manifest_by_artifact(actual, specs)
        if validate_nonempty is not None:
            validate_nonempty(actual)
        for spec in specs:
            expected_meta = golden_manifest[spec.artifact]
            actual_meta = actual_manifest[spec.artifact]
            for field in ("artifact", "kind", "status", "content_type"):
                _require(
                    actual_meta[field] == expected_meta[field],
                    f"{spec.artifact}: {field} mismatch",
                )
            expected = (golden / spec.artifact).read_bytes()
            observed = (actual / spec.artifact).read_bytes()
            if spec.kind == "html":
                _require(observed == expected, f"{spec.artifact}: HTML byte mismatch")
            else:
                _require(
                    _json_equal(json.loads(expected), json.loads(observed)),
                    f"{spec.artifact}: JSON semantic mismatch",
                )
        json_count = sum(spec.kind != "html" for spec in specs)
        html_count = sum(spec.kind == "html" for spec in specs)
        print(f"REPEAT PASS json_semantic={json_count} html_byte={html_count} metadata={len(specs)} whitelist=0")
    tables = current_invariant["tables"]
    assert isinstance(tables, dict)
    print(f"VERIFY PASS db_tables={len(tables)} db_digest={current_invariant['overall_sha256']} http={len(specs)}")
