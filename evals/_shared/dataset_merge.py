"""Read frozen benchmark inputs and describe revalidated question changes.

Historical cases are comparison records, never an authority for new labels.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

from .assets import BENCHMARKS, digest, file_digest, load_dataset, read_json, read_jsonl
from .dataset import news_key as legacy_news_key
from .dataset import timestamp
from .identity import news_key


def observation_order(row):
    return timestamp(row["observed_at"]), row["raw_run"], digest(row["raw"])


def add_observation(records, row):
    raw = row["raw"]
    key = news_key(raw["source_id"], raw["url"])
    payload = digest({k: v for k, v in raw.items() if k != "fetched_at"})
    record = records.setdefault(key, {"variants": {}, "observations": 0})
    old = record["variants"].get(payload)
    if old is None or observation_order(row) < observation_order(old):
        record["variants"][payload] = row
    return record


def merge_records(destination, incoming):
    for key, record in incoming.items():
        for row in record["variants"].values():
            raw = row["raw"]
            if key not in {news_key(raw["source_id"], raw["url"]), legacy_news_key(raw["source_id"], raw["url"])}:
                raise ValueError("frozen raw identity mismatch")
            merged = add_observation(destination, row)
            # Compact v2 archives cannot recover exact overlapping poll counts.
            merged["observations"] = max(merged["observations"], record["observations"])


def read_bases(paths):
    """Accept any sibling leaf; use its available same-version siblings too."""
    from .object_datasets import read_reference

    datasets, bundles = {}, {}
    for path in paths:
        path = path.resolve()
        if path.is_file():
            path = path.parent
        seed = read_json(path / "manifest.json")
        candidates = {path}
        for target, benchmark in BENCHMARKS.items():
            parts = (benchmark, target) if seed["schema_version"] == 1 else (target, benchmark)
            sibling = path.parents[2].joinpath(*parts, seed["version"])
            if (sibling / "manifest.json").is_file():
                candidates.add(sibling)
        for leaf in sorted(candidates):
            if leaf in datasets:
                continue
            manifest, cases = load_dataset(leaf)
            datasets[leaf] = (manifest, cases)
            evidence = (Path(manifest["shared_evidence"]) if manifest["schema_version"] == 1
                        else leaf / manifest["shared_evidence"]).resolve()
            bundles.setdefault((manifest["schema_version"], digest(manifest["evidence_files"])),
                               (manifest, evidence))
    records, refs, manifests, windows, failures = {}, {}, {}, {}, {}
    for manifest, evidence in bundles.values():
        if manifest["schema_version"] == 1:
            incoming = {}
            for item in read_jsonl(evidence / "raw-observations.jsonl"):
                record = add_observation(incoming, {"raw": item["raw"], "raw_run": item["run_id"],
                    "payload_run": item["payload_run_id"], "observed_at": item["observed_at"]})
                record["observations"] += 1
            raw_manifests = {p.parent.name: read_json(p) for p in (evidence / "evidence/radar/runs").glob("*/manifest.json")}
            ref_paths = [evidence / "evidence/aihot/manifest.json"]
            window = {k: manifest["window"][k] for k in ("start_inclusive", "end_exclusive")}
            failures_v1 = []
            for failure in read_json(evidence / "coverage.json")["failed_attempts"]:
                failure = dict(failure)
                if "status" in failure:
                    failure["reason"] = failure.pop("status")
                failures_v1.append(failure)
            inv = {"windows": [window], "source_failures": failures_v1}
        else:
            incoming = {row["case_id"]: {k: v for k, v in row.items() if k != "case_id"}
                        for row in read_jsonl(evidence / "raw-inputs.jsonl")}
            raw_manifests = read_json(evidence / "raw-manifests.json")
            inv = read_json(evidence / "inventory.json")
            ref_paths = []
            wanted = set(manifest["reference_manifests"])
            for name in manifest["evidence_files"]:
                if name.startswith("aihot/") and name.endswith("/manifest.json"):
                    candidate = evidence / name
                    if digest(read_json(candidate)) in wanted:
                        ref_paths.append(candidate)
            if {digest(read_json(p)) for p in ref_paths} != wanted:
                raise ValueError("base is missing frozen AIHOT reference manifests")
        merge_records(records, incoming)
        for run, payload in raw_manifests.items():
            if run in manifests and manifests[run] != payload:
                raise ValueError(f"conflicting raw run manifests: {run}")
            manifests[run] = payload
        for window in inv.get("windows", [inv.get("window")]):
            windows[digest(window)] = window
        for failure in inv["source_failures"]:
            failures[digest(failure)] = failure
        for path in ref_paths:
            ref = read_reference(path)
            refs.setdefault(ref.key, ref)
    parents = [{"path": str(path), "manifest_sha256": file_digest(path / "manifest.json"),
                "target": manifest["target"], "version": manifest["version"]}
               for path, (manifest, _) in sorted(datasets.items())]
    return records, refs, manifests, list(windows.values()), list(failures.values()), datasets, parents


def question_entries(rows, target, group="main"):
    fields = {"news-admission": ("member",), "visible-score": ("score",),
              "content-enrichment": ("category", "tags", "title", "summary", "reason"),
              "featured-members": ("featured",)}[target]
    for row in rows:
        if row["input"] is None:
            continue
        for field in fields:
            if field not in row["reference"]:
                continue
            value = row["reference"][field]
            if field == "tags":
                value = sorted(set(value))
            key = news_key(row["input"]["source_id"], row["input"]["url"])
            yield (key, field), {"group": group,
                "input_sha256": digest(row["input"]), "reference_sha256": digest(value)}


def changes_for(target, datasets, cases, supplemental, excluded):
    previous = defaultdict(list)
    parent_count = 0
    parent_entries = 0
    for leaf, (manifest, rows) in datasets.items():
        if manifest["target"] != target:
            continue
        parent_count += 1
        if manifest["schema_version"] == 1 and target in {"visible-score", "content-enrichment"}:
            rows = [r for r in rows if r["input"] is not None and r["reference"].get("member")]
        entries = list(question_entries(rows, target))
        if target == "news-admission" and "recall-only.jsonl" in manifest["files"]:
            entries += list(question_entries(read_jsonl(leaf / "recall-only.jsonl"), target, "recall-only"))
        parent_entries += len(entries)
        for key, state in entries:
            if state not in previous[key]:
                previous[key].append(state)
    current = dict(question_entries(cases, target))
    if target == "news-admission":
        current.update(question_entries(supplemental, target, "recall-only"))
    reasons = defaultdict(list)
    for item in excluded:
        reasons[item["case_id"]].append(item)
    changes = []
    for key in sorted(previous.keys() | current.keys()):
        before, after = previous.get(key, []), current.get(key)
        status = "added" if not before else "removed" if after is None else "retained" if before == [after] else "updated"
        row = {"case_id": key[0], "field": key[1], "status": status, "before": before, "after": after}
        if after is None:
            row["reasons"] = [r for r in reasons[key[0]] if "field" not in r or r["field"] == key[1]] or [
                {"reason": "reference_field_not_eligible"}]
        changes.append(row)
    counts = Counter(r["status"] for r in changes)
    return changes, {"unit": "object/news/field, including recall-only separately from main",
        "compared_parent_datasets": parent_count, "previous_unique_questions": len(previous),
        "current_unique_questions": len(current),
        **{k: counts[k] for k in ("added", "retained", "updated", "removed")},
        "duplicate_parent_questions": parent_entries - len(previous)}
