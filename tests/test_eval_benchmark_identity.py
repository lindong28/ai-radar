"""Benchmark names bind consumer contracts; versions only identify snapshots."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_eval_object_datasets import setup_build
from test_eval_system_assets_runner import definitions
from test_eval_system_dataset import raw, write_raw

from evals._shared import assets, runner
from evals._shared import object_datasets as ob
from evals._shared.dataset_merge import read_bases


@pytest.mark.parametrize("version", ["first", "object-specific-v2", "v0", "v01", "v1.1"])
def test_new_builder_rejects_descriptive_or_nonincremental_version(tmp_path, version):
    args = setup_build(tmp_path)
    with pytest.raises(ValueError, match="version must"):
        ob.build(**args, version=version)
    assert not args["data_root"].exists()


@pytest.mark.parametrize("target", assets.OBJECT_BENCHMARKS)
def test_new_identity_contract_and_old_runner_boundary(tmp_path, target):
    result = ob.build(**setup_build(tmp_path), version="v1", targets=[target],
                      admission_benchmark="aihot-prefilter")
    leaf = Path(result["datasets"][target]["path"])
    manifest, rows = assets.load_dataset(leaf)
    assert leaf.parts[-3:] == (target, assets.OBJECT_BENCHMARKS[target], "v1")
    assert manifest["case_count"] == len(rows)
    with pytest.raises(ValueError, match="not a shared pool"):
        runner.dataset_paths(leaf)
    for change, message in [({"schema_version": 1}, "requires schema 2"),
                            ({"evaluation_mode": "full-pool"}, "consumer contract")]:
        (leaf / "manifest.json").write_text(json.dumps({**manifest, **change}))
        with pytest.raises(ValueError, match=message):
            assets.load_dataset(leaf)


def test_legacy_named_schema2_still_loads(tmp_path):
    args = setup_build(tmp_path)
    result = ob.build(**args, version="v1", targets=["visible-score"])
    leaf = Path(result["datasets"]["visible-score"]["path"])
    manifest, rows = assets.load_dataset(leaf)
    legacy_leaf = args["data_root"] / "visible-score" / assets.BENCHMARKS["visible-score"] / "old-rule-name"
    legacy_leaf.parent.mkdir(parents=True)
    leaf.rename(legacy_leaf)
    manifest.update(benchmark=assets.BENCHMARKS["visible-score"], version="old-rule-name")
    (legacy_leaf / "manifest.json").write_text(json.dumps(manifest))
    assert assets.load_dataset(legacy_leaf)[1] == rows


def test_unrelated_same_version_sibling_requires_explicit_base(tmp_path):
    args = setup_build(tmp_path)
    first = ob.build(**args, version="v1", targets=["news-admission"])
    admission = Path(first["datasets"]["news-admission"]["path"])
    write_raw(args["raw_root"], "2026-09-18T08:00:00Z", "2026-09-18T08:15:00Z", [raw("b")])
    second = ob.build(**{**args, "start": "2026-09-18T08:00:00Z", "end": "2026-09-18T09:00:00Z"},
                      version="v1", targets=["visible-score"])
    score = Path(second["datasets"]["visible-score"]["path"])
    assert set(read_bases([admission])[-2]) == {admission}
    assert set(read_bases([admission, score])[-2]) == {admission, score}


def test_new_run_and_index_partition_by_benchmark(tmp_path):
    definitions(tmp_path)
    target = "news-admission"
    rows = []
    for benchmark in (assets.BENCHMARKS[target], assets.OBJECT_BENCHMARKS[target]):
        run, experiment = assets.create_run(tmp_path, target, "v1", benchmark=benchmark,
            created_at=datetime(2026, 9, 19, tzinfo=UTC))
        assert run.parts[-5:-2] == (target, benchmark, "v1")
        rows.extend(assets.archive_metrics(tmp_path, run, experiment,
            {"metrics": {key: {"value": 0.5, "status": "trusted"} for key in ("precision", "recall")}},
            {"target": target, "benchmark": benchmark, "version": "v1"}))
    assert assets.rebuild_index(tmp_path) == rows
    with pytest.raises(ValueError, match="pairing"):
        assets.create_run(tmp_path, target, "v1", benchmark="aihot-score-pointwise")
