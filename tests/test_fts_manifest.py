"""Snapshot-bound FTS baseline manifest contract."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import unicodedata
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "deploy" / "sync" / "build_fts_manifest.py"
SEARCH_FIELDS = ("title", "content_text", "source_name", "author", "title_zh")


def _load_module():
    spec = importlib.util.spec_from_file_location("build_fts_manifest", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


manifest_module = _load_module()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_manifest_module_contract_preserves_raw_newlines() -> None:
    assert "replacing CRLF/CR with LF" not in manifest_module.__doc__
    assert "without Unicode or newline rewriting" in manifest_module.__doc__


def _create_snapshot(path: Path, *, exclusive: bool = True) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE sources (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL
            );
            CREATE TABLE items (
                id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                url TEXT NOT NULL,
                title TEXT NOT NULL,
                content_text TEXT,
                author TEXT,
                published_at TEXT NOT NULL,
                fetched_at TEXT NOT NULL
            );
            CREATE TABLE item_evaluations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                numeric_json TEXT,
                error TEXT
            );
            INSERT INTO sources VALUES ('source', 'Generic Source');
            """
        )
        connection.execute(
            """
            CREATE VIRTUAL TABLE items_fts USING fts5(
                item_id UNINDEXED,
                title,
                content_text,
                source_name,
                author,
                title_zh,
                tokenize='trigram'
            )
            """
        )
        if exclusive:
            rows = [
                ("item-title", "TitleOnlyBeacon", "generic body", "Generic Source", "Generic Author", "通用译名甲"),
                ("item-content", "generic title", "ContentOnlyHarbor", "Generic Source", "Generic Author", "通用译名乙"),
                ("item-source", "generic title", "generic body", "SourceOnlyCedar", "Generic Author", "通用译名丙"),
                ("item-author", "generic title", "generic body", "Generic Source", "AuthorOnlyQuartz", "通用译名丁"),
                ("item-zh", "generic title", "generic body", "Generic Source", "Generic Author", "中文独有灯塔词"),
            ]
        else:
            rows = [("item-shared",) + ("SharedOnlyToken",) * 5]
        connection.executemany(
            "INSERT INTO items_fts(item_id, title, content_text, source_name, author, title_zh) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        connection.executemany(
            "INSERT INTO items "
            "(id, source_id, url, title, content_text, author, published_at, fetched_at) "
            "VALUES (?, 'source', ?, ?, ?, ?, ?, ?)",
            [
                (
                    row[0],
                    f"https://example.invalid/{row[0]}",
                    row[1],
                    row[2],
                    row[4],
                    f"2026-01-0{index}T00:00:00Z",
                    f"2026-01-0{index}T00:00:00Z",
                )
                for index, row in enumerate(rows, start=1)
            ],
        )
        connection.commit()
    finally:
        connection.close()


def _create_artifact(path: Path, marker: str = "base") -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE items (id TEXT PRIMARY KEY, marker TEXT NOT NULL)")
        connection.execute("INSERT INTO items VALUES ('i1', ?)", (marker,))
        connection.commit()
    finally:
        connection.close()


def test_manifest_is_deterministic_and_contains_full_oracle_and_exclusive_probes(
    tmp_path: Path,
) -> None:
    snapshot = tmp_path / "snapshot.db"
    artifact = tmp_path / "shipping.db"
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    _create_snapshot(snapshot)
    _create_artifact(artifact)

    manifest_module.build_manifest(snapshot=snapshot, artifact=artifact, output=first_path)
    manifest_module.build_manifest(snapshot=snapshot, artifact=artifact, output=second_path)

    assert first_path.read_bytes() == second_path.read_bytes()
    payload = json.loads(first_path.read_text(encoding="utf-8"))
    assert payload["format_version"] == 2
    assert payload["snapshot_id"] == _sha256(artifact)
    assert len(payload["snapshot_id"]) == 64
    assert payload["fts"]["table"] == "items_fts"
    assert payload["fts"]["fields"] == [
        "item_id",
        "title",
        "content_text",
        "source_name",
        "author",
        "title_zh",
    ]
    assert payload["fts"]["row_count"] == 5
    assert len(payload["fts"]["sha256"]) == 64
    assert "raw UTF-8" in payload["fts"]["normalization"]

    assert set(payload["probes"]) == set(SEARCH_FIELDS)
    for field in SEARCH_FIELDS:
        probe = payload["probes"][field]
        assert probe["field"] == field
        assert probe["term"]
        assert probe["exclusive"] is True
        assert probe["matches"]["count"] > 0
        assert probe["matches"] == probe["unqualified_matches"]
        assert probe["timeline_http_matches"]["count"] > 0
        assert set(probe["timeline_http_matches"]["item_ids"]).issubset(
            probe["unqualified_matches"]["item_ids"]
        )
        assert probe["field_matches"][field] == probe["matches"]
        for other in SEARCH_FIELDS:
            if other != field:
                assert probe["field_matches"][other] == {"count": 0, "item_ids": []}

    assert payload["probes"]["title_zh"]["term"] not in {
        "",
        payload["probes"]["title"]["term"],
    }
    manifest_module.validate_manifest(payload)


