from __future__ import annotations

import os
import subprocess
from collections import Counter
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = "benchmarks/aihot"
SYNTHETIC_OBJECT_ID = "0123456789abcdef0123456789abcdef01234567"


def _parse_index_records(index_entries: bytes) -> list[tuple[str, str, str]]:
    if not index_entries:
        return []
    if not index_entries.endswith(b"\0"):
        raise ValueError("malformed git index output: missing NUL terminator")

    records: list[tuple[str, str, str]] = []
    for position, raw_record in enumerate(index_entries[:-1].split(b"\0"), start=1):
        if not raw_record:
            raise ValueError(f"malformed git index record {position}: empty record")
        try:
            metadata, raw_path = raw_record.split(b"\t", 1)
        except ValueError as error:
            raise ValueError(
                f"malformed git index record {position}: missing metadata/path separator"
            ) from error

        metadata_fields = metadata.split()
        if len(metadata_fields) != 3:
            raise ValueError(
                f"malformed git index record {position}: expected mode object stage"
            )
        if not raw_path:
            raise ValueError(f"malformed git index record {position}: empty path")

        try:
            mode, _object_id, stage = (
                field.decode("ascii") for field in metadata_fields
            )
        except UnicodeDecodeError as error:
            raise ValueError(
                f"malformed git index record {position}: non-ASCII metadata"
            ) from error
        if not stage.isdecimal():
            raise ValueError(
                f"malformed git index record {position}: non-decimal stage {stage!r}"
            )

        tracked_path = os.fsdecode(raw_path)
        if tracked_path != DATASET_PATH and not tracked_path.startswith(
            f"{DATASET_PATH}/"
        ):
            raise ValueError(
                f"unexpected git index path outside dataset boundary: {tracked_path!r}"
            )
        records.append((mode, stage, tracked_path))

    return records


def _dataset_index_violations(index_entries: bytes) -> list[str]:
    records = _parse_index_records(index_entries)
    violations: list[str] = []
    path_counts = Counter(tracked_path for _, _, tracked_path in records)

    for mode, stage, tracked_path in records:
        if stage != "0":
            violations.append(f"{tracked_path!r} has stage {stage}, expected 0")
        if tracked_path == DATASET_PATH:
            if mode != "160000":
                violations.append(
                    f"{tracked_path!r} has mode {mode}, expected 160000"
                )
        elif tracked_path.startswith(f"{DATASET_PATH}/"):
            violations.append(
                "dataset content is tracked as a superproject blob: "
                f"{tracked_path!r}"
            )

    for tracked_path, count in path_counts.items():
        if count > 1:
            violations.append(
                f"{tracked_path!r} has {count} index entries, expected at most 1"
            )

    return violations


def test_aihot_dataset_index_boundary_accepts_only_absence_or_gitlink() -> None:
    result = subprocess.run(
        ["git", "ls-files", "--stage", "-z", "--", DATASET_PATH],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )

    assert _dataset_index_violations(result.stdout) == []


def test_aihot_dataset_index_boundary_accepts_single_stage_zero_gitlink() -> None:
    synthetic_index = (
        f"160000 {SYNTHETIC_OBJECT_ID} 0\t{DATASET_PATH}\0".encode()
    )

    assert _dataset_index_violations(synthetic_index) == []


def test_aihot_dataset_index_boundary_rejects_exact_path_regular_blob() -> None:
    synthetic_index = (
        f"100644 {SYNTHETIC_OBJECT_ID} 0\t{DATASET_PATH}\0".encode()
    )

    assert _dataset_index_violations(synthetic_index) == [
        "'benchmarks/aihot' has mode 100644, expected 160000"
    ]


def test_aihot_dataset_index_boundary_rejects_unusual_character_subpath() -> None:
    unusual_path = 'benchmarks/aihot/windows/tab\tline\nquote"/items.jsonl'
    synthetic_index = (
        f"100644 {SYNTHETIC_OBJECT_ID} 0\t{unusual_path}\0".encode()
    )

    assert _dataset_index_violations(synthetic_index) == [
        "dataset content is tracked as a superproject blob: "
        "'benchmarks/aihot/windows/tab\\tline\\nquote\"/items.jsonl'"
    ]


def test_aihot_dataset_index_boundary_rejects_nonzero_and_duplicate_stages() -> None:
    synthetic_index = (
        f"160000 {SYNTHETIC_OBJECT_ID} 1\t{DATASET_PATH}\0"
        f"160000 {SYNTHETIC_OBJECT_ID} 2\t{DATASET_PATH}\0"
    ).encode()

    assert _dataset_index_violations(synthetic_index) == [
        "'benchmarks/aihot' has stage 1, expected 0",
        "'benchmarks/aihot' has stage 2, expected 0",
        "'benchmarks/aihot' has 2 index entries, expected at most 1",
    ]


@pytest.mark.parametrize(
    ("synthetic_index", "expected_error"),
    [
        (b"malformed without terminator", "missing NUL terminator"),
        (b"malformed without separator\0", "missing metadata/path separator"),
        (b"100644 object-only\tbenchmarks/aihot\0", "expected mode object stage"),
    ],
)
def test_aihot_dataset_index_boundary_rejects_malformed_records(
    synthetic_index: bytes,
    expected_error: str,
) -> None:
    with pytest.raises(ValueError, match=expected_error):
        _dataset_index_violations(synthetic_index)
