#!/usr/bin/env python3
"""Maintain the persistent base-only database shipped to the replica server.

The source snapshot may contain FTS5, but the shipping replica never does.
The first round dynamically copies every non-FTS schema object and table. Later
rounds derive a primary-key delta from the immutable snapshot, apply it in
place, and reconcile every non-FTS table by row count and a typed full-row
SHA-256 digest. Any invalid replica or reconciliation mismatch is replaced by
a freshly built base-only copy and reported as self-heal/bootstrap semantics.

This module also owns the producer's live-database safety guard: the live DB
must differ by resolved path and, when both files exist, by inode from every
artifact that the producer can write. Snapshot, replica, and manifest outputs
must likewise be distinct from one another.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sqlite3
import struct
import sys
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any, NamedTuple

FTS_TABLE = "items_fts"
FTS_SHADOW_TABLES = {
    "items_fts_config",
    "items_fts_content",
    "items_fts_data",
    "items_fts_docsize",
    "items_fts_idx",
}
SUPPORTED_OBJECT_TYPES = {"table", "index", "trigger", "view"}
PERSISTENT_PRAGMAS = ("page_size", "auto_vacuum", "encoding", "user_version", "application_id")


class ReplicaError(RuntimeError):
    """Base error for a refused or failed replica operation."""


class SafetyError(ReplicaError):
    """A source and writable artifact alias each other."""


class ReplicaInvalid(ReplicaError):
    """The persistent replica cannot safely receive a logical delta."""


class ReconciliationError(ReplicaError):
    """The post-apply replica differs from its source snapshot."""


class SyncResult(NamedTuple):
    mode: str
    operations: int
    reason: str


class TableDigest(NamedTuple):
    table: str
    rows: int
    sha256: str


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _resolved(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _same_inode(left: Path, right: Path) -> bool:
    return left.exists() and right.exists() and os.path.samefile(left, right)


def assert_safe_artifacts(
    *,
    live_db: Path,
    snapshot: Path,
    replica: Path,
    manifest: Path | None = None,
) -> None:
    """Refuse path or inode aliasing before any producer artifact is written."""

    live_db = _resolved(live_db)
    written = [_resolved(snapshot), _resolved(replica)]
    if manifest is not None:
        written.append(_resolved(manifest))

    for artifact in written:
        if live_db == artifact:
            raise SafetyError(f"live database and written artifact share path: {live_db}")
        if _same_inode(live_db, artifact):
            raise SafetyError(
                f"live database and written artifact share inode: {live_db} and {artifact}"
            )

    for index, left in enumerate(written):
        for right in written[index + 1 :]:
            if left == right:
                raise SafetyError(f"written artifacts share path: {left}")
            if _same_inode(left, right):
                raise SafetyError(f"written artifacts share inode: {left} and {right}")


def _schema_rows(connection: sqlite3.Connection, schema: str) -> list[tuple[Any, ...]]:
    return connection.execute(
        f"SELECT type, name, tbl_name, sql FROM {quote_identifier(schema)}.sqlite_master "
        "WHERE name NOT LIKE 'sqlite_autoindex_%' ORDER BY type, name"
    ).fetchall()


def _is_items_fts_virtual_table(
    object_type: str, name: str, sql: str | None
) -> bool:
    return (
        object_type == "table"
        and name == FTS_TABLE
        and sql is not None
        and re.match(
            r"^\s*CREATE\s+VIRTUAL\s+TABLE\b.*\bUSING\s+fts5\s*\(",
            sql,
            flags=re.IGNORECASE | re.DOTALL,
        )
        is not None
    )


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


def _fts_owned_objects(rows: list[tuple[Any, ...]]) -> set[tuple[str, str]]:
    has_virtual_table = any(
        _is_items_fts_virtual_table(object_type, name, sql)
        for object_type, name, _, sql in rows
    )
    if not has_virtual_table:
        return set()

    owned = {("table", FTS_TABLE)} | {
        ("table", shadow) for shadow in FTS_SHADOW_TABLES
    }
    owned.update(
        ("trigger", name)
        for object_type, name, _, sql in rows
        if object_type == "trigger" and _trigger_mutates_items_fts(sql)
    )
    return owned


def included_schema_rows(
    connection: sqlite3.Connection, schema: str
) -> list[tuple[str, str, str, str | None]]:
    rows = _schema_rows(connection, schema)
    fts_owned = _fts_owned_objects(rows)
    included: list[tuple[str, str, str, str | None]] = []
    for object_type, name, table_name, sql in rows:
        if object_type not in SUPPORTED_OBJECT_TYPES:
            raise ReplicaInvalid(f"unsupported persistent schema object: {object_type} {name}")
        if (object_type, name) in fts_owned:
            continue
        if name != "sqlite_sequence" and sql is None:
            raise ReplicaInvalid(f"schema SQL is NULL for persistent object: {object_type} {name}")
        included.append((object_type, name, table_name, sql))
    return included


def _fts_objects(connection: sqlite3.Connection, schema: str) -> list[tuple[str, str]]:
    rows = _schema_rows(connection, schema)
    owned = _fts_owned_objects(rows)
    return sorted(owned)


def _persistent_pragmas(connection: sqlite3.Connection, schema: str) -> dict[str, Any]:
    return {
        key: connection.execute(
            f"PRAGMA {quote_identifier(schema)}.{quote_identifier(key)}"
        ).fetchone()[0]
        for key in PERSISTENT_PRAGMAS
    }


def _visible_columns(connection: sqlite3.Connection, schema: str, table: str) -> list[str]:
    rows = connection.execute(
        f"PRAGMA {quote_identifier(schema)}.table_xinfo({quote_identifier(table)})"
    ).fetchall()
    return [row[1] for row in rows if row[6] == 0]


def _primary_key_columns(connection: sqlite3.Connection, schema: str, table: str) -> list[str]:
    rows = connection.execute(
        f"PRAGMA {quote_identifier(schema)}.table_xinfo({quote_identifier(table)})"
    ).fetchall()
    return [row[1] for row in sorted(rows, key=lambda row: row[5]) if row[5] > 0]


def _table_order(connection: sqlite3.Connection, schema: str, table: str) -> str:
    keys = _primary_key_columns(connection, schema, table)
    if not keys:
        raise ReplicaInvalid(f"table lacks a stable primary key: {table}")
    return ", ".join(quote_identifier(column) for column in keys)


def _encode_value(value: Any) -> bytes:
    if value is None:
        return b"n"
    if isinstance(value, int):
        payload = str(value).encode("ascii")
        return b"i" + len(payload).to_bytes(8, "big") + payload
    if isinstance(value, float):
        return b"f" + struct.pack(">d", value)
    if isinstance(value, str):
        payload = value.encode("utf-8")
        return b"t" + len(payload).to_bytes(8, "big") + payload
    if isinstance(value, bytes):
        return b"b" + len(value).to_bytes(8, "big") + value
    raise ReplicaInvalid(f"unsupported SQLite value type: {type(value)!r}")


def _table_digest(
    connection: sqlite3.Connection,
    schema: str,
    table: str,
    order_clause: str,
) -> tuple[int, str]:
    columns = _visible_columns(connection, schema, table)
    if not columns:
        raise ReplicaInvalid(f"table has no visible columns: {table}")
    selected = ", ".join(quote_identifier(column) for column in columns)
    cursor = connection.execute(
        f"SELECT {selected} FROM {quote_identifier(schema)}.{quote_identifier(table)} "
        f"ORDER BY {order_clause}"
    )
    digest = hashlib.sha256()
    count = 0
    for row in cursor:
        count += 1
        digest.update(b"r")
        for value in row:
            digest.update(_encode_value(value))
    return count, digest.hexdigest()


def _encoded_row(row: tuple[Any, ...]) -> bytes:
    encoded = bytearray(b"r")
    for value in row:
        encoded.extend(_encode_value(value))
    return bytes(encoded)


def _table_multiset_digest(
    connection: sqlite3.Connection,
    schema: str,
    table: str,
) -> tuple[int, str]:
    columns = _visible_columns(connection, schema, table)
    if not columns:
        raise ReplicaInvalid(f"table has no visible columns: {table}")
    selected = ", ".join(quote_identifier(column) for column in columns)
    row_hashes = sorted(
        hashlib.sha256(_encoded_row(row)).digest()
        for row in connection.execute(
            f"SELECT {selected} FROM {quote_identifier(schema)}.{quote_identifier(table)}"
        )
    )
    digest = hashlib.sha256(b"ai-radar-table-multiset-v1\0")
    digest.update(len(row_hashes).to_bytes(8, "big"))
    for row_hash in row_hashes:
        digest.update(row_hash)
    return len(row_hashes), digest.hexdigest()


def _has_null_primary_key(
    connection: sqlite3.Connection,
    schema: str,
    table: str,
    keys: list[str],
) -> bool:
    null_predicate = " OR ".join(f"{quote_identifier(key)} IS NULL" for key in keys)
    return (
        connection.execute(
            f"SELECT 1 FROM {quote_identifier(schema)}.{quote_identifier(table)} "
            f"WHERE {null_predicate} LIMIT 1"
        ).fetchone()
        is not None
    )


def _rows_in_deterministic_order(
    connection: sqlite3.Connection,
    schema: str,
    table: str,
) -> list[tuple[Any, ...]]:
    columns = _visible_columns(connection, schema, table)
    selected = ", ".join(quote_identifier(column) for column in columns)
    rows = connection.execute(
        f"SELECT {selected} FROM {quote_identifier(schema)}.{quote_identifier(table)}"
    ).fetchall()
    rows.sort(key=_encoded_row)
    return rows


def _sqlite_sequence(connection: sqlite3.Connection, schema: str) -> list[tuple[str, int]]:
    exists = connection.execute(
        f"SELECT 1 FROM {quote_identifier(schema)}.sqlite_master "
        "WHERE type = 'table' AND name = 'sqlite_sequence'"
    ).fetchone()
    if not exists:
        return []
    return connection.execute(
        f"SELECT name, seq FROM {quote_identifier(schema)}.sqlite_sequence ORDER BY name"
    ).fetchall()


def _open_readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{_resolved(path)}?mode=ro&immutable=1", uri=True)


def _quick_check(connection: sqlite3.Connection, schema: str) -> None:
    result = connection.execute(
        f"PRAGMA {quote_identifier(schema)}.quick_check"
    ).fetchone()[0]
    if result != "ok":
        raise ReplicaInvalid(f"{schema} failed quick_check: {result}")


def _validate_replica_shape(snapshot: Path, replica: Path) -> None:
    try:
        connection = _open_readonly(replica)
        connection.execute(
            "ATTACH DATABASE ? AS snapshot",
            (f"file:{_resolved(snapshot)}?mode=ro&immutable=1",),
        )
        try:
            _quick_check(connection, "main")
            _quick_check(connection, "snapshot")
            fts_objects = _fts_objects(connection, "main")
            if fts_objects:
                raise ReplicaInvalid(f"replica contains FTS objects: {fts_objects!r}")
            if included_schema_rows(connection, "main") != included_schema_rows(
                connection, "snapshot"
            ):
                raise ReplicaInvalid("non-FTS schema differs from snapshot")
            if _persistent_pragmas(connection, "main") != _persistent_pragmas(
                connection, "snapshot"
            ):
                raise ReplicaInvalid("persistent pragmas differ from snapshot")
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        raise ReplicaInvalid(f"replica is not a readable SQLite database: {exc}") from exc


def reconcile_replica(*, snapshot: Path, replica: Path) -> list[TableDigest]:
    """Compare all non-FTS tables and schema; raise on any mismatch."""

    _validate_replica_shape(snapshot, replica)
    connection = _open_readonly(replica)
    connection.execute(
        "ATTACH DATABASE ? AS snapshot",
        (f"file:{_resolved(snapshot)}?mode=ro&immutable=1",),
    )
    try:
        table_names = [
            name
            for object_type, name, _, _ in included_schema_rows(connection, "main")
            if object_type == "table" and name != "sqlite_sequence"
        ]
        results: list[TableDigest] = []
        for table in sorted(table_names):
            keys = _primary_key_columns(connection, "main", table)
            if not keys:
                raise ReplicaInvalid(f"table lacks a stable primary key: {table}")
            nullable_keys = _has_null_primary_key(
                connection, "main", table, keys
            ) or _has_null_primary_key(connection, "snapshot", table, keys)
            if nullable_keys:
                actual = _table_multiset_digest(connection, "main", table)
                expected = _table_multiset_digest(connection, "snapshot", table)
            else:
                order = _table_order(connection, "main", table)
                actual = _table_digest(connection, "main", table, order)
                expected = _table_digest(connection, "snapshot", table, order)
            if actual != expected:
                raise ReconciliationError(
                    f"reconciliation mismatch for {table}: replica={actual} snapshot={expected}"
                )
            results.append(TableDigest(table, actual[0], actual[1]))

        actual_sequence = _sqlite_sequence(connection, "main")
        expected_sequence = _sqlite_sequence(connection, "snapshot")
        if actual_sequence != expected_sequence:
            raise ReconciliationError(
                "reconciliation mismatch for sqlite_sequence: "
                f"replica={actual_sequence!r} snapshot={expected_sequence!r}"
            )
        if actual_sequence or expected_sequence:
            sequence_digest = hashlib.sha256()
            for row in actual_sequence:
                sequence_digest.update(b"r")
                for value in row:
                    sequence_digest.update(_encode_value(value))
            results.append(
                TableDigest("sqlite_sequence", len(actual_sequence), sequence_digest.hexdigest())
            )
        return results
    finally:
        connection.close()


def _fsync_file_and_parent(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _cleanup_sqlite_sidecars(path: Path) -> None:
    for suffix in ("-journal", "-wal", "-shm"):
        sidecar = Path(f"{path}{suffix}")
        if sidecar.exists():
            sidecar.unlink()


def _build_base_copy(snapshot: Path, destination: Path) -> None:
    source = _resolved(snapshot)
    destination = _resolved(destination)
    if source == destination or _same_inode(source, destination):
        raise SafetyError("snapshot and replica destination must be distinct")
    if destination.exists() and destination.stat().st_size:
        raise ReplicaError(f"base-copy destination already contains data: {destination}")

    source_connection = _open_readonly(source)
    try:
        _quick_check(source_connection, "main")
        source_pragmas = _persistent_pragmas(source_connection, "main")
        source_schema = included_schema_rows(source_connection, "main")
    finally:
        source_connection.close()

    destination.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(destination, isolation_level=None, uri=True)
    try:
        connection.execute("ATTACH DATABASE ? AS src", (f"file:{source}?mode=ro&immutable=1",))
        connection.execute(f"PRAGMA main.page_size={int(source_pragmas['page_size'])}")
        connection.execute(f"PRAGMA main.auto_vacuum={int(source_pragmas['auto_vacuum'])}")
        encoding = str(source_pragmas["encoding"]).replace("'", "''")
        connection.execute(f"PRAGMA main.encoding='{encoding}'")
        connection.execute("PRAGMA main.journal_mode=DELETE")
        connection.execute("PRAGMA main.synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=OFF")

        tables = [row for row in source_schema if row[0] == "table" and row[1] != "sqlite_sequence"]
        indexes = [row for row in source_schema if row[0] == "index"]
        views = [row for row in source_schema if row[0] == "view"]
        triggers = [row for row in source_schema if row[0] == "trigger"]
        for _, name, _, sql in tables:
            if sql is None or sql.lstrip().upper().startswith("CREATE VIRTUAL TABLE"):
                raise ReplicaInvalid(f"unsupported non-FTS table definition: {name}")

        connection.execute("BEGIN IMMEDIATE")
        for _, _, _, sql in tables:
            assert sql is not None
            connection.execute(sql)
        for _, name, _, _ in sorted(tables, key=lambda row: row[1]):
            quoted = quote_identifier(name)
            keys = _primary_key_columns(connection, "src", name)
            if not keys:
                raise ReplicaInvalid(f"table lacks a stable primary key: {name}")
            if _has_null_primary_key(connection, "src", name, keys):
                rows = _rows_in_deterministic_order(connection, "src", name)
                columns = _visible_columns(connection, "src", name)
                connection.executemany(
                    f"INSERT INTO main.{quoted} VALUES ("
                    + ", ".join("?" for _ in columns)
                    + ")",
                    rows,
                )
            else:
                order = _table_order(connection, "src", name)
                connection.execute(
                    f"INSERT INTO main.{quoted} SELECT * FROM src.{quoted} ORDER BY {order}"
                )

        source_names = {row[1] for row in tables}
        if _sqlite_sequence(connection, "src"):
            placeholders = ",".join("?" for _ in source_names)
            connection.execute("DELETE FROM main.sqlite_sequence")
            connection.execute(
                "INSERT INTO main.sqlite_sequence(name, seq) "
                f"SELECT name, seq FROM src.sqlite_sequence WHERE name IN ({placeholders}) ORDER BY name",
                tuple(sorted(source_names)),
            )
        for _, _, _, sql in indexes + views + triggers:
            assert sql is not None
            connection.execute(sql)
        connection.execute(f"PRAGMA main.user_version={int(source_pragmas['user_version'])}")
        connection.execute(f"PRAGMA main.application_id={int(source_pragmas['application_id'])}")
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()
    _cleanup_sqlite_sidecars(destination)
    _fsync_file_and_parent(destination)


def _replace_with_base_copy(snapshot: Path, replica: Path) -> list[TableDigest]:
    replica = _resolved(replica)
    replica.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{replica.name}.rebuild-", suffix=".tmp", dir=replica.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        _build_base_copy(snapshot, temporary)
        reconciliation = reconcile_replica(snapshot=snapshot, replica=temporary)
        os.replace(temporary, replica)
        _fsync_file_and_parent(replica)
        return reconciliation
    finally:
        if temporary.exists():
            temporary.unlink()
        _cleanup_sqlite_sidecars(temporary)


def _logical_size(rows: list[tuple[Any, ...]]) -> int:
    return sum(len(_encode_value(value)) for row in rows for value in row)


def _apply_delta(snapshot: Path, replica: Path) -> int:
    connection = sqlite3.connect(_resolved(replica), isolation_level=None, uri=True)
    try:
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            "ATTACH DATABASE ? AS target",
            (f"file:{_resolved(snapshot)}?mode=ro&immutable=1",),
        )
        main_schema = included_schema_rows(connection, "main")
        target_schema = included_schema_rows(connection, "target")
        if main_schema != target_schema:
            raise ReplicaInvalid("non-FTS schema differs from snapshot")

        tables = [
            name
            for object_type, name, _, _ in main_schema
            if object_type == "table" and name != "sqlite_sequence"
        ]
        ordinary_triggers = [
            (name, sql)
            for object_type, name, _, sql in target_schema
            if object_type == "trigger" and sql is not None
        ]
        changes: dict[str, dict[str, Any]] = {}
        for table in tables:
            columns = _visible_columns(connection, "main", table)
            keys = _primary_key_columns(connection, "main", table)
            if not keys:
                raise ReplicaInvalid(f"table lacks a stable primary key: {table}")
            if _has_null_primary_key(
                connection, "main", table, keys
            ) or _has_null_primary_key(connection, "target", table, keys):
                print(
                    f"[replica] FALLBACK table={table} reason=nullable-primary-key "
                    "action=deterministic-whole-table-compare",
                    file=sys.stderr,
                    flush=True,
                )
                current_digest = _table_multiset_digest(connection, "main", table)
                target_digest = _table_multiset_digest(connection, "target", table)
                replacement = (
                    _rows_in_deterministic_order(connection, "target", table)
                    if current_digest != target_digest
                    else None
                )
                changes[table] = {
                    "mode": "replace",
                    "columns": columns,
                    "replacement": replacement,
                    "existing_count": current_digest[0],
                }
                continue
            quoted_table = quote_identifier(table)
            selected = ", ".join(f"b.{quote_identifier(column)}" for column in columns)
            key_match = " AND ".join(
                f"a.{quote_identifier(column)} IS b.{quote_identifier(column)}" for column in keys
            )
            row_diff = " OR ".join(
                "NOT ("
                f"a.{quote_identifier(column)} IS b.{quote_identifier(column)} AND "
                f"typeof(a.{quote_identifier(column)}) = typeof(b.{quote_identifier(column)}))"
                for column in columns
            )
            order = ", ".join(f"b.{quote_identifier(column)}" for column in keys)
            inserts = connection.execute(
                f"SELECT {selected} FROM target.{quoted_table} AS b "
                f"WHERE NOT EXISTS (SELECT 1 FROM main.{quoted_table} AS a WHERE {key_match}) "
                f"ORDER BY {order}"
            ).fetchall()
            updates = connection.execute(
                f"SELECT {selected} FROM target.{quoted_table} AS b "
                f"JOIN main.{quoted_table} AS a ON {key_match} WHERE {row_diff} ORDER BY {order}"
            ).fetchall()
            delete_order = ", ".join(f"a.{quote_identifier(column)}" for column in keys)
            deletes = connection.execute(
                "SELECT "
                + ", ".join(f"a.{quote_identifier(column)}" for column in keys)
                + f" FROM main.{quoted_table} AS a "
                f"WHERE NOT EXISTS (SELECT 1 FROM target.{quoted_table} AS b WHERE {key_match}) "
                f"ORDER BY {delete_order}"
            ).fetchall()
            changes[table] = {
                "mode": "delta",
                "columns": columns,
                "keys": keys,
                "inserts": inserts,
                "updates": updates,
                "deletes": deletes,
            }

        current_sequence = _sqlite_sequence(connection, "main")
        target_sequence = _sqlite_sequence(connection, "target")
        operations = 0
        for change in changes.values():
            if change["mode"] == "replace":
                replacement = change["replacement"]
                if replacement is not None:
                    operations += change["existing_count"] + len(replacement)
            else:
                operations += sum(
                    len(change[operation])
                    for operation in ("inserts", "updates", "deletes")
                )
        has_table_writes = operations > 0
        if current_sequence != target_sequence:
            operations += len(target_sequence) + len(current_sequence)

        connection.execute("BEGIN IMMEDIATE")
        if has_table_writes:
            for name, _ in ordinary_triggers:
                connection.execute(f"DROP TRIGGER main.{quote_identifier(name)}")
        apply_order = [table for table in sorted(tables) if table != "archive_cache_generations"]
        if "archive_cache_generations" in tables:
            apply_order.append("archive_cache_generations")
        for table in apply_order:
            change = changes[table]
            columns = change["columns"]
            quoted_table = quote_identifier(table)
            if change["mode"] == "replace":
                replacement = change["replacement"]
                if replacement is not None:
                    connection.execute(f"DELETE FROM main.{quoted_table}")
                    connection.executemany(
                        f"INSERT INTO main.{quoted_table} ("
                        + ", ".join(quote_identifier(column) for column in columns)
                        + ") VALUES ("
                        + ", ".join("?" for _ in columns)
                        + ")",
                        replacement,
                    )
                continue
            keys = change["keys"]
            key_where = " AND ".join(f"{quote_identifier(column)} IS ?" for column in keys)
            if change["deletes"]:
                connection.executemany(
                    f"DELETE FROM main.{quoted_table} WHERE {key_where}", change["deletes"]
                )
            if change["inserts"]:
                connection.executemany(
                    f"INSERT INTO main.{quoted_table} ("
                    + ", ".join(quote_identifier(column) for column in columns)
                    + ") VALUES ("
                    + ", ".join("?" for _ in columns)
                    + ")",
                    change["inserts"],
                )
            non_keys = [column for column in columns if column not in keys]
            if change["updates"] and non_keys:
                positions = {column: index for index, column in enumerate(columns)}
                values = [
                    tuple(row[positions[column]] for column in non_keys + keys)
                    for row in change["updates"]
                ]
                connection.executemany(
                    f"UPDATE main.{quoted_table} SET "
                    + ", ".join(f"{quote_identifier(column)} = ?" for column in non_keys)
                    + f" WHERE {key_where}",
                    values,
                )
        if current_sequence != target_sequence:
            connection.execute("DELETE FROM main.sqlite_sequence")
            connection.executemany(
                "INSERT INTO main.sqlite_sequence(name, seq) VALUES (?, ?)", target_sequence
            )
        if has_table_writes:
            for _, sql in ordinary_triggers:
                connection.execute(sql)
        connection.execute("COMMIT")
        _cleanup_sqlite_sidecars(_resolved(replica))
        return operations
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()


def sync_replica(*, live_db: Path, snapshot: Path, replica: Path) -> SyncResult:
    """Bring ``replica`` to ``snapshot`` and return the round's transfer mode."""

    assert_safe_artifacts(live_db=live_db, snapshot=snapshot, replica=replica)
    snapshot = _resolved(snapshot)
    replica = _resolved(replica)
    if not snapshot.is_file():
        raise ReplicaError(f"snapshot not found: {snapshot}")

    if not replica.exists():
        _replace_with_base_copy(snapshot, replica)
        return SyncResult("bootstrap", 0, "replica absent")

    try:
        _validate_replica_shape(snapshot, replica)
        operations = _apply_delta(snapshot, replica)
        reconcile_replica(snapshot=snapshot, replica=replica)
        _fsync_file_and_parent(replica)
        return SyncResult("incremental", operations, "logical delta reconciled")
    except (ReplicaInvalid, ReconciliationError, sqlite3.DatabaseError) as exc:
        print(
            f"[replica] !!! SELF-HEAL: {exc}; rebuilding the base-only shipping replica",
            file=sys.stderr,
            flush=True,
        )
        _replace_with_base_copy(snapshot, replica)
        return SyncResult("self-heal", 0, f"reconciliation/replica failure: {exc}")


