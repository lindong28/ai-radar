"""Object-scoped human reviews: batches live in JSON metadata, not paths."""
from __future__ import annotations

import fcntl
import json
import os
from copy import deepcopy
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory

from .assets import digest, read_json, read_jsonl, utc_now

FORMAT = "ai-radar-human-reviews-v1"
DOCUMENTS = {
    "review_context": "review-context.json", "annotations": "annotations.jsonl",
    "source_cases": "source-cases.jsonl", "source_predictions": "source-predictions.jsonl",
    "effective_cases": "effective-cases.jsonl", "original_scores": "original-scores.json",
    "human_priority_scores": "human-priority-scores.json",
}


def read_reviews(path: Path) -> dict:
    book = read_json(path)
    if book["metadata"]["format"] != FORMAT:
        raise ValueError("unsupported human review format")
    ids = set()
    for batch in book["batches"]:
        key = batch["metadata"]["batch_id"]
        if not isinstance(key, str) or not key or key in ids:
            raise ValueError("duplicate or invalid human batch_id")
        ids.add(key)
        if digest({k: v for k, v in batch.items() if k != "sha256"}) != batch["sha256"]:
            raise ValueError("human review integrity mismatch")
        if any(a["target"] != book["metadata"]["target"] for a in batch["data"]["annotations"]):
            raise ValueError("human review target mismatch")
    return book


def append_batch(path: Path, target: str, batch: dict) -> dict:
    """Serialize writers; replace one complete JSON, preserving prior batches."""
    path.parent.mkdir(parents=True, exist_ok=True)
    batch = deepcopy(batch)
    batch.pop("sha256", None)
    batch["sha256"] = digest(batch)
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        book = read_reviews(path) if path.exists() else {
            "metadata": {"format": FORMAT, "target": target}, "batches": [],
        }
        if book["metadata"]["target"] != target:
            raise ValueError("human review target mismatch")
        key = batch["metadata"]["batch_id"]
        for old in book["batches"]:
            if old["metadata"]["batch_id"] == key:
                if old["data"] == batch["data"] and {
                    k: v for k, v in old["metadata"].items() if k != "recorded_at"
                } == {k: v for k, v in batch["metadata"].items() if k != "recorded_at"}:
                    return old
                raise ValueError("human batch_id already exists with different content")
        book["batches"].append(batch)
        temp = None
        try:
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
                temp = Path(stream.name)
                json.dump(book, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            read_reviews(temp)
            os.replace(temp, path)
        finally:
            if temp is not None and temp.exists():
                temp.unlink()
    return batch


def migrate_batch(source: Path, output: Path, batch_id: str) -> dict:
    from .human_labels import load_annotations

    annotations = load_annotations(source)  # Existing manifest SHA checks remain authoritative.
    manifest = read_json(source / "manifest.json")
    feedback_raw = (source / "feedback.json").read_bytes().decode("utf-8")
    ballot = json.loads(feedback_raw)
    data = {name: (read_jsonl if filename.endswith(".jsonl") else read_json)(source / filename)
            for name, filename in DOCUMENTS.items()}
    data["feedback_raw"] = feedback_raw
    assert data["annotations"] == annotations
    batch = {"metadata": {
        "batch_id": batch_id, "reviewed_at": None, "feedback_exported_at": ballot.get("exported_at"),
        "recorded_at": utc_now(), "source_run": manifest["source_run"],
        "target_prediction": ballot["target_prediction"],
        "policy": manifest["policy"], "user_authority": manifest["user_authority"],
        "legacy_manifest": manifest,
    }, "data": data}
    return append_batch(output, "news-admission", batch)


def import_review(feedback: Path, run: Path, output: Path, batch_id: str) -> dict:
    from .human_labels import import_prefilter_review

    # Reuse the validated parser. The intermediate is reproducible from inputs;
    # only the complete object-scoped JSON is a persistent human-review asset.
    with TemporaryDirectory(prefix="human-review-import-") as scratch:
        batch = Path(scratch) / "payload"
        import_prefilter_review(feedback, run, batch)
        return migrate_batch(batch, output, batch_id)
