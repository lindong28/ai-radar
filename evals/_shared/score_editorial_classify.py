"""Reuse a frozen P3 classifier without paying for its unused scoring stage."""
from __future__ import annotations

import argparse
import json
import signal
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from airadar.provider.deepseek_chat import _parse_json_object
from . import assets
from .cli import transport_factory
from .score_context_study import arm_deadline, check_identity
from .score_editorial import diagnosis
from .score_eval import object_identity
from .score_weights import source_rows


def classify(baseline, candidate_run, output, env_file, source_root, workers=8):
    if not 1 <= workers <= 8:
        raise ValueError("workers must be 1..8")
    output.mkdir(parents=True, exist_ok=False)
    source, metadata, cases, _, hashes = source_rows(baseline, root=source_root)
    config, prompt = (assets.read_json(candidate_run / name) for name in ("config.json", "prompt.json"))
    old = assets.read_json(candidate_run / "started.json")["object_identity"]
    factory = transport_factory(config, env_file)
    frozen = check_identity(Path(metadata["dataset"]), config, prompt, output, "editorial-classifier",
                            "five-editorial", "frozen P3 classifier only; ADR f6b3")
    if frozen != old:
        raise ValueError("classifier candidate changed after development")
    driver_hash = assets.file_digest(Path(__file__))
    inputs = {r["case_id"]: r["prompt"]["user"] for r in assets.read_jsonl(source / "prompts.jsonl")}
    assets.write_json(output / "started.json", {"kind": "editorial-classification-only", "source_run": str(source),
        "source_hashes": hashes, "object_identity": frozen, "case_ids": [c["case_id"] for c in cases],
        "started_at": assets.utc_now(), "workers": workers, "driver_sha256": driver_hash})
    chat = factory(output / "attempts")
    def one(case):
        key = case["case_id"]
        request = frozen["request"]
        p = {"system": prompt["editorial_system"], "user": inputs[key]}
        row = {"case_id": key, "editorial_call": {"prompt": p, "request": request}}
        try:
            response = chat(key)(stage="score-editorial", prompt=p, request=request)
            row["editorial_call"].update({k: response.get(k) for k in (
                "raw", "usage", "model", "provider", "requested_model", "attempt_id")},
                response_json=json.dumps(response["json"], ensure_ascii=False))
            diagnosis(response["json"])
            row["status"] = "ok"
        except Exception as exc:
            row.update(status="error", error_type=type(exc).__name__)
        assets.write_json(output / "items" / f"{key}.json", row)
        return row
    with ThreadPoolExecutor(max_workers=workers) as executor:
        rows = list(executor.map(one, cases))
    assets.write_jsonl(output / "classifications.jsonl", rows)
    result = {"complete": all(r["status"] == "ok" for r in rows), "count": len(rows),
              "identity_unchanged": (frozen == object_identity(config, prompt, "five-editorial")
                                     and driver_hash == assets.file_digest(Path(__file__))),
              "ended_at": assets.utc_now()}
    assets.write_json(output / "result.json", result)
    if not result["complete"] or not result["identity_unchanged"]:
        raise ValueError("classification incomplete or identity drift")
    return result


def load(directory, cases, hashes):
    meta, result = (assets.read_json(directory / name) for name in ("started.json", "result.json"))
    if meta["source_hashes"] != hashes or not result["complete"] or not result["identity_unchanged"]:
        raise ValueError("classification source changed or incomplete")
    rows = assets.read_jsonl(directory / "classifications.jsonl")
    if [r["case_id"] for r in rows] != [c["case_id"] for c in cases]:
        raise ValueError("classification cases differ")
    for row in rows:
        call = row["editorial_call"]
        payload = diagnosis(_parse_json_object(call["raw"]["choices"][0]["message"]["content"]))
        if row["status"] != "ok" or json.loads(call["response_json"]) != payload:
            raise ValueError("classification response mismatch")
    return meta, rows, {n: assets.file_digest(directory / n) for n in (
        "started.json", "classifications.jsonl", "result.json")}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("baseline", "candidate-run", "output", "env-file", "source-root"):
        p.add_argument("--" + name, type=Path, required=True)
    a = p.parse_args()
    arm_deadline()
    result = classify(a.baseline, a.candidate_run, a.output, a.env_file, a.source_root)
    signal.alarm(0)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
