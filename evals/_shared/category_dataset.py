"""Build six-way gold from observed website membership, never from legacy API tip."""
from __future__ import annotations

import argparse
import json
import re
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from airadar.enrich.classification import PRIMARY_CATEGORY_SLUGS

from . import assets
from .identity import identity_url, substantive_hash


def page_items(body: bytes) -> list[dict]:
    """Read actual Next RSC item objects; navigation links are not news membership."""
    chunks = []
    for match in re.finditer(r"self\.__next_f\.push\((.*?)\)</script>", body.decode(), re.S):
        value = json.loads(match.group(1))
        if len(value) > 1 and isinstance(value[1], str):
            chunks.append(value[1])
    decoded, found = "".join(chunks), {}
    decoder = json.JSONDecoder()
    for match in re.finditer(r'\{"id":', decoded):
        try:
            value, _ = decoder.raw_decode(decoded, match.start())
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("id") and value.get("title") and "aiTags" in value:
            found[value["id"]] = value
    if not found:
        raise ValueError("no news objects parsed; not evidence of an empty category")
    return list(found.values())


def observations(directory: Path) -> tuple[dict, dict]:
    result, files = defaultdict(list), {}
    for meta_path in sorted(directory.glob("*.response.json")):
        meta = assets.read_json(meta_path)
        url = urlsplit(meta["url"])
        category = parse_qs(url.query).get("category", [None])[0]
        if url.netloc != "aihot.news" or url.path not in {"/all", "/api/public/feed"}:
            continue
        if category not in PRIMARY_CATEGORY_SLUGS.values() or meta["status"] != 200:
            continue
        body_path = meta_path.with_name(meta_path.name.removesuffix(".response.json") + ".body")
        if assets.file_digest(body_path) != meta["sha256"]:
            raise ValueError("captured body hash differs")
        body = body_path.read_bytes()
        rows = (json.loads(body)["items"] if url.path == "/api/public/feed" else page_items(body))
        for row in rows:
            result[row["id"]].append({"category": category, "url": meta["url"],
                "observed_at": meta["finished_at"], "body_sha256": meta["sha256"],
                "source_url": row["url"]})
        files[body_path.name], files[meta_path.name] = body_path, meta_path
    if not result:
        raise ValueError("no successful category-filtered website observations")
    return result, files


def build(inputs: list[Path], captures: list[Path], bases: list[Path], version: str,
          data_root: Path = assets.DEFAULT_DATA_ROOT) -> dict:
    target, benchmark = "content-enrichment", assets.CATEGORY_NAVIGATION
    version = assets.dataset_version(version)
    output = data_root / target / benchmark / version
    if output.exists():
        raise FileExistsError(output)
    candidates, votes, proof = defaultdict(list), defaultdict(list), {}
    source_hashes = []
    for source in inputs + bases:
        manifest, cases = assets.load_dataset(source, target)
        if manifest["benchmark"] not in {assets.OBJECT_BENCHMARKS[target], benchmark}:
            raise ValueError("unsupported original-input source")
        source_hashes.append({"path": str(source.resolve()), "sha256": assets.file_digest(source / "manifest.json")})
        for case in cases:
            candidates[case["case_id"]].append(case)
        if manifest["benchmark"] == benchmark:
            previous = assets.read_json(source / "observations.json")
            for item, values in previous.items():
                votes[item].extend(values)
            for name in manifest["evidence_files"]:
                proof[name] = source / manifest["shared_evidence"] / name
        prefix = "inputs/" + assets.file_digest(source / "manifest.json")
        for name in ("manifest.json", "cases.jsonl"):
            proof[prefix + "/" + name] = source / name
    for capture in captures:
        current, files = observations(capture)
        for item, values in current.items():
            votes[item].extend(values)
        for name, path in files.items():
            proof["navigation/" + assets.file_digest(path) + "/" + name] = path
    cases, excluded = [], []
    for key, variants in sorted(candidates.items()):
        if len({substantive_hash(c["input"], target) for c in variants}) != 1:
            excluded.append({"case_id": key, "reason": "substantive_input_conflict"})
            continue
        case = variants[-1]
        ids = {a["item_id"] for c in variants for a in c["provenance"].get("aihot", [])}
        observed = [v for i in ids for v in votes.get(i, [])]
        if any(identity_url(v["source_url"]) != identity_url(case["input"]["url"]) for v in observed):
            excluded.append({"case_id": key, "reason": "source_url_mismatch"})
            continue
        categories = {v["category"] for v in observed}
        if len(categories) != 1:
            excluded.append({"case_id": key, "reason": "conflicting_navigation" if categories else "not_observed_in_navigation"})
            continue
        cases.append({**case, "reference": {"category": categories.pop()},
                      "provenance": {**case["provenance"], "category_observations": observed}})
    if not cases:
        raise ValueError("no usable matched category questions")
    output.mkdir(parents=True)
    for name, source in proof.items():
        dest = output / "evidence" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
    assets.write_jsonl(output / "cases.jsonl", cases)
    assets.write_jsonl(output / "excluded.jsonl", excluded)
    assets.write_json(output / "observations.json", votes)
    counts = dict(Counter(c["reference"]["category"] for c in cases))
    manifest = {"schema_version": 2, "target": target, "benchmark": benchmark, "version": version,
        "created_at": assets.utc_now(), "evaluation_mode": "pointwise", "case_count": len(cases),
        "counts": {"category": counts, "split": dict(Counter(c["split"] for c in cases))},
        "files": {n: assets.file_digest(output / n) for n in ("cases.jsonl", "excluded.jsonl", "observations.json")},
        "shared_evidence": "evidence", "evidence_files": {n: assets.file_digest(output / "evidence" / n) for n in proof},
        "source_datasets": source_hashes, "builder_sha256": assets.file_digest(Path(__file__)),
        "reference_semantics": "membership observed on six AIHOT category filters; conflicts excluded; absence is not a negative",
        "rebuild": {"inputs": [str(p.resolve()) for p in inputs], "captures": [str(p.resolve()) for p in captures],
                    "bases": [str(p.resolve()) for p in bases]},
        "split_policy": "inherit source per-news stable dev/regression split; no gold-based resampling"}
    assets.write_json(output / "manifest.json", manifest)
    assets.load_dataset(output, target)
    return {"dataset": str(output), "cases": len(cases), "counts": manifest["counts"],
            "excluded": dict(Counter(c["reason"] for c in excluded))}


def main():
    parser = argparse.ArgumentParser(description="从 AIHOT 六分类页面成员建题，合并去重，不猜标签。")
    parser.add_argument("--inputs", type=Path, action="append", default=[])
    parser.add_argument("--capture", type=Path, action="append", default=[])
    parser.add_argument("--base", type=Path, action="append", default=[])
    parser.add_argument("--version", required=True)
    parser.add_argument("--data-root", type=Path, default=assets.DEFAULT_DATA_ROOT)
    args = parser.parse_args()
    print(json.dumps(build(args.inputs, args.capture, args.base, args.version, args.data_root), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
