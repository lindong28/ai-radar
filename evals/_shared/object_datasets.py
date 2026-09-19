"""Object-specific v2 questions; shared observations do not imply shared eligibility.

Read verified captures once, deduplicate before pairing, and freeze compact input
evidence. Neither this builder nor its references invoke production filters.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from airadar.eval import aihot_dataset as ds
from airadar.fetcher.raw_capture import dependency_closure, read_run

from .assets import (
    DEFAULT_DATA_ROOT,
    ROOT,
    dataset_version,
    digest,
    file_digest,
    load_dataset,
    read_json,
    read_jsonl,
    utc_now,
    write_json,
    write_jsonl,
)
from .assets import (
    OBJECT_BENCHMARKS as BENCHMARKS,
)
from .dataset import raw_content_hash, reference_pass_items, resolve_sources, timestamp
from .identity import input_url, news_key, split_for, substantive_hash
from .interval import validate_interval

FIELDS = {"score": "aihot_score_0_to_100", "category": "aihot_category_slug", "tags": "tags",
          "title": "aihot_title", "summary": "aihot_summary", "reason": "aihot_recommendation_reason",
          "featured": "aihot_selected"}
ENRICHMENT = ("category", "tags", "title", "summary", "reason")


@dataclass
class Reference:
    path: Path
    manifest: dict
    capture: dict
    items: list[dict]
    passes: list[list]
    files: dict[str, bytes]

    @property
    def key(self):
        return digest(self.manifest)


def read_reference(path: Path) -> Reference:
    path = path.resolve()
    if path.is_dir():
        path /= "manifest.json"
    manifest = read_json(path)
    kind = manifest.get("artifact_type")
    if kind == "aihot_interval_v1":
        validate_interval(path)
        root, capture = path.parent, manifest
        names = {*manifest["raw_sha256"], manifest["items"]["path"], path.name}
        files = {name: (root / name).read_bytes() for name in sorted(names)}
    elif kind in {"aihot_window_v1", "aihot_window_v2", "aihot_window_v3"}:
        # Published daily windows have an archive-relative capture/items/schema.
        root = path.parents[2]
        relative = str(path.relative_to(root))
        report = ds.validate_persisted_artifact(root, relative)
        if report["result"] != "pass":
            raise ValueError(f"reference replay failed: {path}")
        metadata, raw_files = ds._persisted_artifact_maps(root, subject_path=relative)
        files = {**metadata, **raw_files}
        capture = read_json(root / manifest["capture"]["path"])
    else:
        raise ValueError(f"unsupported reference artifact: {path}")
    return Reference(path, manifest, capture, read_jsonl(root / manifest["items"]["path"]),
                     reference_pass_items(root, capture), files)


def collect_raw(root: Path, start: str, end: str, sources: dict) -> tuple[dict, dict, dict]:
    """Bound memory by unique payloads, not the number of repeated fetch rows."""
    begin, finish = timestamp(start), timestamp(end)
    if begin >= finish:
        raise ValueError("raw end must be after start")
    if not (root / "runs").is_dir():
        raise ValueError("raw root must contain runs/")
    records: dict[str, dict] = {}
    manifests, failures = {}, []
    observations = 0
    for path in sorted((root / "runs").glob("*/manifest.json")):
        header = read_json(path)
        if not begin <= timestamp(header["started_at"]) < finish:
            continue
        run_id = header["run_id"]
        if header.get("state") != "completed":
            failures.append({"run_id": run_id, "reason": "run_not_completed"})
            continue
        # A corrupt completed archive is not silently relabeled as empty input.
        manifest, rows = read_run(root, run_id)
        manifests[run_id] = manifest
        by_source = defaultdict(list)
        for row in rows:
            by_source[row["source_id"]].append(row)
        for source_id in sorted(sources):
            state = manifest["sources"].get(source_id, {})
            if state.get("status") not in {"success", "not_modified"}:
                failures.append({"run_id": run_id, "source_id": source_id,
                                 "reason": state.get("status", "missing_source")})
                continue
            payload_run = run_id
            payload_rows = by_source[source_id]
            if state["status"] == "not_modified":
                dependency_closure(root, {run_id})
                payload_run = state["payload_ref"]["run_id"]
                payload_manifest, previous = read_run(root, payload_run)
                manifests[payload_run] = payload_manifest
                payload_rows = [row for row in previous if row["source_id"] == source_id]
            observed = state.get("observed_at", manifest["started_at"])
            for raw in payload_rows:
                observations += 1
                key = news_key(source_id, raw["url"])
                payload = digest({k: v for k, v in raw.items() if k != "fetched_at"})
                record = records.setdefault(key, {"variants": {}, "observations": 0})
                record["observations"] += 1
                candidate = {"raw": raw, "observed_at": observed, "raw_run": run_id, "payload_run": payload_run}
                old = record["variants"].get(payload)
                if old is None or (timestamp(observed), run_id) < (timestamp(old["observed_at"]), old["raw_run"]):
                    record["variants"][payload] = candidate
    if not manifests:
        raise ValueError("no completed raw runs in requested interval")
    return records, {"raw_observations": observations, "unique_raw_news": len(records),
                     "run_count": len(manifests), "source_failures": failures,
                     "window": {"start_inclusive": start, "end_exclusive": end}}, manifests


def base_case(key: str, record: dict, sources: dict) -> dict:
    observations = list(record["variants"].values())
    first = min(observations, key=lambda o: (timestamp(o["observed_at"]), o["raw_run"], digest(o["raw"])))
    source = sources[first["raw"]["source_id"]]
    raw = {**first["raw"], "url": input_url(first["raw"]["url"], source)}
    return {"case_id": key, "split": split_for(raw["url"]),
            "input": {**raw, "case_id": key, "item_id": key, "tier": source["tier"],
                      "source_kind": source["kind"], "source_name": source["name"],
                      "source_enabled": True, "content_hash": raw_content_hash(raw)},
            "reference": {}, "provenance": {k: v for k, v in first.items() if k != "raw"}}


class AdmissionIndex:
    def __init__(self, references: list[Reference], contract: dict, sources: dict):
        self.proofs = []
        self.matches = defaultdict(list)
        self.source_names = {s: {v["name"].casefold(), *(a.casefold() for a in v.get("aihot_aliases", []))}
                             for s, v in sources.items()}
        for ref in references:
            sets, uncertain = [], set()
            for items in ref.passes:
                mapped, excluded = resolve_sources([i.model_dump() for i in items], contract, allow_empty=True)
                # Ambiguous publisher identity prevents reliable absence labels.
                uncertain.update(row["observed_name"].casefold() for row in excluded)
                sets.append([(item.id, ds.timeline_key(item)) for item in items])
                for item in items:
                    source = mapped.get(item.upstream_publisher_name)
                    if source and source["slug"] in sources:
                        self.matches[news_key(source["slug"], item.original_url)].append(
                            (item.id, ds.timeline_key(item), ref.key))
            self.proofs.append((ref, sets, uncertain))

    def label(self, case: dict) -> tuple[str, dict]:
        raw = case["input"]
        field = "published_at" if raw.get("published_at") else "fetched_at"
        try:
            center = timestamp(raw[field])
        except (ValueError, TypeError, KeyError, AttributeError):
            return "invalid_input_timestamp", {}
        begin, end = center - timedelta(hours=12), center + timedelta(hours=12)
        matches = sorted({i for i, at, _ in self.matches[case["case_id"]] if begin < at < end})
        evidence = {"time_field": field, "start_exclusive": begin.isoformat(), "end_exclusive": end.isoformat(),
                    "matched_aihot_ids": matches,
                    "match_references": sorted({ref for _, at, ref in self.matches[case["case_id"]] if begin < at < end})}
        for ref, sets, uncertain in self.proofs:
            if len(sets) != 2:
                continue
            try:
                for payload in ref.capture["passes"][-2:]:
                    ds.ensure_window_covered(start=begin.isoformat(), end=end.isoformat(),
                        first_response_date=payload["raw_pages"][0]["date"],
                        last_response_date=payload["raw_pages"][-1]["date"])
            except ds.DatasetContractError:
                continue
            # Membership stability is scoped to this news's ±12h, not the whole archive.
            if {i for i, at in sets[0] if begin < at < end} != {i for i, at in sets[1] if begin < at < end}:
                continue
            if uncertain.intersection(self.source_names[raw["source_id"]]):
                continue
            evidence["coverage_reference"] = ref.key
            return "main", evidence
        return ("recall-only" if matches else "unknown_reference_coverage"), evidence


def usable(field: str, value: Any) -> bool:
    if field == "score":
        return type(value) in {int, float} and math.isfinite(value) and 0 <= value <= 100
    if field == "featured":
        return type(value) is bool
    if field == "tags":
        return isinstance(value, list) and all(isinstance(tag, str) and tag.strip() for tag in value)
    return isinstance(value, str) and bool(value.strip())


def construct(records: dict, references: list[Reference], contract: dict, sources: dict,
              aihot_input_references=()) -> tuple[dict, dict, list]:
    from .aihot_inputs import fallback_case

    cases = {t: [] for t in BENCHMARKS}
    excluded = {t: [] for t in BENCHMARKS}
    supplemental = []
    index = AdmissionIndex(references, contract, sources)
    paired = defaultdict(list)
    source_for = {}
    ref_by_key = {ref.key: ref for ref in references}
    for ref in references:
        mapped, _ = resolve_sources(ref.items, contract, allow_empty=True)
        for item in ref.items:
            source = mapped.get(item["upstream_publisher_name"])
            if source and source["slug"] in sources:
                key = news_key(source["slug"], item["original_url"])
                paired[key].append((ref.key, item))
                source_for[key] = source
    keys = records.keys() | paired.keys() if aihot_input_references else records.keys()
    for key in sorted(keys):
        from_aihot = key not in records
        if from_aihot:
            case, reason = fallback_case(key, paired[key], ref_by_key, source_for[key], aihot_input_references)
            if case is None:
                for target in ("visible-score", "content-enrichment"):
                    excluded[target].append({"case_id": key, "reason": reason})
                continue
        else:
            case = base_case(key, records[key], sources)
            group, evidence = index.label(case)
            if group in {"main", "recall-only"}:
                labeled = copy.deepcopy(case)
                labeled["reference"] = {"member": bool(evidence["matched_aihot_ids"])}
                labeled["provenance"]["admission"] = evidence
                (cases["news-admission"] if group == "main" else supplemental).append(labeled)
            else:
                excluded["news-admission"].append({"case_id": key, "reason": group, **evidence})
        if key not in paired:
            continue
        if len({i["id"] for _, i in paired[key]}) != 1:
            for target in BENCHMARKS:
                if target != "news-admission" and not (from_aihot and target == "featured-members"):
                    excluded[target].append({"case_id": key, "reason": "ambiguous_raw_or_reference_version"})
            continue
        labels = {}
        for field, observed in FIELDS.items():
            if from_aihot and field == "featured":
                continue
            values = {digest(sorted(set(item[observed])) if field == "tags" else item[observed]): item[observed]
                      for _, item in paired[key]
                      if usable(field, item.get(observed))}
            if len(values) == 1:
                labels[field] = next(iter(values.values()))
            elif len(values) > 1:
                target = "visible-score" if field == "score" else "featured-members" if field == "featured" else "content-enrichment"
                excluded[target].append({"case_id": key, "field": field, "reason": "conflicting_reference_values"})
        for target, fields in (("visible-score", ("score",)), ("content-enrichment", ENRICHMENT),
                               ("featured-members", ("featured",))):
            if from_aihot and target == "featured-members":
                continue
            if not from_aihot and len({substantive_hash(o["raw"], target)
                                      for o in records[key]["variants"].values()}) != 1:
                excluded[target].append({"case_id": key, "reason": "ambiguous_raw_or_reference_version"})
                continue
            available = {f: labels[f] for f in fields if f in labels}
            if available:
                row = copy.deepcopy(case)
                row["reference"] = available
                row["provenance"]["aihot"] = [{"reference": r, "item_id": i["id"]} for r, i in paired[key]]
                cases[target].append(row)
    for key in sorted(paired.keys() - records.keys()):
        for target in BENCHMARKS:
            if target != "news-admission" and (target == "featured-members" or not aihot_input_references):
                excluded[target].append({"case_id": key, "reason": "missing_raw"})
    return cases, excluded, supplemental


def build(*, raw_root: Path | None = None, references: list[Path] | None = None,
          start: str | None = None, end: str | None = None, version: str, bases: list[Path] | None = None,
          targets: list[str] | None = None, data_root: Path = DEFAULT_DATA_ROOT,
          contract_path: Path = ROOT / "tests/fixtures/aihot_sources.json", aihot_inputs: bool = False) -> dict:
    dataset_version(version)
    targets = list(dict.fromkeys(targets or BENCHMARKS))
    if set(targets) - BENCHMARKS.keys():
        raise ValueError("unknown target")
    data_root = data_root.resolve()
    leaves = {t: data_root / t / BENCHMARKS[t] / version for t in targets}
    if any(leaf.exists() for leaf in leaves.values()):
        raise FileExistsError("dataset version already exists; choose a new version")
    from .dataset_merge import changes_for, merge_records, read_bases

    if any(v is not None for v in (raw_root, start, end)) and not all(v is not None for v in (raw_root, start, end)):
        raise ValueError("new raw input requires --raw-root, --start and --end together")
    if not bases and raw_root is None and not (aihot_inputs and references):
        raise ValueError("provide --base or new raw input (--raw-root, --start, --end)")
    records, ref_map, manifests, windows, failures, parents, lineage = read_bases(bases or [])
    for ref in map(read_reference, references or []):
        ref_map.setdefault(ref.key, ref)
    refs = list(ref_map.values())
    refs.sort(key=lambda ref: ref.key)
    allowed_inputs = {key for manifest, _ in parents.values() for key in manifest.get("aihot_input_references", [])}
    if aihot_inputs:
        allowed_inputs.update(ref_map)
    if allowed_inputs - ref_map.keys():
        raise ValueError("base is missing an authorized AIHOT input reference")
    if not refs:
        raise ValueError("at least one validated AIHOT reference is required")
    contract = read_json(contract_path)
    mapped, unmapped = resolve_sources([i for ref in refs for i in ref.items], contract, allow_empty=bool(bases))
    sources = {source["slug"]: source for source in mapped.values()}
    if raw_root is not None:
        fresh, inventory, new_manifests = collect_raw(raw_root, start, end, sources)
        merge_records(records, fresh)
        for run, payload in new_manifests.items():
            if run in manifests and manifests[run] != payload:
                raise ValueError(f"conflicting raw run manifests: {run}")
            manifests[run] = payload
        windows.append(inventory["window"])
        failures.extend(inventory["source_failures"])
    if bases or raw_root is None:
        windows = sorted({digest(w): w for w in windows}.values(), key=lambda w: timestamp(w["start_inclusive"]))
        inventory = {"window": {"start_inclusive": min((w["start_inclusive"] for w in windows), key=timestamp),
                     "end_exclusive": max((w["end_exclusive"] for w in windows), key=timestamp)} if windows else None,
                     "windows": windows, "unique_raw_news": len(records), "run_count": len(manifests),
                     "raw_observations": None,
                     "observation_count_scope": "per-news observations are lower bounds; overlapping compact archives cannot yield an exact poll total",
                     "source_failures": list({digest(f): f for f in failures}.values())}
    scoped = {k: r for k, r in records.items() if next(iter(r["variants"].values()))["raw"]["source_id"] in sources}
    cases, excluded, supplemental = construct(scoped, refs, contract, sources, allowed_inputs)
    for key in sorted(records.keys() - scoped.keys()):
        for target in BENCHMARKS:
            excluded[target].append({"case_id": key, "reason": "source_out_of_scope"})
    inventory["eligible_source_raw_news"] = len(scoped)
    # Compact, self-contained evidence: preserve all distinct input payloads,
    # not millions of identical poll rows. Original producer manifests remain.
    owner = leaves[targets[0]]
    evidence = owner / "evidence"
    owner.mkdir(parents=True, exist_ok=False)
    write_jsonl(evidence / "raw-inputs.jsonl", [{"case_id": key, **records[key]} for key in sorted(records)])
    write_json(evidence / "raw-manifests.json", manifests)
    write_json(evidence / "inventory.json", inventory)
    write_json(evidence / "sources.json", {"contract": contract, "matched": mapped, "unmapped": unmapped})
    if bases:
        write_json(evidence / "parents.json", lineage)
    for ref in refs:
        for name, content in ref.files.items():
            ds._validate_relative_path(name)
            destination = evidence / "aihot" / ref.key / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as stream:
                stream.write(content)
    evidence_files = {str(p.relative_to(evidence)): file_digest(p) for p in sorted(evidence.rglob("*")) if p.is_file()}
    outputs = {}
    for target, leaf in leaves.items():
        leaf.mkdir(parents=True, exist_ok=True)
        write_jsonl(leaf / "cases.jsonl", cases[target])
        write_jsonl(leaf / "excluded.jsonl", excluded[target])
        if target == "news-admission":
            write_jsonl(leaf / "recall-only.jsonl", supplemental)
        if target == "content-enrichment":
            write_json(leaf / "field-subsets.json", {f: [r["case_id"] for r in cases[target] if f in r["reference"]] for f in ENRICHMENT})
        change_counts = None
        if bases:
            changes, change_counts = changes_for(target, parents, cases[target], supplemental, excluded[target])
            write_jsonl(leaf / "changes.jsonl", changes)
            write_json(leaf / "merge-summary.json", change_counts)
        files = {p.name: file_digest(p) for p in sorted(leaf.iterdir()) if p.is_file()}
        counts = {"main": len(cases[target]), "excluded": len(excluded[target]),
                  "exclusion_reasons": dict(Counter(r["reason"] for r in excluded[target])),
                  "fields": {f: sum(f in c["reference"] for c in cases[target]) for f in FIELDS}}
        if target == "news-admission":
            counts.update(positive=sum(c["reference"]["member"] for c in cases[target]), recall_only=len(supplemental))
        manifest = {"schema_version": 2, "target": target, "benchmark": BENCHMARKS[target], "version": version,
                    "created_at": utc_now(), "case_count": len(cases[target]), "counts": counts,
                    "policy": "object-specific-v2", "evaluation_mode": "pointwise-threshold" if target == "featured-members" else "pointwise",
                    "window": inventory["window"], "files": files, "shared_evidence": os.path.relpath(evidence, leaf),
                    "evidence_files": evidence_files, "builder_sha256": file_digest(Path(__file__)),
                    "merge_builder_sha256": file_digest(Path(__file__).with_name("dataset_merge.py")),
                    "identity_builder_sha256": file_digest(Path(__file__).with_name("identity.py")),
                    "identity_policy": "same-source-x-post-id-substantive-v1",
                    "rebuild": {"raw_root": str(raw_root.resolve()) if raw_root else None,
                                "references": [str(Path(p).resolve()) for p in references or []],
                                "bases": [str(Path(p).resolve()) for p in bases or []],
                                "start": start, "end": end, "targets": targets, "contract_path": str(contract_path.resolve())},
                    "reference_manifests": [r.key for r in refs], "split_policy": "identity URL hash modulo 5: 0 regression, otherwise dev; X aliases share one split",
                    "pairing_limit": "same source/canonical URL and one target-relevant content version; cross-site body equality is not observable"}
        if allowed_inputs:
            # This policy travels with the evidence bundle, even on an O1/O4 sibling.
            manifest["aihot_input_references"] = sorted(allowed_inputs)
            manifest["aihot_input_scope"] = ["visible-score", "content-enrichment"]
            manifest["original_input_builder_sha256"] = file_digest(Path(__file__).with_name("aihot_inputs.py"))
            manifest["rebuild"]["aihot_inputs"] = aihot_inputs
        if target in {"visible-score", "content-enrichment"} and allowed_inputs:
            manifest["policy"] = "object-specific-aihot-original-v3"
            manifest["pairing_limit"] = "same source/canonical URL; Radar raw preferred, otherwise one substantive identity-bound AIHOT original version; not a continuous candidate pool"
            counts["input_origins"] = dict(Counter(c["provenance"].get("input_origin", "radar-raw") for c in cases[target]))
        write_json(leaf / "manifest.json", manifest)
        load_dataset(leaf, target)
        outputs[target] = {"path": str(leaf), **counts}
        if change_counts is not None:
            outputs[target]["merge"] = change_counts
    return {"version": version, "datasets": outputs, "inventory": {k: v for k, v in inventory.items() if k != "source_failures"},
            "source_failure_count": len(inventory["source_failures"]), "model_calls": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("build", help="build immutable independent object question sets, offline")
    create.add_argument("--base", dest="bases", type=Path, action="append",
                        help="existing schema 1/2 dataset leaf or manifest (legacy names accepted); repeat to merge frozen inputs")
    create.add_argument("--raw-root", type=Path)
    create.add_argument("--reference", type=Path, action="append",
                        help="repeat for interval roots or published daily window manifest paths")
    create.add_argument("--aihot-inputs", action="store_true",
                        help="authorize frozen AIHOT original titles/bodies as O2/O3-only fallback; inherited by --base")
    create.add_argument("--start", help="inclusive new raw run start, timezone required")
    create.add_argument("--end", help="exclusive new raw run start, timezone required")
    create.add_argument("--version", required=True, help="next input snapshot version: v1, v2, ...")
    create.add_argument("--target", dest="targets", choices=list(BENCHMARKS), action="append")
    create.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    create.add_argument("--contract-path", type=Path, default=ROOT / "tests/fixtures/aihot_sources.json")
    validate = sub.add_parser("validate", help="verify questions and all frozen evidence hashes")
    validate.add_argument("dataset", type=Path)
    args = vars(parser.parse_args())
    command = args.pop("command")
    try:
        if command == "build":
            args["references"] = args.pop("reference")
            result = build(**args)
        else:
            manifest, rows = load_dataset(args["dataset"])
            result = {"status": "valid", "target": manifest["target"], "version": manifest["version"], "case_count": len(rows),
                      "scope": "frozen asset integrity, not model quality or collection continuity"}
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(1, f"dataset operation failed: {type(exc).__name__}: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
