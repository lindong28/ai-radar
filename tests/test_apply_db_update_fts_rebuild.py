"""FTS rebuild semantics and snapshot-authority regressions for server apply."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from airadar import db as airadar_db

REPO_ROOT = Path(__file__).resolve().parents[1]
APPLY_PATH = REPO_ROOT / "deploy" / "sync" / "apply_db_update.py"
spec = importlib.util.spec_from_file_location("apply_db_update_fts", APPLY_PATH)
assert spec is not None and spec.loader is not None
adu = importlib.util.module_from_spec(spec)
sys.modules["apply_db_update_fts"] = adu
spec.loader.exec_module(adu)

MANIFEST_PATH = REPO_ROOT / "deploy" / "sync" / "build_fts_manifest.py"
manifest_spec = importlib.util.spec_from_file_location("fts_manifest_fixture", MANIFEST_PATH)
assert manifest_spec is not None and manifest_spec.loader is not None
manifest_module = importlib.util.module_from_spec(manifest_spec)
manifest_spec.loader.exec_module(manifest_module)


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.failures: dict[str, int] = {}
        self.slot_active: dict[str, bool] = {"8000": True, "8001": True}

    def run(self, *argv: str, check: bool = True):  # noqa: ANN201
        self.calls.append(argv)
        joined = " ".join(argv)
        return_code = next(
            (code for needle, code in self.failures.items() if needle in joined), 0
        )
        if "is-active" in joined:
            port = joined.split("@")[1].split(".")[0]
            return_code = 0 if self.slot_active.get(port, False) else 3
        elif argv[0] == "curl" and "/api/v1/healthz" in joined:
            port = joined.rsplit(":", 1)[1].split("/")[0]
            return_code = 0 if self.slot_active.get(port, False) else 7
        elif return_code == 0 and "systemctl" in argv and "@" in joined:
            port = joined.split("@")[1].split(".")[0]
            if " stop " in f" {joined} ":
                self.slot_active[port] = False
            elif " restart " in f" {joined} ":
                self.slot_active[port] = True

        class Result:
            returncode = return_code
            stdout = ""
            stderr = ""

        return Result()

    def ok(self, *argv: str) -> bool:
        return self.run(*argv, check=False).returncode == 0


def _insert_source_and_item(conn: sqlite3.Connection, item_id: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO sources "
        "(id, name, url, tier, enabled, kind, synced_at) "
        "VALUES ('source', 'Source', 'https://example.invalid/feed', 'T1', 1, 'feed', "
        "'2026-01-01T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO items "
        "(id, source_id, url, title, content_text, content_hash, published_at, fetched_at) "
        "VALUES (?, 'source', ?, ?, 'body', ?, '2026-01-01T00:00:00Z', "
        "'2026-01-01T00:00:00Z')",
        (item_id, f"https://example.invalid/{item_id}", item_id, f"hash-{item_id}"),
    )


def _insert_enrich(
    conn: sqlite3.Connection,
    *,
    item_id: str,
    evaluated_at: str,
    output_json: str | None,
    error: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO item_evaluations "
        "(item_id, stage, ruleset_version, model_id, input_json, output_json, "
        "evaluated_at, error) VALUES (?, 'enrich', 'v1', 'model', '{}', ?, ?, ?)",
        (item_id, output_json, evaluated_at, error),
    )


def _insert_prefilter(
    conn: sqlite3.Connection,
    *,
    item_id: str,
    is_ai_related: bool,
) -> None:
    conn.execute(
        "INSERT INTO item_evaluations "
        "(item_id, stage, ruleset_version, model_id, input_json, numeric_json, "
        "evaluated_at, error) VALUES (?, 'prefilter', 'v1', 'model', '{}', ?, "
        "'2026-01-20T00:00:00Z', NULL)",
        (item_id, json.dumps({"is_ai_related": is_ai_related})),
    )


def _fts_title_zh(conn: sqlite3.Connection, item_id: str) -> str:
    row = conn.execute(
        "SELECT title_zh FROM items_fts WHERE item_id=?", (item_id,)
    ).fetchone()
    assert row is not None
    return str(row[0])


def _create_oracle_snapshot(path: Path) -> None:
    airadar_db.migrate(path)
    rows = [
        (
            "item-title",
            "source-common",
            "TitleOnlyBeacon",
            "plain body",
            "Generic Author",
            "通用译名甲",
        ),
        (
            "item-content",
            "source-common",
            "plain title",
            "ContentOnlyHarbor",
            "Generic Author",
            "通用译名乙",
        ),
        (
            "item-source",
            "source-exclusive",
            "plain title",
            "plain body",
            "Generic Author",
            "通用译名丙",
        ),
        (
            "item-author",
            "source-common",
            "plain title",
            "plain body",
            "AuthorOnlyQuartz",
            "通用译名丁",
        ),
        (
            "item-zh",
            "source-common",
            "plain title",
            "plain body",
            "Generic Author",
            "中文独有灯塔词",
        ),
    ]
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO sources (id, name, url, tier, enabled, kind, synced_at) "
            "VALUES ('source-common', 'Generic Source', 'https://example.invalid/common', "
            "'T1', 1, 'feed', '2026-01-01T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO sources (id, name, url, tier, enabled, kind, synced_at) "
            "VALUES ('source-exclusive', 'SourceOnlyCedar', "
            "'https://example.invalid/exclusive', 'T1', 1, 'feed', "
            "'2026-01-01T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO curation_runs (id, ruleset_version, weights_json, threshold, "
            "input_eval_ids, output_curated_ids, created_at) "
            "VALUES ('run', 'v1', '{}', 0.5, '[]', '[]', '2026-01-01T00:00:00Z')"
        )
        for rank, (item_id, source_id, title, body, author, title_zh) in enumerate(
            rows, start=1
        ):
            conn.execute(
                "INSERT INTO items (id, source_id, url, title, content_text, author, "
                "content_hash, published_at, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    item_id,
                    source_id,
                    f"https://example.invalid/{item_id}",
                    title,
                    body,
                    author,
                    f"hash-{item_id}",
                    f"2026-01-0{rank}T00:00:00Z",
                    f"2026-01-0{rank}T00:00:00Z",
                ),
            )
            _insert_enrich(
                conn,
                item_id=item_id,
                evaluated_at=f"2026-01-0{rank}T00:00:00Z",
                output_json=json.dumps({"title_zh": title_zh}, ensure_ascii=False),
            )
            conn.execute(
                "INSERT INTO curated_items "
                "(run_id, item_id, weighted_score, rank, reason_json) "
                "VALUES ('run', ?, 0.9, ?, '{}')",
                (item_id, rank),
            )
            _insert_prefilter(conn, item_id=item_id, is_ai_related=True)
        conn.execute(
            "INSERT INTO items (id, source_id, url, title, content_text, author, "
            "content_hash, published_at, fetched_at) VALUES "
            "('item-hidden', 'source-common', 'https://example.invalid/item-hidden', "
            "'TitleOnlyBeacon', 'plain body', 'Generic Author', 'hash-item-hidden', "
            "'2026-01-09T00:00:00Z', '2026-01-09T00:00:00Z')"
        )
        _insert_enrich(
            conn,
            item_id="item-hidden",
            evaluated_at="2026-01-09T00:00:00Z",
            output_json=json.dumps({"title_zh": "通用译名甲"}, ensure_ascii=False),
        )
        _insert_prefilter(conn, item_id="item-hidden", is_ai_related=False)
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def _strip_fts(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.executescript(
            """
            DROP TRIGGER IF EXISTS items_ai_fts;
            DROP TRIGGER IF EXISTS items_au_fts;
            DROP TRIGGER IF EXISTS items_ad_fts;
            DROP TRIGGER IF EXISTS sources_au_fts;
            DROP TRIGGER IF EXISTS enrich_ai_fts;
            DROP TABLE IF EXISTS items_fts;
            """
        )
        conn.commit()
    finally:
        conn.close()


def _stage_bundle(deploy: Any, root: Path) -> tuple[str, dict[str, object], Path]:
    primary = root / "primary-with-fts.db"
    _create_oracle_snapshot(primary)
    shutil.copyfile(primary, deploy.cfg.incoming)
    _strip_fts(deploy.cfg.incoming)
    snapshot_id = adu.snapshot_id_of(deploy.cfg.incoming)
    sidecar = deploy.cfg.data_dir / manifest_module.sidecar_name(snapshot_id)
    payload = manifest_module.build_manifest(
        snapshot=primary,
        artifact=deploy.cfg.incoming,
        output=sidecar,
    )
    return snapshot_id, payload, primary


def _make_deploy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data = tmp_path / "data"
    data.mkdir()
    active_conf = data / "nginx" / "active.conf"
    active_conf.parent.mkdir()
    active_conf.write_text("upstream ai_radar_active { server 127.0.0.1:8000; }\n")
    cfg = adu.Config(
        home=REPO_ROOT,
        data_dir=data,
        incoming=data / "radar.db.incoming",
        claimed=data / "radar.db.claimed",
        basis_dir=data / "basis",
        receipt=data / "accepted-snapshot.json",
        journal=data / "switch-journal.json",
        active_conf=active_conf,
        lock=data / ".deploy.lock",
        quarantine_dir=data / "quarantine",
        public_search_url="https://public.invalid/api/v1/timeline",
        nginx_link=data / "nginx-link.conf",
        health_wait_s=1,
    )
    cfg.nginx_link.symlink_to(cfg.active_conf)
    deploy = adu.Deploy(cfg, FakeRunner())
    monkeypatch.setattr(adu.Deploy, "free_mem_mb", lambda self: 4096)
    monkeypatch.setattr(adu.time, "sleep", lambda _seconds: None)
    cfg.slot_db("8000").write_bytes(b"old-serving-database")
    return deploy


def _install_search_oracle(
    deploy: Any,
    manifest: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    *,
    fail_new_public: bool = False,
) -> tuple[dict[str, dict[str, object]], list[tuple[str, str, str | None]]]:
    probes = manifest["probes"]
    assert isinstance(probes, dict)
    expected = {
        str(probe["term"]): dict(probe["timeline_http_matches"])
        for probe in probes.values()
    }
    old = {
        term: {"count": 1, "item_ids": [f"old-{index}"]}
        for index, term in enumerate(expected)
    }
    calls: list[tuple[str, str, str | None]] = []

    def fake_http(url: str, term: str) -> dict[str, object]:
        calls.append((url, term, deploy.active_port()))
        if url.startswith("http://127.0.0.1:8001/"):
            return expected[term]
        if deploy.active_port() == "8001":
            if fail_new_public:
                return {"count": 0, "item_ids": []}
            return expected[term]
        return old[term]

    monkeypatch.setattr(deploy, "_http_search_results", fake_http, raising=False)
    return old, calls


def _has_fts(path: Path) -> bool:
    with sqlite3.connect(path) as conn:
        return (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='items_fts'"
            ).fetchone()
            is not None
        )


def _failure_record(deploy: Any) -> tuple[dict[str, object], Path]:
    journal = json.loads(deploy.cfg.journal.read_text())
    failure_path = Path(journal["failure_path"])
    return json.loads(failure_path.read_text()), failure_path


def _failure_evidence(failure: dict[str, object], label: str) -> Path | None:
    evidence = failure["evidence"]
    assert isinstance(evidence, dict)
    value = evidence[label]
    return Path(value) if isinstance(value, str) else None


def test_snapshot_identity_is_the_full_sha256(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.db"
    artifact.write_bytes(b"base-only artifact")

    snapshot_id = adu.snapshot_id_of(artifact)

    assert len(snapshot_id) == 64
    assert snapshot_id == "5e87844447e4c8697b940dd49340714d4cf104cc41adbb771e731a96b772b9a7"


def test_rebuild_backfill_order_and_runtime_trigger_overwrite_are_distinct(
    tmp_path: Path,
) -> None:
    database = tmp_path / "candidate.db"
    airadar_db.migrate(database)
    with sqlite3.connect(database) as conn:
        _insert_source_and_item(conn, "order-disagreement")
        _insert_enrich(
            conn,
            item_id="order-disagreement",
            evaluated_at="2026-01-03T00:00:00Z",
            output_json='{"title_zh":"evaluated-at-wins"}',
        )
        _insert_enrich(
            conn,
            item_id="order-disagreement",
            evaluated_at="2026-01-01T00:00:00Z",
            output_json='{"title_zh":"later-insert-wins-at-runtime"}',
        )
        conn.commit()
        assert _fts_title_zh(conn, "order-disagreement") == "later-insert-wins-at-runtime"

    airadar_db.rebuild_fts(database)

    with sqlite3.connect(database) as conn:
        assert _fts_title_zh(conn, "order-disagreement") == "evaluated-at-wins"
        trigger_names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='trigger' AND sql LIKE '%items_fts%'"
            )
        }
    assert trigger_names == {
        "items_ai_fts",
        "items_au_fts",
        "items_ad_fts",
        "sources_au_fts",
        "enrich_ai_fts",
    }


def test_rebuild_title_zh_handles_failure_null_and_missing_payloads(tmp_path: Path) -> None:
    database = tmp_path / "candidate.db"
    airadar_db.migrate(database)
    with sqlite3.connect(database) as conn:
        for item_id in ("later-failure", "null-output", "missing-title"):
            _insert_source_and_item(conn, item_id)

        _insert_enrich(
            conn,
            item_id="later-failure",
            evaluated_at="2026-01-01T00:00:00Z",
            output_json='{"title_zh":"kept-after-failure"}',
        )
        _insert_enrich(
            conn,
            item_id="later-failure",
            evaluated_at="2026-01-04T00:00:00Z",
            output_json='{"title_zh":"must-not-win"}',
            error="provider failed",
        )
        _insert_enrich(
            conn,
            item_id="null-output",
            evaluated_at="2026-01-01T00:00:00Z",
            output_json='{"title_zh":"kept-after-null"}',
        )
        _insert_enrich(
            conn,
            item_id="null-output",
            evaluated_at="2026-01-04T00:00:00Z",
            output_json=None,
        )
        _insert_enrich(
            conn,
            item_id="missing-title",
            evaluated_at="2026-01-01T00:00:00Z",
            output_json='{"title_zh":"cleared-by-newer-payload"}',
        )
        _insert_enrich(
            conn,
            item_id="missing-title",
            evaluated_at="2026-01-04T00:00:00Z",
            output_json="{}",
        )
        conn.commit()

    airadar_db.rebuild_fts(database)

    with sqlite3.connect(database) as conn:
        assert _fts_title_zh(conn, "later-failure") == "kept-after-failure"
        assert _fts_title_zh(conn, "null-output") == "kept-after-null"
        assert _fts_title_zh(conn, "missing-title") == ""


def test_apply_keeps_base_immutable_and_commits_base_only_basis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deploy = _make_deploy(tmp_path, monkeypatch)
    snapshot_id, manifest, _primary = _stage_bundle(deploy, tmp_path)
    base_before = deploy.cfg.incoming.read_bytes()
    _install_search_oracle(deploy, manifest, monkeypatch)

    assert deploy.apply() == 0

    candidate = deploy.cfg.slot_db("8001")
    assert adu.snapshot_id_of(deploy.cfg.basis) == snapshot_id
    assert deploy.cfg.basis.read_bytes() == base_before
    assert not _has_fts(deploy.cfg.basis)
    assert _has_fts(candidate)
    assert adu.snapshot_id_of(candidate) != snapshot_id
    receipt = json.loads(deploy.cfg.receipt.read_text())
    assert receipt["snapshot_id"] == snapshot_id
    assert receipt["manifest_sha256"] == manifest["manifest_sha256"]


@pytest.mark.parametrize("sidecar_failure", ["missing", "corrupt", "identity-mismatch"])
def test_manifest_authority_failure_quarantines_without_switch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sidecar_failure: str,
) -> None:
    deploy = _make_deploy(tmp_path, monkeypatch)
    snapshot_id, manifest, _primary = _stage_bundle(deploy, tmp_path)
    deploy.cfg.slot_db("8001").write_bytes(b"previous inactive release")
    sidecar = deploy.cfg.data_dir / manifest_module.sidecar_name(snapshot_id)
    if sidecar_failure == "missing":
        sidecar.unlink()
    elif sidecar_failure == "corrupt":
        sidecar.write_text("not json")
    else:
        manifest["snapshot_id"] = "0" * 64
        manifest["manifest_sha256"] = manifest_module.manifest_self_hash(manifest)
        sidecar.write_bytes(manifest_module.canonical_manifest_bytes(manifest) + b"\n")

    with pytest.raises(adu.ApplyError, match="manifest"):
        deploy.apply()

    assert deploy.active_port() == "8000"
    failure, _failure_path = _failure_record(deploy)
    assert failure["snapshot_id"] == snapshot_id
    assert failure["phase"] == "manifest"
    evidence_status = failure["evidence_status"]
    assert isinstance(evidence_status, dict)
    assert evidence_status["candidate"] == "not-applicable"
    assert evidence_status["manifest"] == (
        "missing-at-failure" if sidecar_failure == "missing" else "captured"
    )
    assert not deploy.cfg.incoming.exists()
    assert not deploy.cfg.claimed.exists()
    assert deploy.cfg.slot_db("8001").read_bytes() == b"previous inactive release"


def test_manifest_probe_contract_corruption_quarantines_before_switch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deploy = _make_deploy(tmp_path, monkeypatch)
    snapshot_id, manifest, _primary = _stage_bundle(deploy, tmp_path)
    sidecar = deploy.cfg.data_dir / manifest_module.sidecar_name(snapshot_id)
    probe = manifest["probes"]["title"]
    del probe["timeline_http_matches"]
    manifest["manifest_sha256"] = manifest_module.manifest_self_hash(manifest)
    sidecar.write_bytes(manifest_module.canonical_manifest_bytes(manifest) + b"\n")

    with pytest.raises(adu.ApplyError, match="manifest validation.*probe title"):
        deploy.apply()

    assert deploy.active_port() == "8000"
    failure, _failure_path = _failure_record(deploy)
    assert failure["phase"] == "manifest"
    assert json.loads(deploy.cfg.journal.read_text())["state"] == "quarantined"
    assert not any("ai-radar-serve@8001" in " ".join(call) for call in deploy.r.calls)


def test_historical_v1_manifest_is_rejected_before_switch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deploy = _make_deploy(tmp_path, monkeypatch)
    snapshot_id, manifest, _primary = _stage_bundle(deploy, tmp_path)
    sidecar = deploy.cfg.data_dir / manifest_module.sidecar_name(snapshot_id)
    manifest["format_version"] = 1
    for probe in manifest["probes"].values():
        del probe["timeline_http_matches"]
    manifest["manifest_sha256"] = manifest_module.manifest_self_hash(manifest)
    sidecar.write_bytes(manifest_module.canonical_manifest_bytes(manifest) + b"\n")

    with pytest.raises(adu.ApplyError, match="unsupported manifest format_version"):
        deploy.apply()

    assert deploy.active_port() == "8000"
    failure, _failure_path = _failure_record(deploy)
    assert failure["phase"] == "manifest"
    assert json.loads(deploy.cfg.journal.read_text())["state"] == "quarantined"


def test_raw_codepoint_oracle_drift_quarantines_before_candidate_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deploy = _make_deploy(tmp_path, monkeypatch)
    snapshot_id, manifest, _primary = _stage_bundle(deploy, tmp_path)
    sidecar = deploy.cfg.data_dir / manifest_module.sidecar_name(snapshot_id)
    fts = manifest["fts"]
    assert isinstance(fts, dict)
    fts["sha256"] = "0" * 64
    manifest["manifest_sha256"] = manifest_module.manifest_self_hash(manifest)
    sidecar.write_bytes(manifest_module.canonical_manifest_bytes(manifest) + b"\n")

    with pytest.raises(adu.ApplyError, match="FTS.*digest|digest.*FTS"):
        deploy.apply()

    assert deploy.active_port() == "8000"
    assert not any("restart ai-radar-serve@8001" in " ".join(call) for call in deploy.r.calls)
    _failure, failure_path = _failure_record(deploy)
    assert failure_path.is_file()
    monkeypatch.setattr(
        deploy,
        "_materialize_and_verify_candidate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("deterministic failure was automatically retried")
        ),
    )
    assert deploy.apply() == 0


def test_crash_during_quarantine_replays_the_same_evidence_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deploy = _make_deploy(tmp_path, monkeypatch)
    snapshot_id, manifest, _primary = _stage_bundle(deploy, tmp_path)
    (deploy.cfg.data_dir / manifest_module.sidecar_name(snapshot_id)).unlink()
    original_move = deploy._move_to_quarantine
    moved = 0

    def crash_after_first_move(source: Path, destination: Path) -> Path:
        nonlocal moved
        result = original_move(source, destination)
        moved += 1
        if moved == 1:
            raise SystemExit("quarantine crash")
        return result

    with monkeypatch.context() as crash:
        crash.setattr(deploy, "_move_to_quarantine", crash_after_first_move)
        with pytest.raises(SystemExit, match="quarantine crash"):
            deploy.apply()

    intent = json.loads(deploy.cfg.journal.read_text())
    assert intent["state"] == "quarantining"
    base_destination = deploy._quarantine_destinations(
        snapshot_id, intent["quarantine"]["failure_id"]
    )["base"]
    assert base_destination.is_file()
    assert not deploy.cfg.claimed.exists()

    assert deploy.apply() == 0
    failure, failure_path = _failure_record(deploy)
    assert failure_path.is_file()
    assert _failure_evidence(failure, "base") == base_destination
    assert _failure_evidence(failure, "manifest") is None
    assert json.loads(deploy.cfg.journal.read_text())["state"] == "quarantined"

    with monkeypatch.context() as no_reverify:
        no_reverify.setattr(
            deploy,
            "_materialize_and_verify_candidate",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("terminal quarantine paid verifier cost again")
            ),
        )
        assert deploy.apply() == 0


def test_repeated_snapshot_failures_keep_distinct_bound_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deploy = _make_deploy(tmp_path, monkeypatch)
    deploy.cfg.claimed.write_bytes(b"same rejected base")
    snapshot_id = adu.snapshot_id_of(deploy.cfg.claimed)
    manifest_path = deploy.cfg.data_dir / manifest_module.sidecar_name(snapshot_id)
    manifest_path.write_bytes(b"bad manifest one")
    deploy.cfg.slot_db("8001").write_bytes(b"candidate one")

    deploy._quarantine(
        candidate="8001",
        snapshot_id=snapshot_id,
        manifest_sha256=None,
        phase="test",
        kind="deterministic-gate",
        message="first failure",
        retry_count=0,
    )
    first, first_path = _failure_record(deploy)

    deploy.cfg.claimed.write_bytes(b"same rejected base")
    manifest_path.write_bytes(b"bad manifest two")
    deploy.cfg.slot_db("8001").write_bytes(b"candidate two")
    deploy._quarantine(
        candidate="8001",
        snapshot_id=snapshot_id,
        manifest_sha256=None,
        phase="test",
        kind="deterministic-gate",
        message="second failure",
        retry_count=0,
    )
    second, second_path = _failure_record(deploy)

    assert first_path != second_path
    assert first_path.is_file() and second_path.is_file()
    assert first["failure_id"] != second["failure_id"]
    first_manifest = _failure_evidence(first, "manifest")
    second_manifest = _failure_evidence(second, "manifest")
    assert first_manifest is not None and first_manifest.read_bytes() == b"bad manifest one"
    assert second_manifest is not None and second_manifest.read_bytes() == b"bad manifest two"


def test_expected_but_missing_candidate_has_explicit_evidence_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deploy = _make_deploy(tmp_path, monkeypatch)
    deploy.cfg.claimed.write_bytes(b"rejected base")
    snapshot_id = adu.snapshot_id_of(deploy.cfg.claimed)
    manifest_path = deploy.cfg.data_dir / manifest_module.sidecar_name(snapshot_id)
    manifest_path.write_bytes(b"invalid manifest evidence")
    deploy.cfg.slot_db("8001").unlink(missing_ok=True)

    deploy._quarantine(
        candidate="8001",
        snapshot_id=snapshot_id,
        manifest_sha256=None,
        phase="test",
        kind="deterministic-gate",
        message="candidate disappeared",
        retry_count=0,
        include_candidate=True,
    )

    failure, _failure_path = _failure_record(deploy)
    evidence_status = failure["evidence_status"]
    assert isinstance(evidence_status, dict)
    assert evidence_status["candidate"] == "missing-at-failure"


def test_candidate_http_gate_failure_quarantines_before_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deploy = _make_deploy(tmp_path, monkeypatch)
    snapshot_id, manifest, _primary = _stage_bundle(deploy, tmp_path)
    _install_search_oracle(deploy, manifest, monkeypatch)
    expected_http = deploy._http_search_results

    def fail_candidate(url: str, term: str) -> dict[str, object]:
        if url.startswith("http://127.0.0.1:8001/"):
            return {"count": 0, "item_ids": []}
        return expected_http(url, term)

    monkeypatch.setattr(deploy, "_http_search_results", fail_candidate)

    with pytest.raises(adu.ApplyError, match="candidate-slot"):
        deploy.apply()

    assert deploy.active_port() == "8000"
    assert json.loads(deploy.cfg.journal.read_text())["state"] == "quarantined"
    failure, _failure_path = _failure_record(deploy)
    candidate_evidence = _failure_evidence(failure, "candidate")
    assert candidate_evidence is not None and candidate_evidence.is_file()
    joined = [" ".join(call) for call in deploy.r.calls]
    assert any("disable ai-radar-serve@8001" in call for call in joined)
    assert any("stop ai-radar-serve@8001" in call for call in joined)
    assert not any("nginx -s reload" in call for call in joined)


def test_http_gate_uses_visibility_aware_expectations_and_rejects_raw_sets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deploy = _make_deploy(tmp_path, monkeypatch)
    _snapshot_id, manifest, _primary = _stage_bundle(deploy, tmp_path)
    probes = manifest["probes"]
    assert isinstance(probes, dict)
    differing = next(
        probe
        for probe in probes.values()
        if probe["timeline_http_matches"] != probe["unqualified_matches"]
    )
    http_by_term = {
        str(probe["term"]): dict(probe["timeline_http_matches"])
        for probe in probes.values()
    }
    monkeypatch.setattr(
        deploy,
        "_http_search_results",
        lambda _url, term: http_by_term[term],
    )

    deploy._verify_http_against_manifest(
        "http://127.0.0.1:8001/api/v1/timeline",
        manifest,
        vantage="candidate-slot",
    )

    raw_term = str(differing["term"])
    raw_results = dict(differing["unqualified_matches"])
    monkeypatch.setattr(
        deploy,
        "_http_search_results",
        lambda _url, term: raw_results if term == raw_term else http_by_term[term],
    )
    with pytest.raises(adu.ApplyError, match="candidate-slot"):
        deploy._verify_http_against_manifest(
            "http://127.0.0.1:8001/api/v1/timeline",
            manifest,
            vantage="candidate-slot",
        )


@pytest.mark.parametrize("gate", ["candidate", "post-switch", "rollback-baseline"])
def test_http_gate_warms_once_then_judges_with_configured_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    gate: str,
) -> None:
    deploy = _make_deploy(tmp_path, monkeypatch)
    deploy.cfg.http_probe_timeout_s = 37
    probes = {
        field: {
            "term": f"term-{field}",
            "timeline_http_matches": {"count": 1, "item_ids": [f"item-{field}"]},
        }
        for field in adu.SEARCH_FIELDS
    }
    manifest = {"probes": probes}

    class SlowThenCorrectRunner(FakeRunner):
        def __init__(self) -> None:
            super().__init__()
            self.search_calls = 0

        def run(self, *argv: str, check: bool = True):  # noqa: ANN201
            if argv[0] != "curl" or "/api/v1/timeline" not in argv[-1]:
                return super().run(*argv, check=check)
            self.calls.append(argv)
            self.search_calls += 1
            is_warmup = self.search_calls % 2 == 1
            term = parse_qs(urlparse(argv[-1]).query)["q"][0]
            field = term.removeprefix("term-")

            class Result:
                returncode = 28 if is_warmup else 0
                stdout = "" if is_warmup else json.dumps(
                    {
                        "success": True,
                        "data": {
                            "total": 1,
                            "items": [{"id": f"item-{field}"}],
                        },
                    }
                )
                stderr = "cold timeout" if is_warmup else ""

            return Result()

    runner = SlowThenCorrectRunner()
    deploy.r = runner
    api_url = "http://127.0.0.1:18000/api/v1/timeline"

    if gate == "candidate":
        deploy._verify_http_against_manifest(api_url, manifest, vantage="candidate-slot")
    elif gate == "post-switch":
        deploy.cfg.public_search_url = api_url
        deploy._verify_public_against_manifest(manifest)
    else:
        rollback = {
            "previous_serving_public_results": {
                field: {
                    "term": probe["term"],
                    "result": probe["timeline_http_matches"],
                }
                for field, probe in probes.items()
            }
        }
        deploy._verify_baseline_at_url(rollback, api_url, vantage="rollback public")

    assert runner.search_calls == len(adu.SEARCH_FIELDS) * 2
    for call in runner.calls:
        if call[0] == "curl" and "/api/v1/timeline" in call[-1]:
            timeout_at = call.index("--max-time")
            assert call[timeout_at + 1] == "37"


def test_http_verifier_paces_warmup_and_judged_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deploy = _make_deploy(tmp_path, monkeypatch)
    deploy.cfg.http_probe_interval_s = 1.25
    sleeps: list[float] = []
    monkeypatch.setattr(adu.time, "sleep", sleeps.append)

    class SuccessfulRunner(FakeRunner):
        def run(self, *argv: str, check: bool = True):  # noqa: ANN201
            if argv[0] != "curl" or "/api/v1/timeline" not in argv[-1]:
                return super().run(*argv, check=check)
            self.calls.append(argv)

            class Result:
                returncode = 0
                stdout = json.dumps(
                    {"success": True, "data": {"total": 1, "items": [{"id": "item"}]}}
                )
                stderr = ""

            return Result()

    deploy.r = SuccessfulRunner()

    assert deploy._http_search_results(
        "http://127.0.0.1:18000/api/v1/timeline", "term"
    ) == {"count": 1, "item_ids": ["item"]}
    assert sleeps == [1.25, 1.25]


def test_route_proof_selects_canonical_vhost_instead_of_default_vhost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deploy = _make_deploy(tmp_path, monkeypatch)
    seen_hosts: list[str] = []

    class TwoVhostHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            host = self.headers.get("Host", "")
            seen_hosts.append(host)
            item_id = "canonical" if host.split(":", 1)[0] == "news.example.invalid" else "default"
            body = json.dumps(
                {"success": True, "data": {"total": 1, "items": [{"id": item_id}]}}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), TwoVhostHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    loopback_url = f"http://127.0.0.1:{port}/api/v1/timeline"
    deploy.cfg.route_proof_search_url = loopback_url
    deploy.cfg.public_search_url = (
        f"http://news.example.invalid:{port}/api/v1/timeline"
    )
    deploy.cfg.http_probe_interval_s = 0
    deploy.r = adu.Runner()
    try:
        plain = subprocess.run(
            [
                "curl",
                "-sS",
                "--noproxy",
                "*",
                deploy._http_search_url(loopback_url, "term", 1),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        assert json.loads(plain.stdout)["data"]["items"] == [{"id": "default"}]

        assert deploy._http_search_results(loopback_url, "term") == {
            "count": 1,
            "item_ids": ["canonical"],
        }
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert any(host.startswith("127.0.0.1:") for host in seen_hosts)
    assert seen_hosts[-2:] == [
        f"news.example.invalid:{port}",
        f"news.example.invalid:{port}",
    ]


def test_route_proof_preserves_public_https_authority_and_maps_only_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deploy = _make_deploy(tmp_path, monkeypatch)
    deploy.cfg.public_search_url = (
        "https://news.example.invalid:8443/api/v1/timeline"
    )
    deploy.cfg.route_proof_search_url = (
        "https://127.0.0.1:9443/api/v1/timeline"
    )

    args = deploy._curl_search_args(
        deploy.cfg.route_proof_search_url, "term", 2
    )

    assert args[:4] == (
        "--noproxy",
        "*",
        "--connect-to",
        "news.example.invalid:8443:127.0.0.1:9443",
    )
    assert args[-1].startswith(
        "https://news.example.invalid:8443/api/v1/timeline?"
    )
    assert "page=2" in args[-1]


def test_route_proof_rejects_transport_semantics_that_differ_from_public_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deploy = _make_deploy(tmp_path, monkeypatch)
    deploy.cfg.public_search_url = (
        "https://news.example.invalid/api/v1/timeline"
    )
    deploy.cfg.route_proof_search_url = (
        "http://127.0.0.1/api/v1/timeline"
    )

    with pytest.raises(adu.ApplyError, match="scheme must match"):
        deploy._curl_search_args(deploy.cfg.route_proof_search_url, "term", 1)


def test_judged_wrong_http_results_still_quarantine_after_warmup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deploy = _make_deploy(tmp_path, monkeypatch)
    _snapshot_id, _manifest, _primary = _stage_bundle(deploy, tmp_path)

    class WarmThenWrongRunner(FakeRunner):
        def __init__(self) -> None:
            super().__init__()
            self.search_calls = 0

        def run(self, *argv: str, check: bool = True):  # noqa: ANN201
            if argv[0] != "curl" or "/api/v1/timeline" not in argv[-1]:
                return super().run(*argv, check=check)
            self.calls.append(argv)
            self.search_calls += 1

            class Result:
                returncode = 0
                stdout = (
                    "not verdict JSON"
                    if self.search_calls == 1
                    else json.dumps(
                        {"success": True, "data": {"total": 0, "items": []}}
                    )
                )
                stderr = ""

            return Result()

    runner = WarmThenWrongRunner()
    deploy.r = runner

    with pytest.raises(adu.ApplyError, match="candidate-slot"):
        deploy.apply()

    assert runner.search_calls == 2
    assert deploy.active_port() == "8000"
    assert json.loads(deploy.cfg.journal.read_text())["state"] == "quarantined"


def test_candidate_judged_http_timeout_keeps_prepared_retry_transient(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deploy = _make_deploy(tmp_path, monkeypatch)
    snapshot_id, _manifest, _primary = _stage_bundle(deploy, tmp_path)

    class WarmThenTimeoutRunner(FakeRunner):
        def __init__(self) -> None:
            super().__init__()
            self.search_calls = 0

        def run(self, *argv: str, check: bool = True):  # noqa: ANN201
            if argv[0] != "curl" or "/api/v1/timeline" not in argv[-1]:
                return super().run(*argv, check=check)
            self.calls.append(argv)
            self.search_calls += 1

            class Result:
                returncode = 0 if self.search_calls == 1 else 28
                stdout = "warm-up ignored"
                stderr = "" if self.search_calls == 1 else "judged timeout"

            return Result()

    runner = WarmThenTimeoutRunner()
    deploy.r = runner

    with pytest.raises(adu.ApplyError, match="prepared retry retained"):
        deploy.apply()

    journal = json.loads(deploy.cfg.journal.read_text())
    assert runner.search_calls == 2
    assert journal["state"] == "prepared"
    assert journal["last_failure_category"] == "candidate-http-infrastructure-failed"
    assert journal["automatic_fresh_rebuild_retries_used"] == 0
    assert journal["snapshot_id"] == snapshot_id
    assert deploy.active_port() == "8000"
    assert deploy.cfg.claimed.is_file()


def test_pre_switch_http_timeout_keeps_prepared_retry_transient(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deploy = _make_deploy(tmp_path, monkeypatch)
    snapshot_id, manifest, _primary = _stage_bundle(deploy, tmp_path)
    _install_search_oracle(deploy, manifest, monkeypatch)
    monkeypatch.setattr(
        deploy,
        "_verify_route_proof_baseline",
        lambda _rollback: (_ for _ in ()).throw(
            adu.ApplyError("search HTTP probe failed rc=28")
        ),
    )

    with pytest.raises(adu.ApplyError, match="prepared retry retained"):
        deploy.apply()

    journal = json.loads(deploy.cfg.journal.read_text())
    assert journal["state"] == "prepared"
    assert journal["last_failure_category"] == "pre-switch-preparation-failed"
    assert journal["automatic_fresh_rebuild_retries_used"] == 0
    assert journal["snapshot_id"] == snapshot_id
    assert deploy.active_port() == "8000"
    assert deploy.cfg.claimed.is_file()


@pytest.mark.parametrize("checkpoint_identity", ["fts-apply-v2", "fts-apply-v3"])
def test_older_prepared_checkpoint_blocks_under_v4_without_migration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    checkpoint_identity: str,
) -> None:
    deploy = _make_deploy(tmp_path, monkeypatch)
    snapshot_id, manifest, _primary = _stage_bundle(deploy, tmp_path)
    deploy.cfg.incoming.rename(deploy.cfg.claimed)
    deploy.journal_write(
        "prepared",
        "8001",
        snapshot_id,
        manifest_sha256=manifest["manifest_sha256"],
        retry_count=0,
        verifier_identity=checkpoint_identity,
        last_failure_category="pre-switch-preparation-failed",
        last_failure_message="cold HTTP query exceeded the old timeout",
        last_failure_at="2026-08-10T00:40:18Z",
    )
    monkeypatch.setattr(
        deploy,
        "_continue_release",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("older prepared checkpoint resumed under verifier v4")
        ),
    )

    with pytest.raises(adu.ApplyError, match="verifier identity changed"):
        deploy.apply()

    blocked = json.loads(deploy.cfg.journal.read_text())
    assert blocked["state"] == "retry_blocked_verifier_changed"
    assert blocked["verifier_identity"] == checkpoint_identity
    assert blocked["observed_verifier_identity"] == "fts-apply-v4"
    assert blocked["recovery_action"] == "manual-intervention"
    assert blocked["automatic_fresh_rebuild_retries_used"] == 0


def test_deterministic_sqlite_rebuild_error_quarantines_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deploy = _make_deploy(tmp_path, monkeypatch)
    _snapshot_id, _manifest, _primary = _stage_bundle(deploy, tmp_path)

    def fail_rebuild(_path: Path) -> None:
        raise sqlite3.OperationalError("injected deterministic rebuild SQL failure")

    monkeypatch.setattr(airadar_db, "rebuild_fts", fail_rebuild)

    with pytest.raises(adu.ApplyError, match="deterministic rebuild SQL failure"):
        deploy.apply()

    assert deploy.active_port() == "8000"
    assert json.loads(deploy.cfg.journal.read_text())["state"] == "quarantined"
    failure, _failure_path = _failure_record(deploy)
    assert failure["failure_category"] == "deterministic-gate"
    assert failure["automatic_retries_used"] == 0
    assert failure["automatic_retry_disposition"] == "not-eligible"
    candidate_evidence = _failure_evidence(failure, "candidate")
    assert candidate_evidence is not None and candidate_evidence.is_file()

    monkeypatch.setattr(
        airadar_db,
        "rebuild_fts",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("quarantined deterministic failure was retried")
        ),
    )
    assert deploy.apply() == 0


def test_http_gate_failure_stays_quarantine_bound_when_retirement_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deploy = _make_deploy(tmp_path, monkeypatch)
    _snapshot_id, manifest, _primary = _stage_bundle(deploy, tmp_path)
    _install_search_oracle(deploy, manifest, monkeypatch)
    expected_http = deploy._http_search_results

    def fail_candidate(url: str, term: str) -> dict[str, object]:
        if url.startswith("http://127.0.0.1:8001/"):
            return {"count": 0, "item_ids": []}
        return expected_http(url, term)

    monkeypatch.setattr(deploy, "_http_search_results", fail_candidate)
    deploy.r.failures["systemctl disable ai-radar-serve@8001.service"] = 1

    with pytest.raises(adu.ApplyError, match="candidate could not be retired"):
        deploy.apply()

    journal = json.loads(deploy.cfg.journal.read_text())
    assert journal["state"] == "quarantining"
    intent = journal["quarantine"]
    assert intent["failure_category"] == "deterministic-gate"
    assert "candidate-slot" in intent["message"]
    assert "could not disable candidate" in intent["candidate_retirement_failure"]
    assert deploy.active_port() == "8000"
    assert deploy.cfg.slot_db("8001").is_file()

    malformed = {**journal, "quarantine": dict(intent)}
    malformed["quarantine"].pop("retire_candidate_before_capture")
    with pytest.raises(adu.ApplyError, match="invalid retirement intent"):
        deploy._complete_quarantine(malformed)

    deploy.r.failures.clear()
    monkeypatch.setattr(
        deploy,
        "_materialize_and_verify_candidate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("known deterministic HTTP failure re-entered rebuild")
        ),
    )
    assert deploy.apply() == 0
    assert json.loads(deploy.cfg.journal.read_text())["state"] == "quarantined"
    failure, _failure_path = _failure_record(deploy)
    assert failure["failure_category"] == "deterministic-gate"
    retirement_failure = failure["candidate_retirement_failure"]
    assert isinstance(retirement_failure, str)
    assert "could not disable candidate" in retirement_failure
    assert deploy.active_port() == "8000"


@pytest.mark.parametrize("crash_point", ["before", "during", "after"])
def test_first_rebuild_crash_gets_one_fresh_retry_from_original_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    crash_point: str,
) -> None:
    deploy = _make_deploy(tmp_path, monkeypatch)
    snapshot_id, manifest, _primary = _stage_bundle(deploy, tmp_path)
    base_before = deploy.cfg.incoming.read_bytes()
    _install_search_oracle(deploy, manifest, monkeypatch)

    with monkeypatch.context() as crash:
        if crash_point == "before":
            crash.setattr(
                deploy,
                "_materialize_and_verify_candidate",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(SystemExit("before")),
                raising=False,
            )
        elif crash_point == "during":
            crash.setattr(
                airadar_db,
                "rebuild_fts",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(SystemExit("during")),
            )
        else:
            crash.setattr(
                deploy,
                "_verify_candidate_fts",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(SystemExit("after")),
                raising=False,
            )
        with pytest.raises(SystemExit, match=crash_point):
            deploy.apply()

    assert deploy.active_port() == "8000"
    assert adu.snapshot_id_of(deploy.cfg.claimed) == snapshot_id
    assert deploy.cfg.claimed.read_bytes() == base_before
    assert json.loads(deploy.cfg.journal.read_text())["state"] == "rebuilding"

    assert deploy.apply() == 0
    assert deploy.active_port() == "8001"
    assert adu.snapshot_id_of(deploy.cfg.basis) == snapshot_id
    assert not _has_fts(deploy.cfg.basis)


def test_verifier_identity_change_blocks_automatic_fresh_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deploy = _make_deploy(tmp_path, monkeypatch)
    _snapshot_id, _manifest, _primary = _stage_bundle(deploy, tmp_path)

    with monkeypatch.context() as crash:
        crash.setattr(
            deploy,
            "_materialize_and_verify_candidate",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                SystemExit("checkpoint before verifier rollout")
            ),
        )
        with pytest.raises(SystemExit, match="checkpoint before verifier rollout"):
            deploy.apply()

    checkpoint = json.loads(deploy.cfg.journal.read_text())
    checkpoint_identity = checkpoint["verifier_identity"]
    assert checkpoint["state"] == "rebuilding"
    monkeypatch.setattr(adu, "VERIFIER_VERSION", "fts-apply-v999")
    monkeypatch.setattr(
        deploy,
        "_materialize_and_verify_candidate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("verifier-changed snapshot was automatically retried")
        ),
    )

    with pytest.raises(adu.ApplyError, match="verifier identity changed"):
        deploy.apply()

    blocked = json.loads(deploy.cfg.journal.read_text())
    assert blocked["state"] == "retry_blocked_verifier_changed"
    assert blocked["recovery_action"] == "manual-intervention"
    assert blocked["verifier_identity"] == checkpoint_identity
    assert blocked["observed_verifier_identity"] == "fts-apply-v999"
    assert blocked["automatic_fresh_rebuild_retries_used"] == 0
    assert deploy.active_port() == "8000"

    with pytest.raises(adu.ApplyError, match="verifier identity changed"):
        deploy.apply()
    assert json.loads(deploy.cfg.journal.read_text()) == blocked


def test_second_rebuild_crash_quarantines_and_timer_does_not_reverify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deploy = _make_deploy(tmp_path, monkeypatch)
    snapshot_id, manifest, _primary = _stage_bundle(deploy, tmp_path)
    _install_search_oracle(deploy, manifest, monkeypatch)

    def crash(*_args: object, **_kwargs: object) -> None:
        raise SystemExit("crash")

    with monkeypatch.context() as first:
        first.setattr(deploy, "_materialize_and_verify_candidate", crash, raising=False)
        with pytest.raises(SystemExit):
            deploy.apply()
    with monkeypatch.context() as second:
        second.setattr(deploy, "_materialize_and_verify_candidate", crash, raising=False)
        with pytest.raises(SystemExit):
            deploy.apply()

    with monkeypatch.context() as third:
        third.setattr(
            deploy,
            "_materialize_and_verify_candidate",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("quarantined snapshot was reverified")
            ),
            raising=False,
        )
        assert deploy.apply() == 0
        assert deploy.apply() == 0

    assert deploy.active_port() == "8000"
    failure, _failure_path = _failure_record(deploy)
    assert failure["failure_category"] == "retry-exhausted"
    assert failure["automatic_retries_used"] == 1


def test_post_switch_consumer_failure_rolls_back_and_preserves_old_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deploy = _make_deploy(tmp_path, monkeypatch)
    snapshot_id, manifest, _primary = _stage_bundle(deploy, tmp_path)
    old_basis = b"old-basis"
    old_receipt = b'{"snapshot_id":"legacy-value","port":"8000"}'
    deploy.cfg.basis_dir.mkdir()
    deploy.cfg.basis.write_bytes(old_basis)
    deploy.cfg.receipt.write_bytes(old_receipt)
    old_results, calls = _install_search_oracle(
        deploy, manifest, monkeypatch, fail_new_public=True
    )

    with pytest.raises(adu.ApplyError, match="consumer"):
        deploy.apply()

    assert deploy.active_port() == "8000"
    assert deploy.cfg.basis.read_bytes() == old_basis
    assert deploy.cfg.receipt.read_bytes() == old_receipt
    assert deploy.cfg.slot_db("8000").read_bytes() == b"old-serving-database"
    failure, _failure_path = _failure_record(deploy)
    candidate_evidence = _failure_evidence(failure, "candidate")
    assert candidate_evidence is not None and candidate_evidence.is_file()
    assert json.loads(deploy.cfg.journal.read_text())["state"] == "quarantined"
    restored_public_terms = [
        term
        for url, term, active_port in calls
        if url == deploy.cfg.public_search_url and active_port == "8000"
    ]
    assert all(restored_public_terms.count(term) >= 2 for term in old_results)
    joined = [" ".join(call) for call in deploy.r.calls]
    assert any("disable ai-radar-serve@8001" in call for call in joined)
    assert any("stop ai-radar-serve@8001" in call for call in joined)


def test_identical_old_results_cannot_mask_a_broken_nginx_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deploy = _make_deploy(tmp_path, monkeypatch)
    _snapshot_id, manifest, _primary = _stage_bundle(deploy, tmp_path)
    probes = manifest["probes"]
    assert isinstance(probes, dict)
    expected = {
        str(probe["term"]): dict(probe["timeline_http_matches"])
        for probe in probes.values()
    }
    route_proof_old_active: list[bool] = []

    def stale_old_route(url: str, term: str) -> dict[str, object]:
        if url.startswith("http://127.0.0.1:8001/"):
            return expected[term]
        if url == deploy.cfg.route_proof_search_url:
            route_proof_old_active.append(deploy.r.slot_active["8000"])
            if not deploy.r.slot_active["8000"]:
                raise adu.ApplyError("loopback nginx still points at stopped old slot")
        # Old and new semantic results are deliberately indistinguishable.
        return expected[term]

    monkeypatch.setattr(deploy, "_http_search_results", stale_old_route)

    with pytest.raises(adu.ApplyError, match="route identity gate failed"):
        deploy.apply()

    assert False in route_proof_old_active
    assert deploy.active_port() == "8000"
    assert deploy.r.slot_active["8000"] is True
    assert not deploy.cfg.basis.exists()
    assert not deploy.cfg.receipt.exists()
    failure, _failure_path = _failure_record(deploy)
    assert failure["failure_category"] == "route-identity-gate-failed"
    assert _failure_evidence(failure, "candidate") is not None


def test_crash_after_old_stop_recovers_old_release_and_quarantines_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deploy = _make_deploy(tmp_path, monkeypatch)
    _snapshot_id, manifest, _primary = _stage_bundle(deploy, tmp_path)
    deploy.cfg.basis_dir.mkdir()
    deploy.cfg.basis.write_bytes(b"old-basis")
    deploy.cfg.receipt.write_bytes(b"old-receipt")
    _install_search_oracle(deploy, manifest, monkeypatch)

    def crash_route_proof(_manifest: dict[str, object]) -> None:
        assert deploy.r.slot_active["8000"] is False
        raise SystemExit("after old stop")

    with monkeypatch.context() as crash:
        crash.setattr(
            deploy,
            "_verify_route_proof_against_manifest",
            crash_route_proof,
        )
        with pytest.raises(SystemExit, match="after old stop"):
            deploy.apply()

    assert deploy.active_port() == "8001"
    assert deploy.r.slot_active["8000"] is False
    assert json.loads(deploy.cfg.journal.read_text())["state"] == (
        "old_stopping_pending_consumer"
    )

    assert deploy.apply() == 0
    assert deploy.active_port() == "8000"
    assert deploy.r.slot_active["8000"] is True
    assert deploy.cfg.basis.read_bytes() == b"old-basis"
    assert deploy.cfg.receipt.read_bytes() == b"old-receipt"
    failure, _failure_path = _failure_record(deploy)
    assert _failure_evidence(failure, "candidate") is not None


def test_post_switch_rollback_retirement_failure_does_not_move_live_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deploy = _make_deploy(tmp_path, monkeypatch)
    snapshot_id, manifest, _primary = _stage_bundle(deploy, tmp_path)
    deploy.cfg.basis_dir.mkdir()
    deploy.cfg.basis.write_bytes(b"old-basis")
    deploy.cfg.receipt.write_bytes(b"old-receipt")
    _install_search_oracle(deploy, manifest, monkeypatch, fail_new_public=True)
    deploy.r.failures["stop ai-radar-serve@8001"] = 1

    with pytest.raises(adu.ApplyError, match="rollback failed"):
        deploy.apply()

    assert deploy.active_port() == "8000"
    assert json.loads(deploy.cfg.journal.read_text())["state"] == "rollback_failed"
    assert deploy.cfg.slot_db("8001").is_file()
    assert not list((deploy.cfg.quarantine_dir / snapshot_id).glob("candidate.*.db"))
    assert deploy.cfg.basis.read_bytes() == b"old-basis"
    assert deploy.cfg.receipt.read_bytes() == b"old-receipt"

    deploy.r.failures.clear()
    assert deploy.apply() == 0
    assert json.loads(deploy.cfg.journal.read_text())["state"] == "quarantined"
    failure, _failure_path = _failure_record(deploy)
    candidate_evidence = _failure_evidence(failure, "candidate")
    assert candidate_evidence is not None and candidate_evidence.is_file()


def test_rollback_keeps_candidate_serving_during_nginx_drain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deploy = _make_deploy(tmp_path, monkeypatch)
    _snapshot_id, manifest, _primary = _stage_bundle(deploy, tmp_path)
    _install_search_oracle(deploy, manifest, monkeypatch, fail_new_public=True)
    deploy.cfg.nginx_rollback_drain_s = 93.0
    events: list[tuple[str, str | bool, bool]] = []
    drain_sleeps: list[float] = []
    original_switch = deploy._switch_include
    original_retire = deploy._retire_candidate
    original_public = deploy._verify_public_baseline
    original_route = deploy._verify_route_proof_baseline

    def recording_switch(port: str) -> None:
        original_switch(port)
        events.append(("switch", port, deploy.r.slot_active["8001"]))

    def recording_retire(port: str) -> None:
        events.append(("retire", port, deploy.r.slot_active["8001"]))
        original_retire(port)

    def recording_public(rollback: dict[str, object]) -> None:
        original_public(rollback)
        events.append(("verify-public", True, deploy.r.slot_active["8001"]))

    def recording_route(rollback: dict[str, object]) -> None:
        original_route(rollback)
        events.append(("verify-route", True, deploy.r.slot_active["8001"]))

    def recording_sleep(seconds: float) -> None:
        if seconds > 90:
            drain_sleeps.append(seconds)
            events.append(("drain", True, deploy.r.slot_active["8001"]))

    monkeypatch.setattr(deploy, "_switch_include", recording_switch)
    monkeypatch.setattr(deploy, "_retire_candidate", recording_retire)
    monkeypatch.setattr(deploy, "_verify_public_baseline", recording_public)
    monkeypatch.setattr(deploy, "_verify_route_proof_baseline", recording_route)
    monkeypatch.setattr(adu.time, "sleep", recording_sleep)

    with pytest.raises(adu.ApplyError, match="consumer gate failed"):
        deploy.apply()

    rollback_switch = events.index(("switch", "8000", True))
    rollback_events = events[rollback_switch + 1 :]
    verify_public = rollback_events.index(("verify-public", True, True))
    verify_route = rollback_events.index(("verify-route", True, True))
    drain = rollback_events.index(("drain", True, True))
    retirement = rollback_events.index(("retire", "8001", True))
    assert verify_public < verify_route < drain < retirement
    assert drain_sleeps == [pytest.approx(93.0, abs=0.1)]


def test_rollback_validation_failure_is_not_delayed_by_nginx_drain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deploy = _make_deploy(tmp_path, monkeypatch)
    _snapshot_id, manifest, _primary = _stage_bundle(deploy, tmp_path)
    _install_search_oracle(deploy, manifest, monkeypatch, fail_new_public=True)
    deploy.cfg.nginx_rollback_drain_s = 93.0
    sleeps: list[float] = []

    def fail_rollback_public(_rollback: dict[str, object]) -> None:
        raise adu.ApplyError("old public baseline is unhealthy")

    monkeypatch.setattr(deploy, "_verify_public_baseline", fail_rollback_public)
    monkeypatch.setattr(adu.time, "sleep", sleeps.append)

    with pytest.raises(adu.ApplyError, match="rollback failed"):
        deploy.apply()

    assert deploy.active_port() == "8000"
    assert deploy.r.slot_active["8001"] is True
    assert json.loads(deploy.cfg.journal.read_text())["state"] == "rollback_failed"
    assert not any(seconds > 90 for seconds in sleeps)


def test_crash_after_switch_before_consumer_gate_rolls_back_on_reconcile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deploy = _make_deploy(tmp_path, monkeypatch)
    snapshot_id, manifest, _primary = _stage_bundle(deploy, tmp_path)
    deploy.cfg.basis_dir.mkdir()
    deploy.cfg.basis.write_bytes(b"old-basis")
    deploy.cfg.receipt.write_bytes(b"old-receipt")
    _install_search_oracle(deploy, manifest, monkeypatch)

    with monkeypatch.context() as crash:
        crash.setattr(
            deploy,
            "_verify_public_against_manifest",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(SystemExit("post-switch")),
            raising=False,
        )
        with pytest.raises(SystemExit):
            deploy.apply()

    assert deploy.active_port() == "8001"
    assert json.loads(deploy.cfg.journal.read_text())["state"] == "switched_pending_consumer"

    assert deploy.apply() == 0
    assert deploy.active_port() == "8000"
    assert deploy.cfg.basis.read_bytes() == b"old-basis"
    assert deploy.cfg.receipt.read_bytes() == b"old-receipt"
    _failure, failure_path = _failure_record(deploy)
    assert failure_path.is_file()


def test_crash_after_consumer_gate_resumes_final_commit_without_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deploy = _make_deploy(tmp_path, monkeypatch)
    snapshot_id, manifest, _primary = _stage_bundle(deploy, tmp_path)
    _install_search_oracle(deploy, manifest, monkeypatch)

    original_finalize = deploy.finalize
    with monkeypatch.context() as crash:
        crash.setattr(
            deploy,
            "finalize",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(SystemExit("finalize")),
        )
        with pytest.raises(SystemExit):
            deploy.apply()

    assert deploy.active_port() == "8001"
    assert json.loads(deploy.cfg.journal.read_text())["state"] == "consumer_verified"
    monkeypatch.setattr(deploy, "finalize", original_finalize)

    assert deploy.apply() == 0
    assert deploy.active_port() == "8001"
    assert adu.snapshot_id_of(deploy.cfg.basis) == snapshot_id
    assert not _has_fts(deploy.cfg.basis)


def test_actual_candidate_app_search_endpoint_matches_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deploy = _make_deploy(tmp_path, monkeypatch)
    deploy.r = adu.Runner()
    _snapshot_id, manifest, primary = _stage_bundle(deploy, tmp_path)
    candidate = tmp_path / "candidate-app.db"
    shutil.copyfile(primary, candidate)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    environment = os.environ.copy()
    environment["AI_RADAR_DB"] = str(candidate)
    environment["AI_RADAR_PRE_MIGRATED_DB"] = "1"
    environment["PYTHONPATH"] = str(REPO_ROOT / "src")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "airadar.web.app:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=REPO_ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    health_url = f"http://127.0.0.1:{port}/api/v1/healthz"
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                with urllib.request.urlopen(health_url, timeout=1) as response:
                    if response.status == 200:
                        break
            except OSError:
                if process.poll() is not None or time.monotonic() >= deadline:
                    stdout, stderr = process.communicate(timeout=1)
                    pytest.fail(
                        f"candidate app failed to start rc={process.returncode} "
                        f"stdout={stdout!r} stderr={stderr!r}"
                    )
                time.sleep(0.05)
        search_url = f"http://127.0.0.1:{port}/api/v1/timeline"
        probes = manifest["probes"]
        assert isinstance(probes, dict)
        for probe in probes.values():
            assert deploy._http_search_results(search_url, probe["term"]) == probe[
                "timeline_http_matches"
            ]
        assert any(
            probe["timeline_http_matches"] != probe["unqualified_matches"]
            for probe in probes.values()
        )
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
