"""Portable category adjudication material; no inference or gold mutation.

The CLI snapshots C5 evidence, appends attributed model opinions, renders a
static review page, and archives explicit user ballots in the shared store.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from copy import deepcopy
from pathlib import Path

from evals._shared.assets import digest, read_jsonl, utc_now
from evals._shared.human_store import append_batch, read_reviews

FORMAT = "ai-radar-category-review-material-v1"
BALLOT = "ai-radar-category-review-ballot-v1"
OPINIONS = "ai-radar-category-model-opinions-v1"
LABELS = {"ai-models": "模型", "ai-products": "产品", "industry": "行业",
          "paper": "论文", "tip": "教程", "opinion": "观点"}
HERE = Path(__file__).resolve().parent


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_new(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def material_identity(material: dict) -> str:
    """Frozen evidence identity, unchanged when another model adds opinions."""
    return digest({k: v for k, v in material.items() if k != "model_reviews"})


def is_disagreement(case: dict) -> bool:
    pred = case["prediction"]
    return pred["status"] != "ok" or pred.get("category") != case["gold"]


def build(atlas: Path, repo: Path, batch_id: str, scope: str = "all") -> dict:
    manifest = read(atlas / "manifest.json")
    node = next(n for n in manifest["nodes"] if n["id"] == "C5")
    assessments = node["assessments"]
    all_runs = list(dict.fromkeys(r for a in assessments for r in a["runs"]))
    latest = set(assessments[-1]["runs"])
    selected_runs = set(all_runs) if scope == "all" else latest
    runs, records, selected = {}, {}, set()
    for rid in all_runs:
        info = manifest["runs"][rid]
        run = read(atlas / info["file"])
        source = repo / run["source"]
        for filename, expected in info["hashes"].items():
            if sha(source / filename) != expected:
                raise ValueError(f"source hash mismatch: {rid}/{filename}")
        # Verify the projected prediction and prompt against the archived originals.
        raw_predictions = {p["case_id"]: p for p in read_jsonl(source / "predictions.jsonl")}
        raw_prompts = {p["case_id"]: p for p in read_jsonl(source / "prompts.jsonl")}
        raw_cases = {p["case_id"]: p for p in read_jsonl(source / "cases.jsonl")}
        runs[rid] = {"model": run["model"], "source_path": run["source"],
                     "hashes": info["hashes"], "atlas_run_sha256": sha(atlas / info["file"])}
        for c in run["cases"]:
            cid = c["id"]
            if (c["input"] != raw_cases[cid]["input"] or
                    c["gold"] != raw_cases[cid]["reference"]["category"]):
                raise ValueError(f"input/reference projection mismatch: {rid}/{cid}")
            p = raw_predictions[cid]
            if (p["status"] != c["prediction"]["status"] or
                    (p.get("output") or {}).get("category") != c["prediction"].get("category") or
                    (p.get("reason") or "原档未记录 reason") != c["prediction"].get("reason")):
                raise ValueError(f"prediction projection mismatch: {rid}/{cid}")
            prompt = {"system": run["systems"][c["prompt"]["system"]],
                      "user": c["prompt"]["user"]}
            raw = raw_prompts[cid]["prompt"]
            if prompt != {"system": raw["system"], "user": raw["user"]}:
                raise ValueError(f"prompt projection mismatch: {rid}/{cid}")
            if rid in selected_runs and is_disagreement(c):
                selected.add(cid)
            record = records.setdefault(cid, {"case_id": cid, "input": c["input"],
                "input_sha256": digest(c["input"]), "aihot": {"label": c["gold"],
                    "reason": None, "reason_status": "not_recorded"},
                "prompts": {}, "c5_observations": []})
            if record["input"] != c["input"] or record["aihot"]["label"] != c["gold"]:
                raise ValueError(f"input/reference changed across runs: {cid}")
            key = digest(prompt)
            record["prompts"][key] = prompt
            record["c5_observations"].append({"run_id": rid,
                "reason": c["prediction"].get("reason"),
                "label": c["prediction"].get("category"),
                "status": c["prediction"]["status"], "prompt_sha256": key})
    material = {"metadata": {"format": FORMAT, "batch_id": batch_id,
        "created_at": utc_now(), "target": "content-enrichment",
        "benchmark": "aihot-category-navigation", "version": "v1",
        "scope": "atlas-c5-all-disagreements" if scope == "all" else "atlas-c5-latest-disagreements",
        "label_semantics": "acceptable_alternatives_for_single_category",
        "authority": "explicit_user_review_over_nonhuman_reference_for_same_input_and_field"},
        "labels": LABELS, "source": {"candidate": node,
            "atlas_manifest_sha256": sha(atlas / "manifest.json"), "runs": runs},
        "cases": [r for cid, r in records.items() if cid in selected], "model_reviews": []}
    validate(material)
    return material


def valid_labels(labels: list, allow_empty: bool = False) -> None:
    if (not isinstance(labels, list) or any(not isinstance(x, str) for x in labels) or
            len(labels) != len(set(labels)) or not set(labels) <= set(LABELS) or
            (not labels and not allow_empty)):
        raise ValueError("invalid category labels")


def validate_opinions(material: dict, opinion: dict) -> None:
    if opinion["format"] != OPINIONS or opinion["material_identity"] != material_identity(material):
        raise ValueError("model opinion material mismatch")
    reviewer = opinion["reviewer"]
    if reviewer["kind"] != "model" or reviewer["name"] not in {"codex", "claude"}:
        raise ValueError("model opinions cannot claim user authority")
    if not opinion["review_id"] or not opinion["created_at"] or not reviewer["method"]:
        raise ValueError("missing model review provenance")
    cases = {c["case_id"]: c for c in material["cases"]}
    seen = set()
    for row in opinion["judgments"]:
        cid = row["case_id"]
        if cid in seen or cid not in cases or row["input_sha256"] != cases[cid]["input_sha256"]:
            raise ValueError("unknown, duplicate, or changed model case")
        seen.add(cid)
        if row["status"] not in {"judged", "uncertain"}:
            raise ValueError("invalid model status")
        valid_labels(row["acceptable_labels"], allow_empty=row["status"] == "uncertain")
        if not isinstance(row["reason"], str) or not row["reason"].strip():
            raise ValueError("model reason required")
        if row["status"] == "uncertain" and row["acceptable_labels"]:
            raise ValueError("uncertain model response must not assert labels")


def validate(material: dict) -> None:
    if material["metadata"]["format"] != FORMAT or material["labels"] != LABELS:
        raise ValueError("invalid review material format/labels")
    if not material["cases"]:
        raise ValueError("empty review material")
    seen = set()
    for c in material["cases"]:
        if c["case_id"] in seen or digest(c["input"]) != c["input_sha256"]:
            raise ValueError("duplicate case or input hash mismatch")
        seen.add(c["case_id"])
        valid_labels([c["aihot"]["label"]])
        if not c["c5_observations"]:
            raise ValueError("missing C5 observation")
        for o in c["c5_observations"]:
            if o["run_id"] not in material["source"]["runs"]:
                raise ValueError("unknown source run")
            prompt = c["prompts"][o["prompt_sha256"]]
            if digest(prompt) != o["prompt_sha256"]:
                raise ValueError("prompt hash mismatch")
            if o["status"] == "ok":
                valid_labels([o["label"]])
    ids = set()
    for review in material["model_reviews"]:
        validate_opinions(material, review)
        if review["review_id"] in ids:
            raise ValueError("duplicate model review id")
        ids.add(review["review_id"])


def add_opinions(material: dict, opinion: dict) -> dict:
    validate(material)
    validate_opinions(material, opinion)
    result = deepcopy(material)
    for old in result["model_reviews"]:
        if old["review_id"] == opinion["review_id"]:
            if old != opinion:
                raise ValueError("review id already exists with different opinions")
            return result
    result["model_reviews"].append(opinion)
    return result


def render(material_path: Path, output: Path) -> None:
    material = read(material_path)
    validate(material)
    output.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(material_path, output / "material.json")
    write_new(output / "identity.json", {"material_identity": material_identity(material),
                                         "material_sha256": digest(material)})
    for filename in ("index.html", "app.js", "style.css"):
        shutil.copyfile(HERE / "human_review" / filename, output / filename)


def import_ballot(material: dict, ballot_path: Path, output: Path, authority: str) -> dict:
    validate(material)
    raw = ballot_path.read_text(encoding="utf-8")
    ballot = json.loads(raw)
    if (ballot["format"] != BALLOT or ballot["material_identity"] != material_identity(material) or
            ballot["material_sha256"] != digest(material) or
            ballot["batch_id"] != material["metadata"]["batch_id"]):
        raise ValueError("ballot does not match the exact displayed material revision")
    if not authority.strip():
        raise ValueError("user confirmation provenance required")
    if not isinstance(ballot["exported_at"], str) or not ballot["exported_at"]:
        raise ValueError("missing ballot export time")
    cases = {c["case_id"]: c for c in material["cases"]}
    seen, judgments = set(), []
    for row in ballot["judgments"]:
        cid = row["case_id"]
        if cid not in cases or cid in seen or row["input_sha256"] != cases[cid]["input_sha256"]:
            raise ValueError("unknown, duplicate, or changed human case")
        seen.add(cid)
        if row["status"] not in {"reviewed", "pending", "uncertain"}:
            raise ValueError("invalid human status")
        valid_labels(row["acceptable_labels"], allow_empty=row["status"] != "reviewed")
        if row["status"] != "reviewed" and row["acceptable_labels"]:
            raise ValueError("unreviewed case must not assert labels")
        if not isinstance(row["reason"], str):
            raise ValueError("invalid human reason")
        if row["status"] == "reviewed":
            judgments.append({"target": "content-enrichment", "case_id": cid,
                "field": "acceptable_categories", "input_identity": cases[cid]["input_sha256"],
                "reason": row["reason"], "value": row["acceptable_labels"], "provenance": "user"})
    if seen != set(cases):
        raise ValueError("ballot must include every case, including pending cases")
    # Separate from scalar annotations: legacy category consumers must not turn
    # an acceptable-alternatives set into a single reference behind the user's back.
    batch = {"metadata": {"batch_id": "category-" + digest(ballot),
        "kind": "category-acceptable-labels", "reviewed_at": None,
        "feedback_exported_at": ballot["exported_at"], "recorded_at": utc_now(),
        "user_authority": authority, "material_identity": material_identity(material),
        "material_sha256": digest(material)}, "data": {"feedback_raw": raw,
        "material": material, "annotations": [], "category_judgments": judgments}}
    return append_batch(output, "content-enrichment", batch)


def accepted_categories(reviews_path: Path) -> dict[tuple[str, str], frozenset[str]]:
    """Explicit human alternatives, keyed by (case_id, exact input identity).

    Consumers may test prediction in this set. This does NOT rescore existing
    runs or rewrite scalar category references; conflicting human sets raise.
    """
    book = read_reviews(reviews_path)
    if book["metadata"]["target"] != "content-enrichment":
        raise ValueError("not content-enrichment reviews")
    resolved = {}
    for batch in book["batches"]:
        if batch["metadata"].get("kind") != "category-acceptable-labels":
            continue
        for j in batch["data"]["category_judgments"]:
            if j["provenance"] != "user" or j["field"] != "acceptable_categories":
                raise ValueError("not an explicit human category judgment")
            valid_labels(j["value"])
            key = (j["case_id"], j["input_identity"])
            value = frozenset(j["value"])
            if key in resolved and resolved[key] != value:
                raise ValueError(f"conflicting human category sets: {j['case_id']}")
            resolved[key] = value
    return resolved


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build_cmd = sub.add_parser("build")
    build_cmd.add_argument("--atlas", type=Path, required=True)
    build_cmd.add_argument("--repo", type=Path, required=True)
    build_cmd.add_argument("--batch-id", required=True)
    build_cmd.add_argument("--scope", choices=["all", "latest"], default="all")
    build_cmd.add_argument("--output", type=Path, required=True)
    for name in ("validate", "render", "add-opinions", "import-ballot"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--material", type=Path, required=True)
        if name != "validate":
            cmd.add_argument("--output", type=Path, required=True)
        if name == "add-opinions":
            cmd.add_argument("--opinions", type=Path, required=True)
        if name == "import-ballot":
            cmd.add_argument("--ballot", type=Path, required=True)
            cmd.add_argument("--user-authority", required=True)
    args = parser.parse_args()
    if args.command == "build":
        material = build(args.atlas, args.repo, args.batch_id, args.scope)
        write_new(args.output, material)
    else:
        material = read(args.material)
        validate(material)
        if args.command == "render":
            render(args.material, args.output)
        elif args.command == "add-opinions":
            material = add_opinions(material, read(args.opinions))
            write_new(args.output, material)
        elif args.command == "import-ballot":
            imported = import_ballot(material, args.ballot, args.output, args.user_authority)
            print(f"已归档用户原票：{len(imported['data']['category_judgments'])} 条明确多选标注；未改原 gold。")
    print(f"{len(material['cases'])} 道复核材料；{len(material['model_reviews'])} 批模型意见；未运行模型评测。")
    print(f"material_identity={material_identity(material)}")
    print(f"material_sha256={digest(material)}")


if __name__ == "__main__":
    main()