def test_manifest_http_expectations_filter_nonvisible_raw_fts_matches(
    tmp_path: Path,
) -> None:
    snapshot = tmp_path / "snapshot.db"
    artifact = tmp_path / "shipping.db"
    output = tmp_path / "manifest.json"
    _create_snapshot(snapshot)
    _create_artifact(artifact)
    with sqlite3.connect(snapshot) as connection:
        connection.execute(
            "INSERT INTO items_fts "
            "(item_id, title, content_text, source_name, author, title_zh) "
            "VALUES ('item-title-hidden', 'TitleOnlyBeacon', 'generic body', "
            "'Generic Source', 'Generic Author', '通用译名隐')"
        )
        connection.execute(
            "INSERT INTO items "
            "(id, source_id, url, title, content_text, author, published_at, fetched_at) "
            "VALUES ('item-title-hidden', 'source', "
            "'https://example.invalid/item-title-hidden', 'TitleOnlyBeacon', "
            "'generic body', 'Generic Author', '2026-01-09T00:00:00Z', "
            "'2026-01-09T00:00:00Z')"
        )
        visible_ids = [
            "item-title",
            "item-content",
            "item-source",
            "item-author",
            "item-zh",
        ]
        connection.executemany(
            "INSERT INTO item_evaluations (item_id, stage, numeric_json, error) "
            "VALUES (?, 'prefilter', '{\"is_ai_related\":1}', NULL)",
            [(item_id,) for item_id in visible_ids],
        )
        connection.execute(
            "INSERT INTO item_evaluations (item_id, stage, numeric_json, error) "
            "VALUES ('item-title-hidden', 'prefilter', "
            "'{\"is_ai_related\":0}', NULL)"
        )

    payload = manifest_module.build_manifest(
        snapshot=snapshot,
        artifact=artifact,
        output=output,
    )

    title_probe = payload["probes"]["title"]
    assert "item-title-hidden" in title_probe["unqualified_matches"]["item_ids"]
    assert "item-title-hidden" not in title_probe["timeline_http_matches"]["item_ids"]
    assert "item-title" in title_probe["timeline_http_matches"]["item_ids"]
    assert title_probe["unqualified_matches"]["count"] == (
        title_probe["timeline_http_matches"]["count"] + 1
    )
    assert title_probe["matches"] == title_probe["unqualified_matches"]
    assert payload["fts"]["row_count"] == 6