def _parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    guard = subparsers.add_parser("guard", help="refuse live/artifact path or inode aliases")
    guard.add_argument("--live-db", type=Path, required=True)
    guard.add_argument("--snapshot", type=Path, required=True)
    guard.add_argument("--replica", type=Path, required=True)
    guard.add_argument("--manifest", type=Path, required=True)

    sync = subparsers.add_parser("sync", help="bootstrap or incrementally update the replica")
    sync.add_argument("--live-db", type=Path, required=True)
    sync.add_argument("--snapshot", type=Path, required=True)
    sync.add_argument("--replica", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.command == "guard":
            assert_safe_artifacts(
                live_db=args.live_db,
                snapshot=args.snapshot,
                replica=args.replica,
                manifest=args.manifest,
            )
            print("[replica] live database and writable artifacts are path/inode distinct")
            return 0

        result = sync_replica(
            live_db=args.live_db,
            snapshot=args.snapshot,
            replica=args.replica,
        )
        for digest in reconcile_replica(snapshot=args.snapshot, replica=args.replica):
            print(
                f"[replica] table={digest.table} rows={digest.rows} sha256={digest.sha256} match=1"
            )
        print(
            f"[replica] mode={result.mode} operations={result.operations} reason={result.reason}"
        )
        return 0
    except ReplicaError as exc:
        print(f"[replica] ✗ {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
