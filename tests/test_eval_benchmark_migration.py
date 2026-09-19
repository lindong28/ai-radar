"""Consumer tests for byte-preserving benchmark identity migration."""
from pathlib import Path

import pytest

from evals._shared import assets, object_entry
from scripts.migrate_eval_benchmarks import main, migrate


def legacy(root: Path, target: str, *, schema=2):
    leaf = root / target / assets.BENCHMARKS[target] / "old-snapshot"
    reference = {"news-admission": {"member": True}, "visible-score": {"score": 65},
                 "content-enrichment": {"tags": ["AI"]}, "featured-members": {"featured": False}}[target]
    assets.write_jsonl(leaf / "cases.jsonl", [
        {"case_id": target, "split": "dev", "input": {"body": "original"}, "reference": reference},
    ])
    evidence = root / "shared"
    if not evidence.exists():
        assets.write_json(evidence / "source.json", {"raw": "unchanged"})
    manifest = {
        "schema_version": schema, "target": target, "benchmark": assets.BENCHMARKS[target],
        "version": leaf.name, "evaluation_mode": "pointwise-threshold" if target == "featured-members" else "pointwise",
        "case_count": 1, "files": {"cases.jsonl": assets.file_digest(leaf / "cases.jsonl")},
        "shared_evidence": "../../../shared", "evidence_files": {"source.json": assets.file_digest(evidence / "source.json")},
    }
    assets.write_json(leaf / "manifest.json", manifest)
    return leaf


def test_migrate_four_contracts_preserves_source_and_payloads(tmp_path, capsys):
    sources = {target: legacy(tmp_path / "old", target) for target in assets.BENCHMARKS}
    before = {target: (path / "manifest.json").read_bytes() for target, path in sources.items()}
    result = migrate(sources, tmp_path / "new")
    evidence_roots = set()
    for target, item in result.items():
        destination = Path(item["path"])
        manifest, rows = assets.load_dataset(destination, target)
        assert manifest["benchmark"] == assets.OBJECT_BENCHMARKS[target]
        assert manifest["version"] == "v1"
        assert rows == assets.load_dataset(sources[target])[1]
        assert (destination / "cases.jsonl").read_bytes() == (sources[target] / "cases.jsonl").read_bytes()
        assert (sources[target] / "manifest.json").read_bytes() == before[target]
        assert manifest["migration"]["source_manifest_sha256"] == assets.file_digest(sources[target] / "manifest.json")
        evidence_roots.add((destination / manifest["shared_evidence"]).resolve())
        assert object_entry.main(target=target, argv=["validate", "--dataset", str(destination)]) == 0
    assert len(evidence_roots) == 1
    assert "未运行推理" in capsys.readouterr().out


def test_existing_destination_stops_batch_before_writes(tmp_path):
    sources = {target: legacy(tmp_path / "old", target) for target in ("news-admission", "visible-score")}
    occupied = tmp_path / "new/visible-score/aihot-score-pointwise/v1"
    occupied.mkdir(parents=True)
    with pytest.raises(FileExistsError):
        migrate(sources, tmp_path / "new")
    assert not (tmp_path / "new/news-admission").exists()


@pytest.mark.parametrize("mutation", ["cases", "evidence", "mode"])
def test_reject_corrupt_or_incompatible_source(tmp_path, mutation):
    source = legacy(tmp_path / "old", "visible-score")
    if mutation == "mode":
        import json
        manifest = assets.read_json(source / "manifest.json")
        manifest["evaluation_mode"] = "full-pool"
        (source / "manifest.json").write_text(json.dumps(manifest))
    else:
        path = source / "cases.jsonl" if mutation == "cases" else tmp_path / "old/shared/source.json"
        path.write_text(path.read_text() + " ")
    with pytest.raises(ValueError):
        migrate({"visible-score": source}, tmp_path / "new")
    assert not (tmp_path / "new").exists()


def test_reject_unknown_version_and_duplicate_cli_sources(tmp_path):
    source = legacy(tmp_path / "old", "visible-score")
    with pytest.raises(ValueError, match="v1, v2"):
        migrate({"visible-score": source}, tmp_path / "new", "rule-v3")
    assert main(["--source", f"visible-score={source}", "--source", f"visible-score={source}"]) == 1
    assert not (tmp_path / "new").exists()


def test_leaf_rejects_legacy_identity_and_corrupted_migrated_bytes(tmp_path):
    source = legacy(tmp_path / "old", "visible-score")
    assert object_entry.main(target="visible-score", argv=["validate", "--dataset", str(source)]) == 1
    result = migrate({"visible-score": source}, tmp_path / "new")
    destination = Path(result["visible-score"]["path"])
    (destination / "cases.jsonl").write_text("{}\n")
    assert object_entry.main(target="visible-score", argv=["validate", "--dataset", str(destination)]) == 1
