"""Build all four question sets from a verified common capture interval."""

from __future__ import annotations

import copy
import math
import re
import shutil
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from airadar.audit.receipts import _x_identity
from airadar.eval import aihot_dataset as ds
from airadar.fetcher.dedup import content_hash
from airadar.fetcher.raw_capture import dependency_closure, freeze, read_run

from .assets import (
    BENCHMARKS,
    DEFAULT_DATA_ROOT,
    ROOT,
    digest,
    file_digest,
    read_json,
    read_jsonl,
    slug,
    utc_now,
    write_json,
    write_jsonl,
)


def timestamp(value: str) -> datetime:
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if instant.tzinfo is None:
        raise ValueError("capture timestamp must include timezone")
    return instant.astimezone(UTC)


def news_key(source: str, url: str) -> str:
    # This is Radar's existing source/url dedup boundary, not fuzzy title matching.
    return digest([source, url.strip().rstrip("/").lower()])[:24]


def split_for(url: str) -> str:
    return "regression" if int(digest(url.strip().rstrip("/").lower())[:8], 16) % 5 == 0 else "dev"


def raw_content_hash(raw: dict) -> str:
    """Use the same payload identity as fetcher.dedup.upsert_item."""
    identity = f"{raw['content_text']}\n{raw['url'].strip().rstrip('/').lower()}"
    x_post_id = str(raw.get("extra", {}).get("x_post_id") or "")
    if x_post_id:
        identity += f"\n{x_post_id}"
    return content_hash(identity)


def resolve_sources(items: list[dict], contract: dict, *, allow_empty: bool = False) -> tuple[dict, list[dict]]:
    sources = [s for s in contract["sources"] if s.get("ai_radar_main_timeline_member")
               and s.get("enabled") and not s.get("paused") and not s.get("wechat_only")
               and not s.get("meta", {}).get("wechat_only")]
    aliases: dict[str, list[dict]] = defaultdict(list)
    identities = {s["derived_aihot_identity"]: s for s in sources}
    for source in sources:
        for name in {source["name"], *source.get("aihot_aliases", [])}:
            aliases[name.casefold()].append(source)
    observed: dict[str, list[str]] = defaultdict(list)
    for item in items:
        observed[item["upstream_publisher_name"]].append(item["original_url"])
    matched, excluded = {}, []
    for name, urls in observed.items():
        candidates = aliases.get(name.casefold(), [])
        # A known publisher may link to an X post: its publishing source stays
        # the explicit non-X alias, rather than becoming the linked account.
        if len(candidates) == 1 and candidates[0]["kind"] != "x":
            matched[name] = candidates[0]
            continue
        url_identities = {_x_identity([url]) for url in urls}
        handles = re.findall(r"[@＠]([A-Za-z0-9_]{1,15})", name)
        display_identity = f"x:{handles[0].casefold()}" if len(handles) == 1 else None
        if len(candidates) <= 1 and len(url_identities) == 1 and None not in url_identities:
            identity = next(iter(url_identities))
            expected = candidates[0]["derived_aihot_identity"] if candidates else display_identity
            if identity == expected and (display_identity is None or identity == display_identity):
                candidates = [identities[identity]] if identity in identities else []
            else:
                candidates = []
        elif len(candidates) <= 1:
            candidates = []
        if len(candidates) == 1:
            matched[name] = candidates[0]
        else:
            excluded.append({"observed_name": name, "reason": "ambiguous" if candidates else "no_verified_common_source"})
    if not matched and not allow_empty:
        raise ValueError("no common sources observed in AIHOT interval")
    return matched, excluded