def test_manifest_applies_visibility_before_rejecting_app_search_expansion(
    tmp_path: Path,
) -> None:
    snapshot = tmp_path / "snapshot.db"
    artifact = tmp_path / "shipping.db"
    output = tmp_path / "manifest.json"
    _create_snapshot(snapshot)
    _create_artifact(artifact)
    with sqlite3.connect(snapshot) as connection:
        connection.execute(
            "UPDATE items_fts SET item_id='a-visible', title='归藏 工具' "
            "WHERE item_id='item-title'"
        )
        connection.execute(
            "UPDATE items SET id='a-visible', title='归藏 工具', "
            "url='https://example.invalid/a-visible' WHERE id='item-title'"
        )
        connection.execute(
            "UPDATE items_fts SET title='generic body' "
            "WHERE item_id<>'a-visible'"
        )
        connection.execute(
            "UPDATE items SET title='generic body' WHERE id<>'a-visible'"
        )
        connection.execute(
            "INSERT INTO items_fts "
            "(item_id, title, content_text, source_name, author, title_zh) "
            "VALUES ('z-hidden', '归藏工具', 'generic body', "
            "'Generic Source', 'Generic Author', '通用译名隐')"
        )
        connection.execute(
            "INSERT INTO items "
            "(id, source_id, url, title, content_text, author, published_at, fetched_at) "
            "VALUES ('z-hidden', 'source', 'https://example.invalid/z-hidden', "
            "'归藏工具', 'generic body', 'Generic Author', "
            "'2026-01-09T00:00:00Z', '2026-01-09T00:00:00Z')"
        )
        visible_ids = [
            "a-visible",
            "item-content",
            "item-source",
            "item-author",
            "item-zh",
        ]
        connection.executemany(
            "INSERT INTO item_evaluations (item_id, stage, numeric_json, error) "
            "VALUES (?, 'prefilter', '{\"is_ai_related\":1}', NULL)",
            [(item_id,) for item_id in visible_ids],
        )
        connection.execute(
            "INSERT INTO item_evaluations (item_id, stage, numeric_json, error) "
            "VALUES ('z-hidden', 'prefilter', '{\"is_ai_related\":0}', NULL)"
        )

    payload = manifest_module.build_manifest(
        snapshot=snapshot,
        artifact=artifact,
        output=output,
    )

    title_probe = payload["probes"]["title"]
    assert title_probe["term"] == "归藏 工具"
    assert title_probe["unqualified_matches"] == {
        "count": 1,
        "item_ids": ["a-visible"],
    }
    assert title_probe["timeline_http_matches"] == {
        "count": 1,
        "item_ids": ["a-visible"],
    }


def test_manifest_fails_closed_when_exclusive_terms_have_no_visible_match(
    tmp_path: Path,
) -> None:
    snapshot = tmp_path / "snapshot.db"
    artifact = tmp_path / "shipping.db"
    output = tmp_path / "manifest.json"
    _create_snapshot(snapshot)
    _create_artifact(artifact)
    with sqlite3.connect(snapshot) as connection:
        item_ids = [row[0] for row in connection.execute("SELECT id FROM items")]
        connection.executemany(
            "INSERT INTO item_evaluations (item_id, stage, numeric_json, error) "
            "VALUES (?, 'prefilter', '{\"is_ai_related\":0}', NULL)",
            [(item_id,) for item_id in item_ids],
        )

    with pytest.raises(manifest_module.ManifestError, match="visible.*title"):
        manifest_module.build_manifest(
            snapshot=snapshot,
            artifact=artifact,
            output=output,
        )

    assert not output.exists()


@pytest.mark.parametrize(
    "corruption",
    [
        "missing-timeline",
        "empty-timeline",
        "count-drift",
        "wrong-query",
        "wrong-unqualified",
        "nonexclusive-field",
        "timeline-exceeds-raw",
    ],
)
def test_manifest_validation_rejects_probe_contract_corruption(
    tmp_path: Path,
    corruption: str,
) -> None:
    snapshot = tmp_path / "snapshot.db"
    artifact = tmp_path / "shipping.db"
    output = tmp_path / "manifest.json"
    _create_snapshot(snapshot)
    _create_artifact(artifact)
    payload = manifest_module.build_manifest(
        snapshot=snapshot,
        artifact=artifact,
        output=output,
    )
    tampered = json.loads(json.dumps(payload))
    probe = tampered["probes"]["title"]
    if corruption == "missing-timeline":
        del probe["timeline_http_matches"]
    elif corruption == "empty-timeline":
        probe["timeline_http_matches"] = {"count": 0, "item_ids": []}
    elif corruption == "count-drift":
        probe["timeline_http_matches"]["count"] += 1
    elif corruption == "wrong-query":
        probe["query"] = probe["unqualified_query"]
    elif corruption == "wrong-unqualified":
        probe["unqualified_matches"] = {"count": 0, "item_ids": []}
    elif corruption == "nonexclusive-field":
        probe["field_matches"]["author"] = probe["matches"]
    else:
        probe["timeline_http_matches"] = {
            "count": 1,
            "item_ids": ["not-a-raw-match"],
        }
    tampered["manifest_sha256"] = manifest_module.manifest_self_hash(tampered)

    with pytest.raises(manifest_module.ManifestError, match="probe title"):
        manifest_module.validate_manifest(tampered)


