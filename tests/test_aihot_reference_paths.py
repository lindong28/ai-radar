from __future__ import annotations

import importlib.util
import inspect
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from airadar.eval.aihot_dataset import capture_dataset
from airadar.eval.aihot_fit.build import _portable_source_path

ROOT = Path(__file__).resolve().parents[1]
REFERENCE_PATHS = ("data/aihot-reference", "benchmarks/aihot")


def test_capture_defaults_use_reference_archive() -> None:
    spec = importlib.util.spec_from_file_location("capture_cli", ROOT / "scripts/capture_aihot_dataset.py")
    assert spec is not None and spec.loader is not None
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    for args in (
        ["capture", "--start", "2026-09-18", "--end", "2026-09-20"],
        ["freeze", "--window", "example", "--destination", "frozen"],
    ):
        assert cli._parser().parse_args(args).output_root == Path("data/aihot-reference")
    assert inspect.signature(capture_dataset).parameters["output_root"].default == "data/aihot-reference"


@pytest.mark.parametrize("reference_path", REFERENCE_PATHS)
def test_new_manifest_source_is_portable_from_either_checkout(reference_path: str) -> None:
    source = Path("/separate/checkout") / reference_path / "windows/day/items.jsonl"
    assert _portable_source_path(source, "t2-window-day") == "data/aihot-reference/windows/day/items.jsonl"


@pytest.mark.parametrize("reference_path", REFERENCE_PATHS)
@pytest.mark.parametrize("script", ["collect_aihot_supervised.sh", "check_collection_health.sh"])
def test_wrappers_read_target_runtime_submodule_path(
    tmp_path: Path, reference_path: str, script: str
) -> None:
    owner = tmp_path / "owner"
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / ".gitmodules").write_text(
        f'[submodule "benchmarks/aihot"]\n\tpath = {reference_path}\n\turl = ../local-data\n'
    )
    scripts = owner / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(ROOT / "scripts" / script, scripts / script)
    interpreter = owner / ".venv/bin/python"
    interpreter.parent.mkdir(parents=True)
    argv_file = tmp_path / "argv.json"
    interpreter.write_text(
        f"#!{sys.executable}\nimport json, sys\nfrom pathlib import Path\n"
        f"Path({str(argv_file)!r}).write_text(json.dumps(sys.argv[1:]))\n"
    )
    interpreter.chmod(0o755)

    subprocess.run(
        ["bash", str(scripts / script)],
        env={**os.environ, "AIHOT_CAPTURE_WORKTREE": str(runtime)},
        check=True,
        timeout=10,
    )

    argv = json.loads(argv_file.read_text())
    assert argv[argv.index("--aihot-root") + 1] == str(runtime / reference_path)


@pytest.mark.parametrize("reference_path", REFERENCE_PATHS)
@pytest.mark.parametrize("fill_missing", [False, True])
def test_daily_capture_passes_resolved_output_root_without_network(
    tmp_path: Path, reference_path: str, fill_missing: bool
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
    subprocess.run(["git", "init", "-q", str(runtime)], env=env, check=True)
    (runtime / ".gitmodules").write_text(
        f'[submodule "benchmarks/aihot"]\n\tpath = {reference_path}\n\turl = ../local-data\n'
    )
    # Exit before publication so this check cannot invoke push or remote discovery.
    argv_file = tmp_path / "capture-argv.json"
    capture = tmp_path / "capture.py"
    capture.write_text(
        "import json, sys\nfrom pathlib import Path\n"
        f"Path({str(argv_file)!r}).write_text(json.dumps(sys.argv[1:]))\n"
        "sys.exit(2)\n"
    )
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/capture_aihot_daily.sh")],
        env={
            **env,
            "AIHOT_CAPTURE_WORKTREE": str(runtime),
            "AIHOT_CAPTURE_LOG_DIR": str(tmp_path / "logs"),
            "AIHOT_CAPTURE_RETAIN_DAYS": "0",
            "AIHOT_CAPTURE_FILL_MISSING": str(int(fill_missing)),
            "AIHOT_CAPTURE_CMD": f'"{sys.executable}" "{capture}"',
        },
        timeout=10,
    )

    assert result.returncode == 2
    argv = json.loads(argv_file.read_text())
    assert argv[argv.index("--output-root") + 1] == reference_path
    assert ("--fill-missing" in argv) is fill_missing


def test_reference_gitlink_uses_new_path_and_preserves_section_name() -> None:
    result = subprocess.run(
        ["git", "ls-files", "--stage", "--", *REFERENCE_PATHS],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    records = result.stdout.splitlines()
    assert len(records) == 1
    metadata, path = records[0].split("\t")
    mode, _object_id, stage = metadata.split()
    assert (mode, stage, path) == ("160000", "0", "data/aihot-reference")
    configured = subprocess.run(
        ["git", "config", "-f", ".gitmodules", "--get", "submodule.benchmarks/aihot.path"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert configured.stdout.strip() == "data/aihot-reference"


def test_populated_reference_move_can_fast_forward_without_changing_pin(tmp_path: Path) -> None:
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
    }

    def git(cwd: Path, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(cwd), *args],
            env=env,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()

    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-q")
    (source / "capture.txt").write_bytes(b"frozen reference bytes\n")
    git(source, "add", "capture.txt")
    git(source, "commit", "-qm", "fixture")
    pin = git(source, "rev-parse", "HEAD")
    main = tmp_path / "main"
    main.mkdir()
    git(main, "init", "-q", "-b", "main")
    git(main, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(source), "benchmarks/aihot")
    git(main, "commit", "-qm", "fixture")
    candidate = tmp_path / "candidate"
    git(main, "worktree", "add", "-qb", "migration", str(candidate))
    git(candidate, "config", "-f", ".gitmodules", "submodule.benchmarks/aihot.path", "data/aihot-reference")
    git(candidate, "update-index", "--add", "--cacheinfo", f"160000,{pin},data/aihot-reference")
    git(candidate, "update-index", "--force-remove", "benchmarks/aihot")
    (candidate / "data/aihot-reference").mkdir(parents=True)
    git(candidate, "add", ".gitmodules")
    git(candidate, "commit", "-qm", "move reference")

    (main / "data").mkdir()
    git(main, "mv", "benchmarks/aihot", "data/aihot-reference")
    git(main, "merge", "--ff-only", "migration")

    moved = main / "data/aihot-reference"
    assert git(moved, "rev-parse", "HEAD") == pin
    assert Path(git(moved, "rev-parse", "--show-toplevel")).resolve() == moved.resolve()
    assert (moved / "capture.txt").read_bytes() == b"frozen reference bytes\n"
    assert git(main, "status", "--porcelain") == ""
