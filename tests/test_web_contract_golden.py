from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.web_contract_golden import HttpSpec, capture, logical_db_invariant, record_db, verify


class _Headers(dict[str, str]):
    def get(self, key: str, default: str = "") -> str:
        return super().get(key, default)


class _Response:
    def __init__(
        self,
        body: bytes,
        content_type: str,
        *,
        status: int = 200,
        final_url: str | None = None,
    ) -> None:
        self._body = body
        self._final_url = final_url
        self.request_url = ""
        self.status = status
        self.headers = _Headers({"Content-Type": content_type})

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body

    def geturl(self) -> str:
        return self._final_url or self.request_url


def _manifest_entry(directory: Path, spec: HttpSpec, *, url: str) -> dict[str, object]:
    raw = (directory / spec.artifact).read_bytes()
    content_type = spec.expected_content_type or (
        "application/json" if spec.kind == "api-json" else "text/html; charset=utf-8"
    )
    return {
        "artifact": spec.artifact,
        "kind": spec.kind,
        "url": url,
        "status": spec.expected_status,
        "content_type": content_type,
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _write_manifest(directory: Path, specs: tuple[HttpSpec, ...]) -> None:
    entries = [_manifest_entry(directory, spec, url=f"http://test{spec.path}") for spec in specs]
    (directory / "request-manifest.json").write_text(json.dumps({"version": 1, "artifacts": entries}))


def _create_db(path: Path, *, reverse: bool = False) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE samples (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute(
            "CREATE TABLE typed (key TEXT PRIMARY KEY, integer_value INTEGER, real_value REAL, "
            "blob_value BLOB, nullable TEXT) WITHOUT ROWID"
        )
        rows = [
            ("alpha", 1, 1.5, b"a", None),
            ("beta", 2, 2.5, b"b", "present"),
        ]
        if reverse:
            rows.reverse()
        conn.execute("INSERT INTO samples(value) VALUES ('alpha')")
        conn.executemany("INSERT INTO typed VALUES (?, ?, ?, ?, ?)", rows)
        conn.commit()
    finally:
        conn.close()


def test_capture_canonicalizes_supported_response_kinds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    specs = (
        HttpSpec("api.json", "/api", "api-json"),
        HttpSpec("preload.json", "/page", "ssr-preload-json"),
        HttpSpec("page.html", "/html", "html"),
    )
    responses = {
        "http://test/api": _Response(b'{"success":true,"data":{"items":[1]},"error":null}', "application/json"),
        "http://test/page": _Response(
            b'<script id="__PRELOAD__" type="application/json">{"items":[1]}</script>',
            "text/html; charset=utf-8",
        ),
        "http://test/html": _Response(b"<h1>stable</h1>", "text/html; charset=utf-8"),
    }

    def fake_urlopen(request: object, timeout: int) -> _Response:
        del timeout
        url = request.full_url  # type: ignore[attr-defined]
        response = responses[url.replace("http://other", "http://test")]
        response.request_url = url
        return response

    monkeypatch.setattr("scripts.web_contract_golden.urlopen", fake_urlopen)

    golden = tmp_path / "golden"
    actual = tmp_path / "actual"
    capture("http://test", golden, concurrency=2, specs=specs)
    capture("http://other", actual, concurrency=2, specs=specs)

    assert (golden / "api.json").read_bytes() == (
        b'{\n    "success": true,\n    "data": {\n        "items": [\n            1\n        ]\n'
        b'    },\n    "error": null\n}\n'
    )
    assert (golden / "preload.json").read_bytes() == b'{\n  "items": [\n    1\n  ]\n}'
    assert (golden / "page.html").read_bytes() == b"<h1>stable</h1>"
    manifest = json.loads((golden / "request-manifest.json").read_text())
    assert [entry["artifact"] for entry in manifest["artifacts"]] == ["api.json", "page.html", "preload.json"]
    assert all(
        set(entry) == {"artifact", "kind", "url", "status", "content_type", "bytes", "sha256"}
        for entry in manifest["artifacts"]
    )

    db_path = tmp_path / "contract.db"
    _create_db(db_path)
    record_db(db_path, golden / "db-invariants.json")
    verify(db_path, golden, actual, specs=specs)


def test_verify_compares_json_semantically_and_html_byte_for_byte(tmp_path: Path) -> None:
    db_path = tmp_path / "contract.db"
    golden = tmp_path / "golden"
    actual = tmp_path / "actual"
    golden.mkdir()
    actual.mkdir()
    _create_db(db_path)
    specs = (
        HttpSpec("api.json", "/api", "api-json"),
        HttpSpec("preload.json", "/ssr", "ssr-preload-json"),
        HttpSpec("page.html", "/page", "html"),
    )
    (golden / "api.json").write_text('{"data":{"items":[1]},"error":null,"success":true}')
    (actual / "api.json").write_text('{\n  "success": true, "data": {"items": [1]}, "error": null\n}')
    (golden / "preload.json").write_text('{"items":[1]}')
    (actual / "preload.json").write_text('{\n  "items": [1]\n}')
    (golden / "page.html").write_text("<h1>stable</h1>")
    (actual / "page.html").write_text("<h1>stable</h1>")
    _write_manifest(golden, specs)
    _write_manifest(actual, specs)
    record_db(db_path, golden / "db-invariants.json")
    validated: list[Path] = []

    verify(db_path, golden, actual, specs=specs, validate_nonempty=validated.append)

    assert validated == [golden, actual]
    with pytest.raises(AssertionError, match="validator rejected"):
        verify(
            db_path,
            golden,
            actual,
            specs=specs,
            validate_nonempty=lambda _directory: (_ for _ in ()).throw(AssertionError("validator rejected")),
        )

    (actual / "api.json").write_text('{"success":true,"data":{"items":[true]},"error":null}')
    _write_manifest(actual, specs)
    with pytest.raises(AssertionError, match="JSON semantic mismatch"):
        verify(db_path, golden, actual, specs=specs)

    (actual / "api.json").write_text('{"success":true,"data":{"items":[2]},"error":null}')
    _write_manifest(actual, specs)
    with pytest.raises(AssertionError, match="JSON semantic mismatch"):
        verify(db_path, golden, actual, specs=specs)

    (actual / "api.json").write_text('{"success":true,"data":{"items":[1]},"error":null}')
    (actual / "preload.json").write_text('{"items":[2]}')
    _write_manifest(actual, specs)
    with pytest.raises(AssertionError, match="JSON semantic mismatch"):
        verify(db_path, golden, actual, specs=specs)

    (actual / "preload.json").write_text('{"items":[1]}')
    (actual / "page.html").write_text("<h1>changed</h1>")
    _write_manifest(actual, specs)
    with pytest.raises(AssertionError, match="HTML byte mismatch"):
        verify(db_path, golden, actual, specs=specs)

    (actual / "page.html").write_text("<h1>stable</h1>")
    _write_manifest(actual, specs)
    manifest = json.loads((actual / "request-manifest.json").read_text())
    manifest["artifacts"][0]["url"] = "http://wrong/not-api"
    (actual / "request-manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(AssertionError, match="url mismatch"):
        verify(db_path, golden, actual, specs=specs)


@pytest.mark.parametrize(
    ("field", "bad_value", "message"),
    [
        ("bytes", 999, "bytes"),
        ("sha256", "0" * 64, "sha256"),
        ("status", 500, "status"),
        ("kind", "html", "kind"),
        ("content_type", "text/plain", "content_type"),
    ],
)
def test_verify_rejects_invalid_manifest_fields(
    tmp_path: Path,
    field: str,
    bad_value: object,
    message: str,
) -> None:
    db_path = tmp_path / "contract.db"
    golden = tmp_path / "golden"
    actual = tmp_path / "actual"
    golden.mkdir()
    actual.mkdir()
    _create_db(db_path)
    specs = (HttpSpec("api.json", "/api", "api-json"),)
    (golden / "api.json").write_text('{"items":[1]}')
    (actual / "api.json").write_text('{"items":[1]}')
    _write_manifest(golden, specs)
    _write_manifest(actual, specs)
    record_db(db_path, golden / "db-invariants.json")
    manifest = json.loads((actual / "request-manifest.json").read_text())
    manifest["artifacts"][0][field] = bad_value
    (actual / "request-manifest.json").write_text(json.dumps(manifest))

    with pytest.raises(AssertionError, match=message):
        verify(db_path, golden, actual, specs=specs)

    golden_manifest = json.loads((golden / "request-manifest.json").read_text())
    actual_manifest = json.loads((actual / "request-manifest.json").read_text())
    golden_manifest["artifacts"][0][field] = bad_value
    actual_manifest["artifacts"][0][field] = bad_value
    (golden / "request-manifest.json").write_text(json.dumps(golden_manifest))
    (actual / "request-manifest.json").write_text(json.dumps(actual_manifest))
    with pytest.raises(AssertionError, match=message):
        verify(db_path, golden, actual, specs=specs)


def test_verify_rejects_missing_and_duplicate_manifest_artifacts(tmp_path: Path) -> None:
    db_path = tmp_path / "contract.db"
    golden = tmp_path / "golden"
    actual = tmp_path / "actual"
    golden.mkdir()
    actual.mkdir()
    _create_db(db_path)
    specs = (HttpSpec("api.json", "/api", "api-json"),)
    (golden / "api.json").write_text('{"items":[1]}')
    (actual / "api.json").write_text('{"items":[1]}')
    _write_manifest(golden, specs)
    record_db(db_path, golden / "db-invariants.json")
    (actual / "request-manifest.json").write_text(json.dumps({"version": 1, "artifacts": []}))
    with pytest.raises(AssertionError, match="artifact set"):
        verify(db_path, golden, actual, specs=specs)

    entry = _manifest_entry(actual, specs[0], url="http://test/api")
    (actual / "request-manifest.json").write_text(json.dumps({"version": 1, "artifacts": [entry, entry]}))
    with pytest.raises(AssertionError, match="duplicate artifact"):
        verify(db_path, golden, actual, specs=specs)


def test_logical_db_invariant_covers_schema_content_order_and_wal(tmp_path: Path) -> None:
    db_path = tmp_path / "contract.db"
    reordered_path = tmp_path / "reordered.db"
    _create_db(db_path)
    _create_db(reordered_path, reverse=True)
    before = logical_db_invariant(db_path)
    assert logical_db_invariant(reordered_path) == before
    assert before["tables"]["typed"]["columns"] == [
        "key",
        "integer_value",
        "real_value",
        "blob_value",
        "nullable",
    ]

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("UPDATE samples SET value = 'beta' WHERE id = 1")
        conn.commit()
    finally:
        conn.close()

    after = logical_db_invariant(db_path)

    assert before["schema_sha256"] == after["schema_sha256"]
    assert before["overall_sha256"] != after["overall_sha256"]

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE typed SET integer_value = 9, real_value = 9.5, blob_value = ?, nullable = NULL WHERE key = 'beta'",
            (b"changed",),
        )
        conn.commit()
    finally:
        conn.close()
    typed_changed = logical_db_invariant(db_path)
    assert typed_changed["tables"]["typed"]["sha256"] != after["tables"]["typed"]["sha256"]
    assert typed_changed["overall_sha256"] != after["overall_sha256"]

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DELETE FROM typed WHERE key = 'alpha'")
        conn.commit()
    finally:
        conn.close()
    row_deleted = logical_db_invariant(db_path)
    assert row_deleted["tables"]["typed"]["rows"] == 1
    assert row_deleted["overall_sha256"] != typed_changed["overall_sha256"]

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE added (id INTEGER PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()
    schema_changed = logical_db_invariant(db_path)
    assert schema_changed["schema_sha256"] != after["schema_sha256"]
    assert "added" in schema_changed["tables"]

    Path(f"{db_path}-wal").write_bytes(b"pending")
    with pytest.raises(RuntimeError, match="WAL present"):
        logical_db_invariant(db_path)


def test_logical_db_invariant_uses_one_read_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "contract.db"
    _create_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    before = logical_db_invariant(db_path)

    from scripts import web_contract_golden

    original_rows = web_contract_golden._rows
    wrote = False

    def rows_with_concurrent_commit(
        reader: sqlite3.Connection,
        table: str,
        columns: list[str],
    ):
        nonlocal wrote
        yield from original_rows(reader, table, columns)
        if table == "samples" and not wrote:
            writer = sqlite3.connect(db_path)
            try:
                writer.execute("UPDATE typed SET integer_value = 99 WHERE key = 'alpha'")
                writer.commit()
            finally:
                writer.close()
            wrote = True

    monkeypatch.setattr(web_contract_golden, "_rows", rows_with_concurrent_commit)
    during_write = logical_db_invariant(db_path)
    monkeypatch.setattr(web_contract_golden, "_rows", original_rows)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    after = logical_db_invariant(db_path)

    assert wrote is True
    assert during_write == before
    assert after["overall_sha256"] != before["overall_sha256"]


def test_capture_rejects_optimized_empty_contract(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    code = (
        "from pathlib import Path; "
        "from scripts.web_contract_golden import capture; "
        "capture('http://test', Path(__import__('sys').argv[1]), 1, specs=())"
    )
    result = subprocess.run(
        [sys.executable, "-O", "-c", code, str(tmp_path)],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "at least one HTTP spec" in result.stderr


def test_capture_rejects_unsafe_artifacts_and_redirects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    victim = tmp_path / "victim.txt"
    victim.write_text("safe")
    with pytest.raises(ValueError, match="safe relative filename"):
        capture(
            "http://test",
            tmp_path / "out",
            concurrency=1,
            specs=(HttpSpec("../victim.txt", "/api", "api-json"),),
        )
    assert victim.read_text() == "safe"

    output = tmp_path / "out"
    output.mkdir()
    (output / "api.json").symlink_to(victim)
    response = _Response(b'{"items":[1]}', "application/json")

    def fake_urlopen(request: object, timeout: int) -> _Response:
        del timeout
        response.request_url = request.full_url  # type: ignore[attr-defined]
        return response

    monkeypatch.setattr("scripts.web_contract_golden.urlopen", fake_urlopen)
    with pytest.raises(ValueError, match="symbolic link"):
        capture(
            "http://test",
            output,
            concurrency=1,
            specs=(HttpSpec("api.json", "/api", "api-json"),),
        )
    assert victim.read_text() == "safe"

    (output / "api.json").unlink()
    redirected = _Response(
        b'{"items":[1]}',
        "application/json",
        final_url="http://test/login",
    )
    monkeypatch.setattr("scripts.web_contract_golden.urlopen", lambda request, timeout: redirected)
    with pytest.raises(AssertionError, match="redirected"):
        capture(
            "http://test",
            output,
            concurrency=1,
            specs=(HttpSpec("api.json", "/api", "api-json"),),
        )


def test_capture_rolls_back_if_publishing_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts import web_contract_golden

    output = tmp_path / "out"
    output.mkdir()
    old = {
        "a.json": b'{"old":"a"}',
        "b.json": b'{"old":"b"}',
        "request-manifest.json": b'{"old":"manifest"}',
    }
    for name, raw in old.items():
        (output / name).write_bytes(raw)
    specs = (
        HttpSpec("a.json", "/a", "api-json"),
        HttpSpec("b.json", "/b", "api-json"),
    )
    responses = {
        "http://test/a": _Response(b'{"new":"a"}', "application/json"),
        "http://test/b": _Response(b'{"new":"b"}', "application/json"),
    }

    def fake_urlopen(request: object, timeout: int) -> _Response:
        del timeout
        url = request.full_url  # type: ignore[attr-defined]
        responses[url].request_url = url
        return responses[url]

    monkeypatch.setattr(web_contract_golden, "urlopen", fake_urlopen)
    real_replace = web_contract_golden.os.replace
    calls = 0

    def fail_once(source: object, destination: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("publish failed")
        real_replace(source, destination)

    monkeypatch.setattr(web_contract_golden.os, "replace", fail_once)
    with pytest.raises(OSError, match="publish failed"):
        capture("http://test", output, concurrency=2, specs=specs)

    assert {name: (output / name).read_bytes() for name in old} == old