def test_manifest_build_fails_when_a_field_has_no_exclusive_term(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot.db"
    artifact = tmp_path / "shipping.db"
    output = tmp_path / "manifest.json"
    _create_snapshot(snapshot, exclusive=False)
    _create_artifact(artifact)

    with pytest.raises(manifest_module.ManifestError, match="exclusive.*title"):
        manifest_module.build_manifest(snapshot=snapshot, artifact=artifact, output=output)

    assert not output.exists()


def test_manifest_accepts_ordinary_fts_prefix_and_trigger_mentions(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot.db"
    artifact = tmp_path / "shipping.db"
    output = tmp_path / "manifest.json"
    _create_snapshot(snapshot)
    _create_artifact(artifact)
    with sqlite3.connect(artifact) as connection:
        connection.executescript(
            """
            CREATE TABLE items_fts_audit (id INTEGER PRIMARY KEY, payload TEXT NOT NULL);
            INSERT INTO items_fts_audit VALUES (1, 'kept');
            CREATE TRIGGER ordinary_trigger_mentions_fts
            AFTER INSERT ON items_fts_audit BEGIN
              SELECT 'INSERT INTO items_fts is diagnostic text, not executable DML';
              SELECT "replace" "items_fts";
            END;
            """
        )

    payload = manifest_module.build_manifest(
        snapshot=snapshot,
        artifact=artifact,
        output=output,
    )

    assert output.is_file()
    assert payload["snapshot_id"] == _sha256(artifact)


def test_manifest_preserves_raw_unicode_codepoints_for_digest_and_match_probes(
    tmp_path: Path,
) -> None:
    nfd_snapshot = tmp_path / "nfd.db"
    nfc_snapshot = tmp_path / "nfc.db"
    artifact = tmp_path / "shipping.db"
    nfd_output = tmp_path / "nfd.json"
    nfc_output = tmp_path / "nfc.json"
    _create_snapshot(nfd_snapshot)
    _create_snapshot(nfc_snapshot)
    _create_artifact(artifact)
    nfd_title = "Cafe\u0301Unique"
    nfc_title = unicodedata.normalize("NFC", nfd_title)
    assert nfd_title != nfc_title
    with sqlite3.connect(nfd_snapshot) as connection:
        connection.execute(
            "UPDATE items_fts SET title='Generic Source' WHERE item_id<>'item-title'"
        )
        connection.execute(
            "UPDATE items_fts SET title=? WHERE item_id='item-title'", (nfd_title,)
        )
    with sqlite3.connect(nfc_snapshot) as connection:
        connection.execute(
            "UPDATE items_fts SET title='Generic Source' WHERE item_id<>'item-title'"
        )
        connection.execute(
            "UPDATE items_fts SET title=? WHERE item_id='item-title'", (nfc_title,)
        )

    nfd_manifest = manifest_module.build_manifest(
        snapshot=nfd_snapshot,
        artifact=artifact,
        output=nfd_output,
    )
    nfc_manifest = manifest_module.build_manifest(
        snapshot=nfc_snapshot,
        artifact=artifact,
        output=nfc_output,
    )

    assert nfd_manifest["fts"]["sha256"] != nfc_manifest["fts"]["sha256"]
    assert "raw UTF-8" in nfd_manifest["fts"]["normalization"]
    nfd_probe = nfd_manifest["probes"]["title"]
    assert nfd_probe["term"] == nfd_title
    assert unicodedata.normalize("NFC", nfd_probe["term"]) != nfd_probe["term"]
    assert nfd_probe["matches"] == {"count": 1, "item_ids": ["item-title"]}


def test_manifest_identity_and_self_hash_reject_tampering(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot.db"
    artifact = tmp_path / "shipping.db"
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    _create_snapshot(snapshot)
    _create_artifact(artifact, "first")
    manifest_module.build_manifest(snapshot=snapshot, artifact=artifact, output=first_path)
    first = json.loads(first_path.read_text(encoding="utf-8"))

    connection = sqlite3.connect(artifact)
    try:
        connection.execute("UPDATE items SET marker = 'second' WHERE id = 'i1'")
        connection.commit()
    finally:
        connection.close()
    manifest_module.build_manifest(snapshot=snapshot, artifact=artifact, output=second_path)
    second = json.loads(second_path.read_text(encoding="utf-8"))

    assert first["snapshot_id"] != second["snapshot_id"]
    assert first["manifest_sha256"] != second["manifest_sha256"]
    tampered = json.loads(first_path.read_text(encoding="utf-8"))
    tampered["snapshot_id"] = "0" * 64
    with pytest.raises(manifest_module.ManifestError, match="self-hash"):
        manifest_module.validate_manifest(tampered)