class RawCoverage:
    """Verify each archive once and cache outward-aligned cadence proofs."""

    def __init__(self, root: Path, source_ids: set[str], cadence_seconds: int = 900):
        if cadence_seconds <= 0:
            raise ValueError("cadence must be positive")
        self.root, self.source_ids, self.cadence_seconds = root, source_ids, cadence_seconds
        self.runs = []
        for path in sorted((root / "runs").glob("*/manifest.json")):
            manifest = read_json(path)
            self.runs.append((timestamp(manifest["started_at"]), manifest["run_id"]))
        self.verified: dict[str, dict | None] = {}
        self.proofs: dict[tuple[int, int], dict] = {}

    def check(self, begin: datetime, end: datetime) -> dict:
        cadence = self.cadence_seconds
        key = (math.floor(begin.timestamp() / cadence) * cadence, math.ceil(end.timestamp() / cadence) * cadence)
        if key in self.proofs:
            return self.proofs[key]
        slots: dict[int, set[str]] = defaultdict(set)
        configs: dict[str, set[str]] = defaultdict(set)
        run_ids = set()
        for observed, run_id in self.runs:
            if not key[0] <= observed.timestamp() < key[1]:
                continue
            if run_id not in self.verified:
                try:
                    manifest, _ = read_run(self.root, run_id)
                    dependency_closure(self.root, {run_id})
                    self.verified[run_id] = manifest
                except (ValueError, OSError, KeyError, TypeError):
                    self.verified[run_id] = None
            manifest = self.verified[run_id]
            if manifest is None:
                continue
            slot = math.floor(observed.timestamp() / cadence) * cadence
            for source_id in self.source_ids:
                source = manifest["sources"].get(source_id, {})
                if source.get("status") in {"success", "not_modified"}:
                    slots[slot].add(source_id)
                    configs[source_id].add(source["configuration_sha256"])
                    run_ids.add(run_id)
        missing = [{"slot": datetime.fromtimestamp(slot, UTC).isoformat(), "sources": sorted(self.source_ids - slots[slot])}
                   for slot in range(*key, cadence) if self.source_ids - slots[slot]]
        changed = sorted(s for s, values in configs.items() if len(values) != 1)
        proof = {"start": datetime.fromtimestamp(key[0], UTC).isoformat(),
                 "end": datetime.fromtimestamp(key[1], UTC).isoformat(), "cadence_seconds": cadence,
                 "source_ids": sorted(self.source_ids), "complete": not missing and not changed,
                 "missing_slots": missing, "configuration_changed": changed, "run_ids": sorted(run_ids)}
        self.proofs[key] = proof
        return proof


def reference_pass_items(reference: Path, manifest: dict) -> list[list]:
    """Read every original API item, including those outside the input interval.

    The caller first validates the interval (all terminal page chains and raw
    hashes). SSR labels are not needed for the O1 existence test.
    """
    result = []
    for payload in manifest["passes"][-2:]:
        capture_pass = ds.CapturePassManifest.model_validate(payload)
        raw_files = {p.raw_path: (reference / p.raw_path).read_bytes() for p in capture_pass.raw_pages}
        result.append(ds._load_capture_pass_items(capture_pass, raw_files=raw_files))
    return result


