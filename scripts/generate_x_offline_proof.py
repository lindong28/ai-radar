#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from airadar.audit.receipts import (  # noqa: E402
    X_OFFLINE_PROOF_NODE_IDS,
    X_OFFLINE_PROOF_PRODUCTION_PATHS,
    X_OFFLINE_PROOF_TEST_PATHS,
    atomic_json,
    canonical_payload_sha256,
    validate_x_offline_proof_receipt,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def generate(output: Path) -> dict[str, object]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *X_OFFLINE_PROOF_NODE_IDS],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"controlled X pagination pytest gate failed with exit code {result.returncode}"
        )
    report: dict[str, object] = {
        "schema_version": 1,
        "artifact_type": "x_pagination_offline_test",
        "status": "success",
        "captured_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "pytest_node_ids": list(X_OFFLINE_PROOF_NODE_IDS),
        "pytest_exit_code": result.returncode,
        "pytest_stdout_sha256": hashlib.sha256(result.stdout).hexdigest(),
        "pytest_stderr_sha256": hashlib.sha256(result.stderr).hexdigest(),
        "test_code_sha256_by_path_relative_to_repository_root": {
            path: _sha(ROOT / path) for path in X_OFFLINE_PROOF_TEST_PATHS
        },
        "production_code_sha256_by_path_relative_to_repository_root": {
            path: _sha(ROOT / path) for path in X_OFFLINE_PROOF_PRODUCTION_PATHS
        },
    }
    report["report_payload_sha256"] = canonical_payload_sha256(report)
    validate_x_offline_proof_receipt(report, repository_root=ROOT)
    atomic_json(output, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts/x-pagination-offline-receipt.json",
    )
    args = parser.parse_args()
    generate(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
