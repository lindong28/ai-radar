from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from test_eval_object_datasets import setup_build
from test_eval_system_dataset import raw, write_raw
from test_eval_system_interval import capture

from evals._shared import dataset as legacy
from evals._shared import object_datasets as ob
from evals._shared.assets import file_digest, load_dataset, read_json, read_jsonl
from evals._shared.dataset import news_key


def leaves(result):
    return {t: Path(d["path"]) for t, d in result["datasets"].items()}


def test_merge_duplicate_versions_and_reextend_without_original_archives(tmp_path):
    args = setup_build(tmp_path)
    first = leaves(ob.build(**args, version="v1"))
    second = leaves(ob.build(**args, version="v2"))
    frozen = {p: file_digest(p / "cases.jsonl") for p in [*first.values(), *second.values()]}
    args["raw_root"].rename(tmp_path / "unavailable-raw")
    args["references"][0].parent.rename(tmp_path / "unavailable-reference")
    merged = leaves(ob.build(bases=[first["visible-score"], second["content-enrichment"]],
        version="v3", data_root=args["data_root"], contract_path=args["contract_path"]))
    for target, path in merged.items():
        assert load_dataset(path)[1] == load_dataset(first[target])[1]
        report = read_json(path / "merge-summary.json")
        assert report["added"] == report["updated"] == report["removed"] == 0
        assert report["duplicate_parent_questions"] == report["previous_unique_questions"]
    again = leaves(ob.build(bases=[merged["news-admission"], first["news-admission"]],
        version="v4", data_root=args["data_root"], contract_path=args["contract_path"]))
    assert load_dataset(again["news-admission"])[1] == load_dataset(first["news-admission"])[1]
    assert {p: file_digest(p / "cases.jsonl") for p in frozen} == frozen


def test_extend_with_new_raw_adds_questions_and_new_variant_removes_only_pair(tmp_path):
    args = setup_build(tmp_path)
    base = leaves(ob.build(**args, version="v1"))["news-admission"]
    write_raw(args["raw_root"], "2026-09-18T08:00:00Z", "2026-09-18T08:15:00Z", [raw("b")])
    extra = leaves(ob.build(**{**args, "start": "2026-09-18T08:00:00Z", "end": "2026-09-18T09:00:00Z"},
                           bases=[base], version="v2"))
    score = extra["visible-score"]
    assert len(load_dataset(score)[1]) == 2
    assert read_json(score / "merge-summary.json")["added"] == 1
    assert read_json(score / "merge-summary.json")["retained"] == 1
    write_raw(args["raw_root"], "2026-09-19T08:00:00Z", "2026-09-19T08:15:00Z",
              [{**raw(), "content_text": "changed body"}])
    changed = leaves(ob.build(**{**args, "start": "2026-09-19T08:00:00Z", "end": "2026-09-19T09:00:00Z"},
                              bases=[score], version="v3"))
    for target in ("visible-score", "featured-members"):
        report = read_json(changed[target] / "merge-summary.json")
        assert report["removed"] == 1 and report["retained"] == 1
        removed = next(r for r in read_jsonl(changed[target] / "changes.jsonl") if r["status"] == "removed")
        assert removed["reasons"][0]["reason"] == "ambiguous_raw_or_reference_version"
    assert any(c["case_id"] == news_key("gazette", raw()["url"]) for c in read_jsonl(changed["news-admission"] / "recall-only.jsonl"))


