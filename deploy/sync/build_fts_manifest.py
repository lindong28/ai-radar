#!/usr/bin/env python3
"""Build the snapshot-bound FTS oracle transferred beside a base-only DB.

Sidecar and publish contract (consumed by ``apply_db_update.py`` in U3):

* ``snapshot_id`` is exactly the lowercase 64-hex SHA-256 of the immutable
  base-only database artifact. It is the only identity representation allowed
  in the manifest, remote filename, apply journal, receipt, recovery, and basis
  flow; the consumer must not retain the former 16-hex abbreviation.
* The manifest is canonical UTF-8 JSON (sorted keys, compact separators).
  ``manifest_sha256`` is the lowercase SHA-256 of that canonical object with
  the ``manifest_sha256`` member omitted. The on-disk JSON may end in one LF;
  that LF is not part of the self-hash.
* The remote immutable sidecar name is
  ``radar.db.fts-manifest.<snapshot_id>.json``. The producer uploads a temporary
  ``.upload`` name and publishes the content-addressed final name without
  replacing it: an identical existing sidecar is an idempotent retry, while
  different content under the same artifact identity aborts the round. Only
  then does it atomically rename ``radar.db.upload`` to ``radar.db.incoming``;
  the DB rename remains the existing commit marker.
* The consumer must first claim the DB, compute its full SHA-256, select only
  the exact hash-keyed sidecar, recompute the manifest self-hash, and require
  both identities to match before FTS rebuild or traffic switch. Missing,
  malformed, or mismatched sidecars reject/quarantine the snapshot. A fixed
  ``manifest.incoming`` path is forbidden because the apply timer could observe
  it paired with an older DB between two renames.
* Sidecar retention/cleanup belongs to the U3 committed/quarantine lifecycle;
  U2 deliberately does not delete remote oracle evidence.

FTS full-table digest v1 maps NULL to the empty string and converts other values
to text without Unicode or newline rewriting. Each raw UTF-8 value is
length-framed. A row hash is SHA-256 over the six framed values; the table
digest is SHA-256 over a domain tag, the 64-bit row count, and the sorted
multiset of row hashes. This is independent of SQLite rowid/layout while
retaining duplicates.

Every field probe is selected from actual ``items_fts`` data. Its manifest v2
evidence keeps raw FTS5 ``MATCH`` results for the target field, every other
search field, and the unqualified table query. A probe is accepted only when
the target result is non-empty, every other field result is empty, and the
unqualified result set exactly equals the target result set. These raw results
remain the SQLite-level oracle.

``timeline_http_matches`` is a separate, required expectation for the application's
``/api/v1/timeline`` search path. It is produced with the app-owned search, source
visibility, deduplication, prefilter, and scoring predicates rather than a copied SQL
predicate. Every one of those five is imported from the route module; none may be
restated here. Source visibility was once absent rather than copied, which reads as
compliance with this rule and is not: an omitted predicate has no stale copy to
notice. A raw-exclusive term is accepted for HTTP use only when at least one
of its raw target matches survives those visibility rules. Failure to find a
raw-exclusive, HTTP-visible term for any field aborts manifest creation.

Rollout is consumer-first: the v2 apply verifier must be installed before a v2
producer publishes a sidecar. A v2 consumer rejects historical v1 sidecars, and
an older consumer rejects v2, so an incorrect rollout order fails closed rather
than mixing probe semantics.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from airadar.web.routes.categories import deduped_item_clause  # noqa: E402
from airadar.web.routes.search import search_id_subquery  # noqa: E402
from airadar.web.routes.timeline import (  # noqa: E402
    _PREFILTER_SCORING_CLAUSE,
    TIMELINE_SOURCE_VISIBILITY_CLAUSES,
)

FORMAT_VERSION = 2
FTS_FIELDS = ("item_id", "title", "content_text", "source_name", "author", "title_zh")
SEARCH_FIELDS = FTS_FIELDS[1:]
NORMALIZATION = (
    "digest-v1: NULL->empty text; other SQLite values->text; raw UTF-8 codepoint "
    "sequence without Unicode or newline rewriting; length framing; sorted multiset "
    "of per-row SHA-256 hashes"
)
SIDECAR_PREFIX = "radar.db.fts-manifest."
SIDECAR_SUFFIX = ".json"
FTS_TABLE = "items_fts"
FTS_SHADOW_TABLES = {
    "items_fts_config",
    "items_fts_content",
    "items_fts_data",
    "items_fts_docsize",
    "items_fts_idx",
}


class ManifestError(RuntimeError):
    """The baseline oracle cannot be generated or validated."""


def _resolved(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _same_inode(left: Path, right: Path) -> bool:
    return left.exists() and right.exists() and os.path.samefile(left, right)


def _assert_distinct(snapshot: Path, artifact: Path, output: Path) -> None:
    paths = [_resolved(snapshot), _resolved(artifact), _resolved(output)]
    labels = ("snapshot", "artifact", "manifest output")
    for index, left in enumerate(paths):
        for other_index, right in enumerate(paths[index + 1 :], start=index + 1):
            if left == right:
                raise ManifestError(f"{labels[index]} and {labels[other_index]} share path")
            if _same_inode(left, right):
                raise ManifestError(f"{labels[index]} and {labels[other_index]} share inode")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _raw_text(value: Any) -> str:
    return "" if value is None else str(value)


def _sql_tokens(sql: str) -> list[tuple[str, bool]]:
    """Tokenize trigger SQL while discarding literals and comments."""

    tokens: list[tuple[str, bool]] = []
    index = 0
    while index < len(sql):
        character = sql[index]
        if character.isspace():
            index += 1
            continue
        if sql.startswith("--", index):
            newline = sql.find("\n", index + 2)
            index = len(sql) if newline == -1 else newline + 1
            continue
        if sql.startswith("/*", index):
            closing = sql.find("*/", index + 2)
            index = len(sql) if closing == -1 else closing + 2
            continue
        if character == "'":
            index += 1
            while index < len(sql):
                if sql[index] == "'":
                    if index + 1 < len(sql) and sql[index + 1] == "'":
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            continue
        if character in {'"', "`"}:
            delimiter = character
            index += 1
            value: list[str] = []
            while index < len(sql):
                if sql[index] == delimiter:
                    if index + 1 < len(sql) and sql[index + 1] == delimiter:
                        value.append(delimiter)
                        index += 2
                        continue
                    index += 1
                    break
                value.append(sql[index])
                index += 1
            tokens.append(("".join(value), True))
            continue
        if character == "[":
            closing = sql.find("]", index + 1)
            if closing == -1:
                tokens.append((sql[index + 1 :], True))
                break
            tokens.append((sql[index + 1 : closing], True))
            index = closing + 1
            continue
        if character.isalnum() or character in {"_", "$"}:
            end = index + 1
            while end < len(sql) and (sql[end].isalnum() or sql[end] in {"_", "$"}):
                end += 1
            tokens.append((sql[index:end], False))
            index = end
            continue
        tokens.append((character, False))
        index += 1
    return tokens


def _trigger_mutates_items_fts(sql: str | None) -> bool:
    if sql is None:
        return False

    tokens = _sql_tokens(sql)
    folded = [value.casefold() for value, _ in tokens]
    conflict_actions = {"rollback", "abort", "replace", "fail", "ignore"}
    for index, token in enumerate(folded):
        target_index: int | None = None
        if not tokens[index][1] and token == "insert":
            target_index = index + 1
            if (
                target_index + 1 < len(folded)
                and not tokens[target_index][1]
                and not tokens[target_index + 1][1]
                and folded[target_index] == "or"
                and folded[target_index + 1] in conflict_actions
            ):
                target_index += 2
            if (
                target_index < len(folded)
                and not tokens[target_index][1]
                and folded[target_index] == "into"
            ):
                target_index += 1
        elif not tokens[index][1] and token == "replace":
            target_index = index + 1
            if (
                target_index < len(folded)
                and not tokens[target_index][1]
                and folded[target_index] == "into"
            ):
                target_index += 1
        elif not tokens[index][1] and token == "update":
            target_index = index + 1
            if (
                target_index + 1 < len(folded)
                and not tokens[target_index][1]
                and not tokens[target_index + 1][1]
                and folded[target_index] == "or"
                and folded[target_index + 1] in conflict_actions
            ):
                target_index += 2
        elif (
            not tokens[index][1]
            and token == "delete"
            and index + 1 < len(folded)
            and not tokens[index + 1][1]
            and folded[index + 1] == "from"
        ):
            target_index = index + 2

        if target_index is None or target_index >= len(folded):
            continue
        if target_index + 2 < len(folded) and folded[target_index + 1] == ".":
            target_index += 2
        if folded[target_index] == FTS_TABLE.casefold():
            return True
    return False


def _frame(value: str) -> bytes:
    payload = value.encode("utf-8")
    return len(payload).to_bytes(8, "big") + payload


def _row_hash(row: tuple[str, ...]) -> bytes:
    digest = hashlib.sha256(b"ai-radar-items-fts-row-v1\0")
    for value in row:
        digest.update(_frame(value))
    return digest.digest()


def _table_digest(rows: list[tuple[str, ...]]) -> str:
    digest = hashlib.sha256(b"ai-radar-items-fts-table-v1\0")
    digest.update(len(rows).to_bytes(8, "big"))
    for row_hash in sorted(_row_hash(row) for row in rows):
        digest.update(row_hash)
    return digest.hexdigest()


def canonical_manifest_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def manifest_self_hash(payload: dict[str, Any]) -> str:
    body = dict(payload)
    body.pop("manifest_sha256", None)
    return hashlib.sha256(canonical_manifest_bytes(body)).hexdigest()


def _validate_result_set(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ManifestError(f"manifest {label} is not an object")
    count = value.get("count")
    item_ids = value.get("item_ids")
    if (
        type(count) is not int
        or count < 0
        or not isinstance(item_ids, list)
        or any(not isinstance(item_id, str) for item_id in item_ids)
        or item_ids != sorted(set(item_ids))
        or count != len(item_ids)
    ):
        raise ManifestError(f"manifest {label} is invalid")
    return {"count": count, "item_ids": item_ids}


def validate_manifest(payload: dict[str, Any]) -> None:
    if payload.get("format_version") != FORMAT_VERSION:
        raise ManifestError("unsupported manifest format_version")
    snapshot_id = payload.get("snapshot_id")
    if not isinstance(snapshot_id, str) or re.fullmatch(r"[0-9a-f]{64}", snapshot_id) is None:
        raise ManifestError("snapshot_id must be a lowercase full SHA-256")
    recorded = payload.get("manifest_sha256")
    if not isinstance(recorded, str) or recorded != manifest_self_hash(payload):
        raise ManifestError("manifest self-hash mismatch")
    fts = payload.get("fts")
    if not isinstance(fts, dict) or fts.get("fields") != list(FTS_FIELDS):
        raise ManifestError("manifest FTS field contract mismatch")
    probes = payload.get("probes")
    if not isinstance(probes, dict) or set(probes) != set(SEARCH_FIELDS):
        raise ManifestError("manifest probe field contract mismatch")
    for field in SEARCH_FIELDS:
        probe = probes[field]
        if not isinstance(probe, dict) or probe.get("field") != field:
            raise ManifestError(f"manifest probe {field} is malformed")
        term = probe.get("term")
        if not isinstance(term, str) or not term:
            raise ManifestError(f"manifest probe {field}.term is invalid")
        if probe.get("query") != _match_expression(field, term):
            raise ManifestError(f"manifest probe {field}.query is inconsistent")
        if probe.get("unqualified_query") != _match_expression(None, term):
            raise ManifestError(
                f"manifest probe {field}.unqualified_query is inconsistent"
            )
        matches = _validate_result_set(probe.get("matches"), f"probe {field}.matches")
        if matches["count"] == 0:
            raise ManifestError(f"manifest probe {field}.matches is empty")
        unqualified = _validate_result_set(
            probe.get("unqualified_matches"),
            f"probe {field}.unqualified_matches",
        )
        if unqualified != matches:
            raise ManifestError(
                f"manifest probe {field}.unqualified_matches differs from target"
            )
        field_matches = probe.get("field_matches")
        if not isinstance(field_matches, dict) or set(field_matches) != set(
            SEARCH_FIELDS
        ):
            raise ManifestError(f"manifest probe {field}.field_matches is malformed")
        for candidate_field in SEARCH_FIELDS:
            candidate_matches = _validate_result_set(
                field_matches[candidate_field],
                f"probe {field}.field_matches.{candidate_field}",
            )
            expected = matches if candidate_field == field else {
                "count": 0,
                "item_ids": [],
            }
            if candidate_matches != expected:
                raise ManifestError(
                    f"manifest probe {field}.field_matches is not exclusive"
                )
        if probe.get("exclusive") is not True:
            raise ManifestError(f"manifest probe {field}.exclusive is invalid")
        timeline_matches = _validate_result_set(
            probe.get("timeline_http_matches"),
            f"probe {field}.timeline_http_matches",
        )
        if timeline_matches["count"] == 0:
            raise ManifestError(
                f"manifest probe {field}.timeline_http_matches is empty"
            )
        if not set(timeline_matches["item_ids"]).issubset(matches["item_ids"]):
            raise ManifestError(
                f"manifest probe {field}.timeline_http_matches exceeds raw target"
            )


def sidecar_name(snapshot_id: str) -> str:
    if re.fullmatch(r"[0-9a-f]{64}", snapshot_id) is None:
        raise ManifestError("sidecar name requires a lowercase full SHA-256")
    return f"{SIDECAR_PREFIX}{snapshot_id}{SIDECAR_SUFFIX}"


def _quote_fts_phrase(term: str) -> str:
    return '"' + term.replace('"', '""') + '"'


def _match_expression(field: str | None, term: str) -> str:
    phrase = _quote_fts_phrase(term)
    return phrase if field is None else f"{field} : {phrase}"


def _match_ids(
    connection: sqlite3.Connection, field: str | None, term: str
) -> dict[str, Any]:
    expression = _match_expression(field, term)
    rows = connection.execute(
        "SELECT item_id FROM items_fts WHERE items_fts MATCH ?", (expression,)
    ).fetchall()
    item_ids = sorted({_raw_text(row[0]) for row in rows})
    return {"count": len(item_ids), "item_ids": item_ids}


def _timeline_http_match_ids(
    connection: sqlite3.Connection,
    term: str,
) -> dict[str, Any]:
    search_subquery, search_params = search_id_subquery(term)
    if search_subquery is None:
        raise ManifestError("application search rejected a non-empty probe term")
    search_rows = connection.execute(search_subquery, search_params).fetchall()
    search_ids = sorted({_raw_text(row[0]) for row in search_rows})
    if not search_ids:
        return {"count": 0, "item_ids": []}
    visibility = [*TIMELINE_SOURCE_VISIBILITY_CLAUSES, deduped_item_clause("i")]
    has_prefilter = connection.execute(
        "SELECT 1 FROM item_evaluations WHERE stage='prefilter' LIMIT 1"
    ).fetchone()
    if has_prefilter is not None:
        visibility.append(_PREFILTER_SCORING_CLAUSE)
    placeholders = ", ".join("?" for _ in search_ids)
    rows = connection.execute(
        f"""
        SELECT i.id
        FROM items i
        JOIN sources s ON s.id=i.source_id
        WHERE i.id IN ({placeholders})
          AND {' AND '.join(f'({clause})' for clause in visibility)}
        """,
        search_ids,
    ).fetchall()
    item_ids = sorted({_raw_text(row[0]) for row in rows})
    return {"count": len(item_ids), "item_ids": item_ids}


def _candidate_terms(value: str) -> Iterable[str]:
    """Yield deterministic, FTS-safe phrases from one actual field value."""

    token_matches = list(re.finditer(r"\S+", value))
    emitted: set[str] = set()

    def emit(term: str) -> Iterable[str]:
        if len(term) >= 3 and term not in emitted:
            emitted.add(term)
            yield term

    for match in token_matches:
        token = match.group(0)
        if len(token) <= 48:
            yield from emit(token)
        else:
            for width in (48, 32, 24, 16, 12, 8, 4, 3):
                if len(token) < width:
                    continue
                for start in range(0, len(token) - width + 1, max(1, width // 2)):
                    yield from emit(token[start : start + width])
    for width in (4, 3, 2):
        for start in range(0, max(0, len(token_matches) - width + 1)):
            yield from emit(
                value[token_matches[start].start() : token_matches[start + width - 1].end()]
            )


def _find_probe(
    connection: sqlite3.Connection,
    rows: list[tuple[str, ...]],
    field: str,
) -> dict[str, Any]:
    field_index = FTS_FIELDS.index(field)
    seen: set[str] = set()
    for row in rows:
        for term in _candidate_terms(row[field_index]):
            if term in seen:
                continue
            seen.add(term)
            field_matches = {
                candidate_field: _match_ids(connection, candidate_field, term)
                for candidate_field in SEARCH_FIELDS
            }
            target = field_matches[field]
            if target["count"] == 0:
                continue
            if any(
                field_matches[other]["count"] != 0
                for other in SEARCH_FIELDS
                if other != field
            ):
                continue
            unqualified = _match_ids(connection, None, term)
            if unqualified != target:
                continue
            timeline_http_matches = _timeline_http_match_ids(
                connection,
                term,
            )
            if not timeline_http_matches["item_ids"] or not set(
                timeline_http_matches["item_ids"]
            ).issubset(target["item_ids"]):
                continue
            expression = _match_expression(field, term)
            return {
                "field": field,
                "term": term,
                "query": expression,
                "matches": target,
                "unqualified_query": _match_expression(None, term),
                "unqualified_matches": unqualified,
                "timeline_http_matches": timeline_http_matches,
                "field_matches": field_matches,
                "exclusive": True,
                "evidence": {
                    "candidate_source_item_id": row[0],
                    "source": f"actual items_fts.{field} value in the bound pre-strip snapshot",
                    "unqualified_equals_target": True,
                    "other_fields_empty": [other for other in SEARCH_FIELDS if other != field],
                },
            }
    raise ManifestError(
        f"no visible exclusive probe term found for field {field}"
    )


def _read_fts_rows(snapshot: Path) -> tuple[sqlite3.Connection, list[tuple[str, ...]]]:
    connection = sqlite3.connect(
        f"file:{_resolved(snapshot)}?mode=ro&immutable=1", uri=True
    )
    try:
        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        if quick_check != "ok":
            raise ManifestError(f"snapshot failed quick_check: {quick_check}")
        columns = [
            row[1]
            for row in connection.execute("PRAGMA table_xinfo('items_fts')").fetchall()
            if row[6] == 0
        ]
        if columns != list(FTS_FIELDS):
            raise ManifestError(
                f"items_fts fields differ: expected={list(FTS_FIELDS)!r} actual={columns!r}"
            )
        selected = ", ".join(f'"{field}"' for field in FTS_FIELDS)
        raw_rows = connection.execute(f"SELECT {selected} FROM items_fts").fetchall()
        rows = [tuple(_raw_text(value) for value in row) for row in raw_rows]
        rows.sort()
        return connection, rows
    except BaseException:
        connection.close()
        raise


def _validate_base_artifact(artifact: Path) -> None:
    connection = sqlite3.connect(
        f"file:{_resolved(artifact)}?mode=ro&immutable=1", uri=True
    )
    try:
        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        if quick_check != "ok":
            raise ManifestError(f"base-only artifact failed quick_check: {quick_check}")
        objects = connection.execute(
            "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
        ).fetchall()
        fts_objects: list[tuple[str, str]] = []
        for object_type, name, sql in objects:
            if object_type == "table" and name == "items_fts" and sql is not None:
                if re.match(
                    r"^\s*CREATE\s+VIRTUAL\s+TABLE\b.*\bUSING\s+fts5\s*\(",
                    sql,
                    flags=re.IGNORECASE | re.DOTALL,
                ):
                    fts_objects.append((object_type, name))
            elif object_type == "table" and name in FTS_SHADOW_TABLES:
                fts_objects.append((object_type, name))
            elif object_type == "trigger" and _trigger_mutates_items_fts(sql):
                fts_objects.append((object_type, name))
        if fts_objects:
            raise ManifestError(
                f"transfer artifact is not base-only: FTS-owned objects={fts_objects!r}"
            )
    finally:
        connection.close()


def _atomic_write(output: Path, content: bytes) -> None:
    output = _resolved(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
        directory = os.open(output.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


def build_manifest(*, snapshot: Path, artifact: Path, output: Path) -> dict[str, Any]:
    _assert_distinct(snapshot, artifact, output)
    if not snapshot.is_file():
        raise ManifestError(f"snapshot not found: {snapshot}")
    if not artifact.is_file():
        raise ManifestError(f"base-only artifact not found: {artifact}")
    _validate_base_artifact(artifact)

    connection, rows = _read_fts_rows(snapshot)
    try:
        if not rows:
            raise ManifestError("items_fts is empty; cannot build search probes")
        probes = {
            field: _find_probe(connection, rows, field)
            for field in SEARCH_FIELDS
        }
    finally:
        connection.close()

    payload: dict[str, Any] = {
        "format_version": FORMAT_VERSION,
        "snapshot_id": sha256_file(artifact),
        "fts": {
            "table": "items_fts",
            "fields": list(FTS_FIELDS),
            "row_count": len(rows),
            "sha256": _table_digest(rows),
            "normalization": NORMALIZATION,
        },
        "probes": probes,
    }
    payload["manifest_sha256"] = manifest_self_hash(payload)
    validate_manifest(payload)
    _atomic_write(output, canonical_manifest_bytes(payload) + b"\n")
    return payload


def _parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        payload = build_manifest(
            snapshot=args.snapshot,
            artifact=args.artifact,
            output=args.output,
        )
        print(f"[manifest] snapshot_id={payload['snapshot_id']}")
        print(f"[manifest] manifest_sha256={payload['manifest_sha256']}")
        print(
            f"[manifest] items_fts rows={payload['fts']['row_count']} "
            f"sha256={payload['fts']['sha256']}"
        )
        for field in SEARCH_FIELDS:
            probe = payload["probes"][field]
            print(
                f"[manifest] field={field} term={probe['term']!r} "
                f"matches={probe['matches']['count']} exclusive=1"
            )
        print(f"[manifest] sidecar={sidecar_name(payload['snapshot_id'])}")
        return 0
    except ManifestError as exc:
        print(f"[manifest] ✗ {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