def admission_cases(raw_cases: list[dict], *, all_pass_items: list[list], manifest: dict,
                    mapped: dict, contract: dict, raw_coverage: RawCoverage) -> tuple[list[dict], list[dict]]:
    source_ids = {s["slug"] for s in mapped.values()}
    indexes = []
    uncertain_sources = set()
    for items in all_pass_items:
        # Reconcile the larger API observation against the same contract, then
        # retain only sources approved by their occurrence in the input window.
        extended, _ = resolve_sources([item.model_dump() for item in items], contract, allow_empty=True)
        observed_names = {item.upstream_publisher_name for item in items}
        for name, source in mapped.items():
            if name in observed_names and extended.get(name, {}).get("slug") != source["slug"]:
                uncertain_sources.add(source["slug"])
        index: dict[str, list] = defaultdict(list)
        for item in items:
            source = extended.get(item.upstream_publisher_name)
            if source and source["slug"] in source_ids:
                index[news_key(source["slug"], item.original_url)].append(item)
        indexes.append(index)
    included, excluded = [], []
    for case in raw_cases:
        raw = case["input"]
        field = "published_at" if raw.get("published_at") else "fetched_at"
        try:
            center = timestamp(raw[field])
        except (ValueError, TypeError, KeyError, AttributeError):
            excluded.append({"case_id": case["case_id"], "reason": "invalid_input_timestamp"})
            continue
        begin, end = center - timedelta(hours=12), center + timedelta(hours=12)
        matches = sorted({item.id for index in indexes for item in index.get(case["case_id"], [])
                          if begin < ds.timeline_key(item) < end})
        reason = None
        proof = None
        if not matches:
            if raw["source_id"] in uncertain_sources:
                excluded.append({"case_id": case["case_id"], "reason": "source_mapping_incomplete"})
                continue
            for payload in manifest["passes"][-2:]:
                try:
                    ds.ensure_window_covered(start=begin.isoformat(), end=end.isoformat(),
                        first_response_date=payload["raw_pages"][0]["date"],
                        last_response_date=payload["raw_pages"][-1]["date"])
                except ds.DatasetContractError:
                    reason = "aihot_window_not_covered"
                    break
            if reason is None:
                sets = [{item.id for item in items if begin < ds.timeline_key(item) < end} for items in all_pass_items]
                if sets[0] != sets[1]:
                    reason = "aihot_window_unstable"
            if reason is None:
                proof = raw_coverage.check(begin, end)
                if not proof["complete"]:
                    reason = "radar_window_incomplete"
        evidence = {"time_field": field, "center": center.isoformat(), "start_exclusive": begin.isoformat(),
                    "end_exclusive": end.isoformat(), "aihot_time_basis": "aihot_timeline_v1",
                    "matched_aihot_ids": matches}
        if proof is not None:
            evidence["radar_coverage_window"] = [proof["start"], proof["end"]]
        if reason:
            excluded.append({"case_id": case["case_id"], "reason": reason, **evidence})
            continue
        labeled = copy.deepcopy(case)
        labeled["reference"] = {"member": bool(matches)}
        labeled["provenance"]["admission"] = evidence
        included.append(labeled)
    return included, excluded


