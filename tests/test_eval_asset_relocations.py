"""Historical bytes and metric identities survive physical directory moves."""

import json

import pytest

from evals._shared import relocations
from evals._shared.assets import file_digest, load_dataset, read_json, rebuild_index, write_json
from scripts.migrate_eval_asset_layout import migrate, tree_identity


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)
    return path


def test_read_relocated_dataset_and_manifest_hash(tmp_path, monkeypatch):
    old = tmp_path / "before/news-admission/aihot-prefilter/v1"
    new = tmp_path / "after/news-admission/aihot-prefilter/v1"
    cases = write(new / "cases.jsonl", '{"case_id":"a","split":"dev","reference":{"member":true}}\n')
    write_json(new / "manifest.json", {
        "target": "news-admission", "benchmark": "aihot-prefilter", "version": "v1",
        "schema_version": 2, "evaluation_mode": "pointwise", "case_count": 1,
        "files": {"cases.jsonl": file_digest(cases)}, "shared_evidence": "evidence", "evidence_files": {},
    })
    monkeypatch.setattr(relocations, "relocation_map", lambda: {
        "external": {str(old): str(new)}, "project": {}, "project_aliases": [],
    })
    assert len(load_dataset(old)[1]) == 1
    assert file_digest(old / "manifest.json") == file_digest(new / "manifest.json")
    assert read_json(old / "manifest.json")["version"] == "v1"
    # Same filename outside the registered component prefix must not be guessed.
    with pytest.raises(FileNotFoundError):
        load_dataset(old.with_name("v10"))
    cases.write_text(cases.read_text() + "{}\n")
    with pytest.raises(ValueError, match="integrity mismatch"):
        load_dataset(old)


def test_existing_pinned_worktree_path_wins(tmp_path, monkeypatch):
    old = write(tmp_path / "benchmarks/aihot/README.md", "pinned old runtime")
    write(tmp_path / "data/aihot-reference/README.md", "new runtime")
    monkeypatch.setattr(relocations, "relocation_map", lambda: {
        "external": {}, "project": {"benchmarks/aihot": "data/aihot-reference"}, "project_aliases": [],
    })
    assert relocations.resolve_asset_path(old, root=tmp_path) == old
    old.unlink()
    assert relocations.resolve_asset_path(old, root=tmp_path).read_text() == "new runtime"


def test_low_level_readers_preserve_working_directory_semantics(tmp_path, monkeypatch):
    from pathlib import Path

    monkeypatch.chdir(tmp_path)
    write(Path("config.json"), '{"local":true}')
    assert read_json(Path("config.json")) == {"local": True}
    assert file_digest(Path("config.json")) == file_digest(tmp_path / "config.json")


@pytest.fixture
def historical_assets(tmp_path, monkeypatch):
    suffix = "news-admission/aihot-all-members/20260917-0700-1200-v1"
    leaf = suffix + "/2026-09-17/12-00-00"
    support = "runs/news-admission/aihot-prefilter/v1/2026-09-20/10-29-30/support/preflight"
    mapping = {"project_aliases": [], "external": {}, "project": {
        "runs/" + suffix: "data/evaluation-archive/runs/" + suffix,
        "experiments/" + suffix: "data/evaluation-archive/experiments/" + suffix,
        "runs/preflight": support,
    }}
    monkeypatch.setattr(relocations, "relocation_map", lambda: mapping)
    write(tmp_path / "runs/preflight/note.txt", "keep original analysis")
    write_json(tmp_path / "runs" / leaf / "scores.json", {"metrics": {"recall": {"value": None}}})
    write_json(tmp_path / "experiments" / leaf / "metadata.json", {"label": "old"})
    row = {"target_slug": "news-admission", "benchmark_name": "aihot-all-members",
           "benchmark_version": "20260917-0700-1200-v1", "run_id": leaf, "task": None,
           "metric_name": "recall", "metric_value": None, "status": "not_computed",
           "source": "runs/" + leaf + "/scores.json", "pointer": ["metrics", "recall", "value"],
           "metadata": "experiments/" + leaf + "/metadata.json"}
    write_json(tmp_path / "experiments" / leaf / "metrics/summary.json", [row])
    return tmp_path, mapping, row


def test_migration_preserves_bytes_rebuilds_queries_and_resumes(historical_assets):
    root, mapping, original = historical_assets
    before = tree_identity(root / "runs")
    dry = migrate(root, mapping=mapping)
    assert dry["mode"] == "dry-run"
    assert tree_identity(root / "runs") == before
    result = migrate(root, apply=True, mapping=mapping)
    assert result["metric_rows"] == 1
    assert result["deleted_files"] == 0
    assert not (root / "runs/preflight").exists()
    rows = rebuild_index(root)
    assert rows[0]["run_id"] == original["run_id"]
    assert rows[0]["status"] == "not_computed"
    assert rows[0]["source"].startswith("data/evaluation-archive/runs/")
    # Frozen leaf remains byte-identical, including its original source path.
    archived = root / "data/evaluation-archive" / original["metadata"]
    assert json.loads((archived.parent / "metrics/summary.json").read_text()) == [original]
    assert migrate(root, apply=True, mapping=mapping) == result
    support_meta = list((root / "experiments/news-admission/aihot-prefilter").rglob("metadata.json"))
    assert len(support_meta) == 1
    assert json.loads(support_meta[0].read_text())["kind"] == "asset-migration"


def test_migration_refuses_collision_before_moving_anything(historical_assets):
    root, mapping, _ = historical_assets
    destination = root / next(iter(mapping["project"].values()))
    destination.mkdir(parents=True)
    with pytest.raises(FileExistsError):
        migrate(root, apply=True, mapping=mapping)
    assert (root / "runs/preflight/note.txt").is_file()
    assert not (root / "data/evaluation-archive/layout-migration.json").exists()


def test_receipt_detects_tampering_and_index_detects_score_drift(historical_assets):
    root, mapping, original = historical_assets
    migrate(root, apply=True, mapping=mapping)
    archived = root / "data/evaluation-archive" / original["source"]
    archived.write_text('{"metrics":{"recall":{"value":0.9}}}')
    with pytest.raises(ValueError, match="payload differs"):
        migrate(root, apply=True, mapping=mapping)
    with pytest.raises(ValueError, match="projection drift"):
        rebuild_index(root)