def test_legacy_base_uses_unfiltered_observations_not_candidate_rows(tmp_path):
    args = setup_build(tmp_path)
    write_raw(args["raw_root"], "2026-09-17T07:15:00Z", "2026-09-17T12:00:00Z", [raw()])
    write_raw(args["raw_root"], "2026-09-17T07:01:00Z", "2026-09-17T07:02:00Z", [], failed_source="gazette")
    old = legacy.build(raw_root=args["raw_root"], reference=args["references"][0],
        version="old", data_root=args["data_root"], contract_path=args["contract_path"])
    latest = leaves(ob.build(**args, version="v1"))
    result = leaves(ob.build(bases=[Path(old["datasets"]["visible-score"]), latest["news-admission"]],
        version="v2", data_root=args["data_root"], contract_path=args["contract_path"]))
    assert load_dataset(result["visible-score"])[1] == load_dataset(latest["visible-score"])[1]
    report = read_json(result["news-admission"] / "merge-summary.json")
    assert report["updated"] == 1  # Historical positive main becomes recall-only.
    assert report["current_unique_questions"] == 2
    assert len(load_dataset(result["visible-score"])[1]) == 1  # No reference-only b input invented.
    featured = read_json(result["featured-members"] / "merge-summary.json")
    assert featured["previous_unique_questions"] == 2 and featured["removed"] == 1
    removed = next(c for c in read_jsonl(result["featured-members"] / "changes.jsonl") if c["status"] == "removed")
    assert removed["case_id"] == news_key("gazette", raw("negative")["url"])
    inventory = read_json(result["news-admission"] / "evidence/inventory.json")
    failed = {"run_id": "20260917T070100Z", "source_id": "gazette", "reason": "failed"}
    assert inventory["source_failures"].count(failed) == 1
    old_only = leaves(ob.build(bases=[Path(old["datasets"]["visible-score"])], version="v3",
        data_root=args["data_root"], contract_path=args["contract_path"]))
    assert read_json(old_only["news-admission"] / "evidence/inventory.json")["source_failures"] == [failed]


def test_current_source_contract_can_remove_all_old_questions(tmp_path):
    args = setup_build(tmp_path)
    first = leaves(ob.build(**args, version="v1"))
    contract = read_json(args["contract_path"])
    contract["sources"][0]["paused"] = True
    args["contract_path"].write_text(json.dumps(contract))
    result = leaves(ob.build(bases=[first["news-admission"]], version="v2",
        data_root=args["data_root"], contract_path=args["contract_path"]))
    for path in result.values():
        assert not load_dataset(path)[1]
        changes = read_jsonl(path / "changes.jsonl")
        assert changes and all(c["status"] == "removed" for c in changes)
        assert all(c["reasons"][0]["reason"] == "source_out_of_scope" for c in changes)


def test_cli_base_only_and_invalid_arguments_and_corrupt_base(tmp_path):
    args = setup_build(tmp_path)
    base = leaves(ob.build(**args, version="v1"))["news-admission"]
    command = [sys.executable, "scripts/build_eval_datasets.py", "build", "--base", str(base),
               "--version", "v2", "--contract-path", str(args["contract_path"]), "--data-root", str(args["data_root"])]
    result = subprocess.run(command, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["datasets"]["visible-score"]["merge"]["retained"] == 1
    assert output["inventory"]["raw_observations"] is None
    result = subprocess.run(command, text=True, capture_output=True)
    assert result.returncode == 1 and "already exists" in result.stderr
    with pytest.raises(ValueError, match="together"):
        ob.build(bases=[base], raw_root=args["raw_root"], version="v3")
    (base / "evidence/raw-inputs.jsonl").write_text("{}\n")
    with pytest.raises(ValueError, match="integrity"):
        ob.build(bases=[base], version="v3", data_root=args["data_root"])


def test_new_reference_conflict_rechecks_old_field_without_dropping_others(tmp_path):
    args = setup_build(tmp_path)
    base = leaves(ob.build(**args, version="v1"))["news-admission"]
    # A second real capture has the same IDs but conflicting title observations.
    from test_eval_system_interval import Transport

    from airadar.eval import aihot_dataset as ds
    original = Transport.get

    def changed_get(self, url, **kwargs):
        response = original(self, url, **kwargs)
        if url.endswith("/api/v1/items"):
            payload = json.loads(response.body)
            for item in payload["items"]:
                item["title"] = "changed reference title"
            return ds.HttpResponse(response.status, response.headers, json.dumps(payload).encode())
        return response

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Transport, "get", changed_get)
        ref = capture(tmp_path / "new-reference")
    result = leaves(ob.build(bases=[base], references=[ref], version="v2", data_root=args["data_root"],
                            contract_path=args["contract_path"]))
    rows = load_dataset(result["content-enrichment"])[1]
    assert "title" not in rows[0]["reference"] and "tags" in rows[0]["reference"]
    assert read_json(result["content-enrichment"] / "merge-summary.json")["removed"] == 1
    assert len(load_dataset(result["visible-score"])[1]) == 1
