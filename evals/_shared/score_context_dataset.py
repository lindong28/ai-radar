"""Freeze a bounded archive-clock / retrieved-event diagnostic from an existing dev run."""
from __future__ import annotations

import argparse
import copy
import gzip
import json
import math
import os
from collections import Counter
from datetime import datetime
from pathlib import Path

from . import assets
from .identity import identity_url


def instant(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or "T" not in value:
        raise ValueError("requires an explicit timezone-aware timestamp")
    return parsed


def grams(raw):
    text = (raw.get("title", "") + " " + raw.get("content_text", "")[:500]).lower()
    return {text[i:i+3] for i in range(len(text)-2) if not text[i:i+3].isspace()}


def candidates(focal, observed, pool, allowed):
    """Text retrieval only, not an assertion that two articles describe one event."""
    target = grams(focal)
    ranked = []
    latest = {}
    for row in pool:
        raw = row["raw"]
        if raw["source_id"] not in allowed or identity_url(raw["url"]) == identity_url(focal["url"]):
            continue
        if instant(row["observed_at"]) > observed:
            continue
        identity = (raw["source_id"], identity_url(raw["url"]))
        if identity not in latest or instant(row["observed_at"]) > instant(latest[identity]["observed_at"]):
            latest[identity] = row
    for row in latest.values():
        raw = row["raw"]
        try:
            age = (observed - instant(raw["published_at"])).total_seconds()/3600
        except (ValueError, TypeError):
            continue
        if not 0 <= age <= 48 or not raw.get("content_text"):
            continue
        other = grams(raw)
        similarity = len(target & other) / math.sqrt(max(1, len(target)*len(other)))
        ranked.append((similarity, row["id"], row))
    result = []
    for similarity, key, row in sorted(ranked, key=lambda x: (-x[0], x[1]))[:3]:
        raw = row["raw"]
        result.append({"id": key, **{k: raw.get(k, "") for k in
            ("source_id", "title", "url", "content_text", "published_at")},
            "observed_at": row["observed_at"], "retrieval_similarity": similarity})
    return result


def build(dataset: Path, source_run: Path, raw_root: Path, output: Path):
    if output.exists():
        raise FileExistsError("context dataset is immutable; select a new vN")
    manifest, all_cases = assets.load_dataset(dataset, "visible-score")
    selected = assets.read_jsonl(source_run / "cases.jsonl")
    original = {c["case_id"]: c for c in all_cases}
    if any(c != original.get(c["case_id"]) or c["split"] != "dev" for c in selected):
        raise ValueError("source cases must be unchanged dev items from the parent benchmark")
    evidence = (dataset / manifest["shared_evidence"]).resolve()
    manifests = assets.read_json(evidence / "raw-manifests.json")
    earliest = {}
    for m in manifests.values():
        for sid, row in m["sources"].items():
            if row["status"] == "success" and row.get("observed_at"):
                observed = instant(row["observed_at"])
                earliest[sid] = min(earliest.get(sid, observed), observed)
    records = assets.read_jsonl(evidence / "raw-inputs.jsonl")
    by_id = {r["case_id"]: r for r in records}
    # Each frozen content version retains its own observation clock.
    pool = [{"id": r["case_id"] + ":" + h, **v} for r in records for h, v in r["variants"].items()]
    allowed = {c["input"]["source_id"] for c in all_cases}
    counts = Counter(); cases = []; proofs = {}; raw_cache = {}
    for c in selected:
        key, raw = c["case_id"], c["input"]
        run = c["provenance"].get("payload_run")
        if not run or key not in by_id:
            counts["no_radar_archive"] += 1; continue
        version = min(by_id[key]["variants"].values(), key=lambda v: instant(v["observed_at"]))
        observed = instant(version["observed_at"])
        if version["raw"].get("content_text") != raw["content_text"]:
            counts["first_body_differs"] += 1; continue
        published = instant(raw["published_at"])
        if raw["source_id"] not in earliest or published < earliest[raw["source_id"]]:
            counts["left_truncated"] += 1; continue
        if published > observed:
            counts["negative_age"] += 1; continue
        source_run_id = version["payload_run"]
        if source_run_id not in raw_cache:
            leaf = raw_root / source_run_id
            m = assets.read_json(leaf / "manifest.json")
            payload = leaf / "items.jsonl.gz"
            if assets.file_digest(payload) != m["items_sha256"]:
                raise ValueError("raw archive digest mismatch")
            with gzip.open(payload, "rt") as stream:
                rows = [json.loads(line) for line in stream]
            raw_cache[source_run_id] = (m, rows)
            proofs[source_run_id] = {"path": str(payload), "sha256": m["items_sha256"]}
        m, rows = raw_cache[source_run_id]
        if not any(r["source_id"] == raw["source_id"] and identity_url(r["url"]) == identity_url(raw["url"])
                   and r.get("content_text") == raw["content_text"] for r in rows):
            raise ValueError("focal body not present in the claimed first archive")
        if instant(m["sources"][raw["source_id"]]["observed_at"]) != observed:
            raise ValueError("first observation does not match source manifest")
        case = copy.deepcopy(c)
        case["input"]["score_context"] = {"archive_first_observed_at": version["observed_at"],
            "age_hours": (observed-published).total_seconds()/3600,
            "neighbors": candidates(raw, observed, pool, allowed)}
        case["provenance"]["context_parent_case"] = c["case_id"]
        cases.append(case); counts["eligible"] += 1
    if not cases:
        raise ValueError("no eligible context cases")
    assets.write_jsonl(output / "cases.jsonl", cases)
    assets.write_json(output / "construction.json", {"exclusions": dict(counts), "raw_proofs": proofs,
        "parent_run": str(source_run), "parent_cases_sha256": assets.file_digest(source_run / "cases.jsonl"),
        "builder_sha256": assets.file_digest(Path(__file__)), "retrieval": "top3 character trigram cosine; prior observed; 48h; not semantic labels",
        "clock": "first observation within frozen archive; not true ingestion or AIHOT clock"})
    assets.write_json(output / "manifest.json", {"schema_version": 2, "target": "visible-score",
        "benchmark": assets.SCORE_CONTEXT, "version": output.name, "evaluation_mode": "pointwise-context",
        "case_count": len(cases), "created_at": assets.utc_now(),
        "files": {name: assets.file_digest(output/name) for name in ("cases.jsonl", "construction.json")},
        "shared_evidence": os.path.relpath(evidence, output),
        "evidence_files": {name: assets.file_digest(evidence/name) for name in ("raw-inputs.jsonl", "raw-manifests.json")}})
    assets.load_dataset(output, "visible-score")
    print(json.dumps({"dataset": str(output), "counts": dict(counts)}, ensure_ascii=False))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "source-run", "raw-root", "output"):
        p.add_argument("--"+name, type=Path, required=True)
    a = p.parse_args()
    build(a.dataset, a.source_run, a.raw_root, a.output)
