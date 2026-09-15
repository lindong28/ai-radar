#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from airadar.eval.aihot_dataset import (  # noqa: E402
    DatasetContractError,
    canonical_json_bytes,
    capture_dataset,
    slice_persisted_capture,
    validate_persisted_artifact,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Capture and replay the private AIHOT benchmark dataset.")
    subcommands = parser.add_subparsers(dest="command", required=True)

    capture = subcommands.add_parser("capture", help="Capture two complete UTC days into the data repository.")
    capture.add_argument("--start", required=True)
    capture.add_argument("--end", required=True)
    capture.add_argument("--output-root", type=Path, default=Path("benchmarks/aihot"))
    capture.add_argument("--fill-missing", action="store_true", help="Publish only missing complete UTC days as v2; validate existing windows.")

    slice_command = subcommands.add_parser("slice", help="Create a deterministic offline JSONL slice.")
    slice_command.add_argument("--capture", required=True)
    slice_command.add_argument("--start", required=True)
    slice_command.add_argument("--end", required=True)
    slice_command.add_argument("--output", type=Path, required=True)

    validate = subcommands.add_parser("validate", help="Validate a persisted capture or window offline.")
    validate.add_argument("path", nargs="?")
    validate.add_argument("--report-json", metavar="PATH")
    frozen = subcommands.add_parser("freeze", help="Copy validated windows and all their capture dependencies; no data deletion.")
    frozen.add_argument("--output-root", type=Path, default=Path("benchmarks/aihot"))
    frozen.add_argument("--window", action="append", required=True)
    frozen.add_argument("--destination", type=Path, required=True)
    return parser


def _artifact_location(value: str, *, expected_top_level: str | None = None) -> tuple[Path, str]:
    path = Path(value)
    parts = path.parts
    candidate_indices = [
        index
        for index, part in enumerate(parts)
        if part in ({expected_top_level} if expected_top_level is not None else {"captures", "windows"})
    ]
    if len(candidate_indices) != 1:
        raise DatasetContractError(
            "reference_invalid",
            "artifact path must contain exactly one captures/ or windows/ root",
        )
    index = candidate_indices[0]
    root = Path(*parts[:index]) if index else Path(".")
    relative_path = PurePosixPath(*parts[index:]).as_posix()
    return root, relative_path


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "freeze":
            import json
            paths: set[str] = set()
            for subject in args.window:
                validate_persisted_artifact(args.output_root, subject)
                payload = json.loads((args.output_root / subject).read_bytes())
                if payload.get("artifact_type") not in {"aihot_window_v1", "aihot_window_v2"}:
                    raise DatasetContractError("window_invalid", "freeze requires window manifests")
                paths.add(str(PurePosixPath(subject).parent))
                paths.add(str(PurePosixPath(payload["capture"]["path"]).parent))
            target = args.destination.resolve()
            if target.is_relative_to(args.output_root.resolve() / "captures") or target.is_relative_to(args.output_root.resolve() / "windows"):
                raise DatasetContractError("reference_invalid", "freeze destination must be outside rolling capture/window roots")
            target.mkdir(parents=True, exist_ok=False)
            for path in sorted(paths):
                shutil.copytree(args.output_root / path, target / path)
            for subject in args.window:
                validate_persisted_artifact(target, subject)
            print(f"Frozen {len(args.window)} validated windows with complete capture dependencies: {target}. No source files deleted.")
            return 0
        if args.command == "capture":
            result = capture_dataset(
                start=args.start,
                end=args.end,
                output_root=args.output_root,
                **({"fill_missing": True} if args.fill_missing else {}),
            )
            print(f"Capture published locally: {result.capture_path}")
            print("Validated windows:")
            for path in result.window_manifest_paths:
                print(f"- {path}")
            print("No remote push or repository integration was performed.")
            return 0
        if args.command == "slice":
            if args.output.exists() or args.output.is_symlink():
                raise DatasetContractError("target_exists", f"refusing to overwrite slice output: {args.output}")
            output_root, capture_path = _artifact_location(
                args.capture,
                expected_top_level="captures",
            )
            value = slice_persisted_capture(
                output_root,
                capture_path=capture_path,
                start=args.start,
                end=args.end,
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("xb") as output:
                output.write(value)
            print(
                f"Offline slice written: output={args.output}; source_capture={args.capture}; "
                f"window=[{args.start}, {args.end}); bytes={len(value)}"
            )
            return 0
        subject_path = args.report_json or args.path
        if subject_path is None or (args.report_json is not None and args.path is not None):
            parser.error("validate requires exactly one subject path, either positional or via --report-json")
        output_root, relative_subject_path = _artifact_location(subject_path)
        report = validate_persisted_artifact(output_root, relative_subject_path)
        if args.report_json is not None:
            sys.stdout.buffer.write(canonical_json_bytes(report) + b"\n")
        else:
            print(f"Validation passed: {subject_path}")
            print("The report covers persisted public/API replay and the selected subject only.")
        return 0
    except DatasetContractError as error:
        if error.code == "no_missing_windows":
            print("No new capture published: every requested day already has a validated window. No action needed.")
            return 0
        print(f"ERROR {error}", file=sys.stderr)
        recovery_actions = {
            "target_exists": "Choose a new output path and retry; existing files are never overwritten.",
            "tool_checkout_dirty": (
                "Retry from a clean tool checkout; the capture records the checkout's exact HEAD."
            ),
            "reference_missing": (
                "Check that the subject path and its referenced artifacts exist under the dataset root."
            ),
        }
        print(
            recovery_actions.get(
                error.code,
                "Fix the named error, then retry; no successful artifact or validation result was produced.",
            ),
            file=sys.stderr,
        )
        return 2
    except OSError as error:
        print(f"ERROR filesystem_failed: {error}", file=sys.stderr)
        print("No successful artifact was published; verify the path and filesystem permissions.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
