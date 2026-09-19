"""Publish legacy object datasets under consumer-contract names, without rebuilding cases.

Run from the repository root with PYTHONPATH=src:. . No model or collector calls.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from evals._shared.assets import (
    BENCHMARKS,
    DEFAULT_DATA_ROOT,
    OBJECT_BENCHMARKS,
    dataset_version,
    digest,
    file_digest,
    load_dataset,
    utc_now,
    write_json,
)


def migrate(sources: dict[str, Path], data_root: Path, version: str = "v1") -> dict:
    """Copy immutable payloads, adjusting only manifest identity and evidence location."""
    dataset_version(version)
    if not sources:
        raise ValueError("at least one source dataset is required")
    prepared = []
    for target, source in sources.items():
        source = source.resolve()
        manifest, _ = load_dataset(source, target)
        if manifest["schema_version"] != 2 or manifest["benchmark"] != BENCHMARKS[target]:
            raise ValueError("migration requires a legacy-named schema 2 object dataset")
        mode = "pointwise-threshold" if target == "featured-members" else "pointwise"
        if manifest.get("evaluation_mode") != mode:
            raise ValueError(f"consumer contract differs for {target}")
        destination = data_root.resolve() / target / OBJECT_BENCHMARKS[target] / version
        if destination.exists():
            raise FileExistsError(f"destination already exists; not overwritten: {destination}")
        prepared.append((source, destination, manifest, file_digest(source / "manifest.json")))

    evidence_copies = {}
    result = {}
    # All leaves share one disk and may share evidence. Copy each bundle once.
    for source, destination, manifest, source_sha in prepared:
        destination.mkdir(parents=True, exist_ok=False)
        for name in manifest["files"]:
            output = destination / name
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, output)
        evidence_key = digest(manifest["evidence_files"])
        if evidence_key not in evidence_copies:
            origin = (source / manifest["shared_evidence"]).resolve()
            evidence = destination / "evidence"
            for name in manifest["evidence_files"]:
                output = evidence / name
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(origin / name, output)
            evidence_copies[evidence_key] = evidence
        published = {
            **manifest,
            "benchmark": OBJECT_BENCHMARKS[manifest["target"]],
            "version": version,
            "shared_evidence": os.path.relpath(evidence_copies[evidence_key], destination),
            "migration": {
                "source_dataset": str(source),
                "source_manifest_sha256": source_sha,
                "published_at": utc_now(),
            },
        }
        write_json(destination / "manifest.json", published)
        load_dataset(destination, manifest["target"])
        if file_digest(source / "manifest.json") != source_sha:
            raise ValueError(f"source changed during migration: {source}")
        result[manifest["target"]] = {"path": str(destination), "case_count": manifest["case_count"]}
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="迁移独立题库命名；保留题目字节与旧资产，不运行模型。")
    parser.add_argument("--source", action="append", required=True, metavar="TARGET=PATH")
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--version", default="v1")
    args = parser.parse_args(argv)
    try:
        sources = {}
        for entry in args.source:
            target, separator, value = entry.partition("=")
            if not separator or not value or target not in BENCHMARKS or target in sources:
                raise ValueError(f"source must have a distinct known TARGET=PATH: {entry}")
            sources[target] = Path(value).expanduser()
        result = migrate(sources, args.data_root.expanduser(), args.version)
    except (OSError, ValueError, KeyError) as exc:
        print(f"题库命名迁移未完成：{exc}。旧资产未修改；检查目标目录与源 manifest 后重试。", file=sys.stderr)
        return 1
    print("题库命名迁移完成；题目内容未重建，旧题库与历史成绩未修改，未运行模型评测。")
    for target, item in result.items():
        print(f"{target}：{item['case_count']} 题；{item['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