def read_raw_interval(root: Path, start: str, end: str, source_ids: set[str], *, cadence_seconds: int = 900) -> tuple[list[dict], dict]:
    begin, finish = timestamp(start), timestamp(end)
    if finish <= begin or begin.timestamp() % cadence_seconds or finish.timestamp() % cadence_seconds:
        raise ValueError("interval must contain complete cadence slots")
    successful_slots: dict[str, set[str]] = defaultdict(set)
    observations, run_ids, failed_attempts = [], set(), []
    configs: dict[str, set[str]] = defaultdict(set)
    cache: dict[str, tuple[dict, list[dict]]] = {}

    def read(run_id: str):
        if run_id not in cache:
            cache[run_id] = read_run(root, run_id)
        return cache[run_id]

    for path in sorted((root / "runs").glob("*/manifest.json")):
        manifest = read_json(path)
        observed = timestamp(manifest["started_at"])
        if not begin <= observed < finish:
            continue
        run_id = manifest["run_id"]
        try:
            manifest, rows = read(run_id)
        except (ValueError, OSError, KeyError) as exc:
            failed_attempts.append({"run_id": run_id, "error_type": type(exc).__name__})
            continue
        slot = begin + timedelta(seconds=int((observed - begin).total_seconds() // cadence_seconds) * cadence_seconds)
        for source_id in sorted(source_ids):
            source = manifest["sources"].get(source_id, {})
            if source.get("status") not in {"success", "not_modified"}:
                failed_attempts.append({"run_id": run_id, "source_id": source_id, "status": source.get("status", "missing")})
                continue
            selected_rows = [r for r in rows if r["source_id"] == source_id]
            payload_run = run_id
            if source["status"] == "not_modified":
                dependency_closure(root, {run_id})
                payload_run = source["payload_ref"]["run_id"]
                _, previous_rows = read(payload_run)
                selected_rows = [r for r in previous_rows if r["source_id"] == source_id]
            successful_slots[slot.isoformat()].add(source_id)
            configs[source_id].add(source["configuration_sha256"])
            run_ids.add(run_id)
            for row in selected_rows:
                observations.append({"raw": row, "run_id": run_id, "payload_run_id": payload_run,
                                     "observed_at": source.get("observed_at", manifest["started_at"])})
    missing = []
    for index in range(int((finish - begin).total_seconds() / cadence_seconds)):
        slot = (begin + timedelta(seconds=index * cadence_seconds)).isoformat()
        absent = source_ids - successful_slots[slot]
        if absent:
            missing.append({"slot": slot, "sources": sorted(absent)})
    if missing:
        raise ValueError(f"raw interval has uncovered source/slots: {missing}")
    changed = [s for s, hashes in configs.items() if len(hashes) != 1]
    if changed:
        raise ValueError(f"source configuration changed inside interval: {changed}")
    return observations, {"start": start, "end": end, "cadence_seconds": cadence_seconds,
        "slot_count": len(successful_slots), "source_ids": sorted(source_ids), "run_ids": sorted(run_ids),
        "failed_attempts": failed_attempts, "configuration_sha256": {s: next(iter(h)) for s, h in configs.items()},
        "coverage_rule": "at least one verified success per source per slot; retries retained"}


def build(*, raw_root: Path, reference: Path, version: str, data_root: Path = DEFAULT_DATA_ROOT,
          contract_path: Path = ROOT / "tests/fixtures/aihot_sources.json") -> dict:
    from .interval import validate_interval

    reference = reference.parent if reference.is_file() else reference
    proof = validate_interval(reference)
    manifest = read_json(reference / "manifest.json")
    window = manifest["window"]
    start, end = window["start_inclusive"], window["end_exclusive"]
    hot_items = read_jsonl(reference / "items.jsonl")
    contract = read_json(contract_path)
    mapped, excluded = resolve_sources(hot_items, contract)
    sources = {s["slug"]: s for s in mapped.values()}
    observations, coverage = read_raw_interval(raw_root, start, end, set(sources))
    slug(version)
    leaves = {t: data_root / b / t / version for t, b in BENCHMARKS.items()}
    if any(p.exists() for p in leaves.values()):
        raise FileExistsError("dataset version already exists; choose a new version")
    # Preserve every raw observation. Questions fix the first observed body per
    # source/URL, a deterministic policy independent of any reference or score.
    first = {}
    for observation in sorted(observations, key=lambda r: (r["observed_at"], r["run_id"], digest(r["raw"]))):
        raw = observation["raw"]
        key = news_key(raw["source_id"], raw["url"])
        first.setdefault(key, observation)
    cases = {}
    for key, observation in first.items():
        raw = observation["raw"]
        source = sources[raw["source_id"]]
        input_record = {**raw, "case_id": key, "item_id": key, "tier": source["tier"],
                        "source_kind": source["kind"], "source_name": source["name"], "source_enabled": True,
                        "content_hash": raw_content_hash(raw)}
        cases[key] = {"case_id": key, "input": input_record, "reference": {"member": False, "featured": False},
                      "split": split_for(raw["url"]),
                      "provenance": {"raw_run": observation["run_id"], "payload_run": observation["payload_run_id"],
                                     "observed_at": observation["observed_at"]}}
    reference_ids = set()
    for item in hot_items:
        source = mapped.get(item["upstream_publisher_name"])
        if source is None:
            continue
        key = news_key(source["slug"], item["original_url"])
        if key in reference_ids:
            raise ValueError("multiple AIHOT records share a news identity; adjudicate before freezing")
        reference_ids.add(key)
        case = cases.setdefault(key, {"case_id": key, "input": None, "reference": {},
            "split": split_for(item["original_url"]),
            "provenance": {}})
        case["reference"] = {"member": True, "featured": item["aihot_selected"]}
        for observed_field, field in (("aihot_score_0_to_100", "score"), ("aihot_category_slug", "category"),
                                     ("tags", "tags"), ("aihot_title", "title"), ("aihot_summary", "summary"),
                                     ("aihot_recommendation_reason", "reason")):
            if item.get(observed_field) is not None:
                case["reference"][field] = item[observed_field]
        case["provenance"]["aihot_item_id"] = item["id"]
    rows = [cases[key] for key in sorted(cases)]
    raw_coverage = RawCoverage(raw_root, set(sources), coverage["cadence_seconds"])
    o1_rows, o1_excluded = admission_cases(
        [row for row in rows if row["input"] is not None],
        all_pass_items=reference_pass_items(reference, manifest), manifest=manifest,
        mapped=mapped, contract=contract, raw_coverage=raw_coverage,
    )
    o1_proofs = list(raw_coverage.proofs.values())
    coverage["admission_windows"] = o1_proofs
    frozen_runs = set(coverage["run_ids"])
    for window_proof in o1_proofs:
        if window_proof["complete"]:
            frozen_runs.update(window_proof["run_ids"])
    counts = {"raw_observation_count": len(observations), "unique_raw_news": len(first),
              "reference_members": len(reference_ids), "missing_raw": len(reference_ids - first.keys()),
              "source_count": len(sources), "hours": (timestamp(end) - timestamp(start)).total_seconds() / 3600,
              "field_pairs": {field: sum(c["input"] is not None and field in c["reference"] for c in rows)
                              for field in ("score", "category", "tags", "title", "summary", "reason")},
              "o1_positive": sum(row["reference"]["member"] for row in o1_rows),
              "o1_negative": sum(not row["reference"]["member"] for row in o1_rows),
              "o1_excluded": len(o1_excluded),
              "o1_exclusion_reasons": dict(Counter(row["reason"] for row in o1_excluded))}
    # Evidence is shared under one input leaf, not copied four times. Each leaf
    # records its byte identity; validation follows the common evidence pointer.
    primary = leaves["news-admission"]
    primary.mkdir(parents=True)
    freeze(raw_root, primary / "evidence/radar", frozen_runs)
    shutil.copytree(reference, primary / "evidence/aihot")
    write_jsonl(primary / "raw-observations.jsonl", observations)
    write_json(primary / "coverage.json", coverage)
    write_jsonl(primary / "admission-excluded.jsonl", o1_excluded)
    write_json(primary / "sources.json", {"contract": contract, "matched": mapped, "excluded": excluded})
    evidence_files = {str(p.relative_to(primary)): file_digest(p) for p in sorted(primary.rglob("*")) if p.is_file()}
    for target, leaf in leaves.items():
        leaf.mkdir(parents=True, exist_ok=True)
        target_rows = o1_rows if target == "news-admission" else rows
        write_jsonl(leaf / "cases.jsonl", target_rows)
        write_json(leaf / "manifest.json", {"schema_version": 1, "target": target, "benchmark": BENCHMARKS[target],
            "version": version, "created_at": utc_now(), "window": window, "case_count": len(target_rows), "counts": counts,
            "files": {"cases.jsonl": file_digest(leaf / "cases.jsonl")},
            "shared_evidence": str(primary), "coverage_sha256": file_digest(primary / "coverage.json"),
            "evidence_files": evidence_files,
            "reference_manifest_sha256": file_digest(primary / "evidence/aihot/manifest.json"),
            "raw_policy": "all prefilter observations; first observed source/url body for inference",
            "reference_validation": proof, "split_policy": "URL hash modulo 5: 0 regression, otherwise dev"})
    return {"version": version, "datasets": {t: str(p) for t, p in leaves.items()}, "counts": counts}
