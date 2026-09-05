from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..egress import (
    EgressPreflightError,
    require_selector_policy,
    reset_selector_policy_cache,
)
from .runner import (
    EGRESS_CONTRACT_FILE,
    egress_implementation_sha256,
    expected_selector_compatibility_receipt,
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class PolicyChangedError(RuntimeError):
    def __init__(self, tested_policy_sha256: str, live_policy_sha256: str) -> None:
        self.tested_policy_sha256 = tested_policy_sha256
        self.live_policy_sha256 = live_policy_sha256
        super().__init__(
            "production policy changed after attestation "
            f"({tested_policy_sha256} != {live_policy_sha256})"
        )


class ImplementationChangedError(RuntimeError):
    def __init__(
        self,
        tested_implementation_sha256: str,
        live_implementation_sha256: str,
    ) -> None:
        self.tested_implementation_sha256 = tested_implementation_sha256
        self.live_implementation_sha256 = live_implementation_sha256
        super().__init__(
            "egress implementation changed after attestation "
            f"({tested_implementation_sha256} != {live_implementation_sha256})"
        )


@dataclass(frozen=True, slots=True)
class ReceiptWriteResult:
    receipt_path: Path
    backup_path: Path | None
    policy_sha256: str
    egress_implementation_sha256: str


def _receipt_backup_timestamp() -> str:
    return datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")


def _validate_sha256(name: str, value: str) -> None:
    if not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{name} must be 64 lowercase hexadecimal characters")


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary_path.chmod(0o644)
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def write_selector_compatibility_receipt(
    *,
    assistant_root: Path,
    tested_policy_sha256: str,
    tested_implementation_sha256: str,
) -> ReceiptWriteResult:
    """Write a v2 receipt only while the attested identities remain live."""

    _validate_sha256("tested_policy_sha256", tested_policy_sha256)
    _validate_sha256("tested_implementation_sha256", tested_implementation_sha256)
    root = assistant_root.expanduser().resolve()
    live_implementation_sha256 = egress_implementation_sha256(root)
    if live_implementation_sha256 != tested_implementation_sha256:
        raise ImplementationChangedError(
            tested_implementation_sha256,
            live_implementation_sha256,
        )

    reset_selector_policy_cache()
    live_policy = require_selector_policy()
    if live_policy.policy_sha256 != tested_policy_sha256:
        raise PolicyChangedError(tested_policy_sha256, live_policy.policy_sha256)

    receipt_path = root / EGRESS_CONTRACT_FILE
    backup_path: Path | None = None
    if receipt_path.exists():
        backup_path = receipt_path.with_name(
            f"{receipt_path.name}.bak-{_receipt_backup_timestamp()}"
        )
        if backup_path.exists():
            raise FileExistsError(f"receipt backup already exists: {backup_path}")
        shutil.copy2(receipt_path, backup_path)

    payload = expected_selector_compatibility_receipt(
        policy_sha256=live_policy.policy_sha256,
        egress_implementation_sha256=live_implementation_sha256,
    )
    _atomic_write_json(receipt_path, payload)
    return ReceiptWriteResult(
        receipt_path=receipt_path,
        backup_path=backup_path,
        policy_sha256=live_policy.policy_sha256,
        egress_implementation_sha256=live_implementation_sha256,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Write an attested selector compatibility receipt after a live identity check."
    )
    parser.add_argument("--assistant-root", required=True, type=Path)
    parser.add_argument("--tested-policy-sha", required=True)
    parser.add_argument("--tested-implementation-sha", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = write_selector_compatibility_receipt(
            assistant_root=args.assistant_root,
            tested_policy_sha256=args.tested_policy_sha,
            tested_implementation_sha256=args.tested_implementation_sha,
        )
    except PolicyChangedError as exc:
        print(
            "Receipt not written: production policy changed after attestation.\n"
            f"Attested policy SHA-256: {exc.tested_policy_sha256}\n"
            f"Live policy SHA-256: {exc.live_policy_sha256}\n"
            "Impact: receipt was not modified.\n"
            "Next: rerun the full attestation against the live production policy.",
            file=sys.stderr,
        )
        return 1
    except ImplementationChangedError as exc:
        print(
            "Receipt not written: implementation changed after attestation.\n"
            f"Attested implementation SHA-256: {exc.tested_implementation_sha256}\n"
            f"Live implementation SHA-256: {exc.live_implementation_sha256}\n"
            "Impact: receipt was not modified.\n"
            "Next: rerun the full attestation against the current implementation closure.",
            file=sys.stderr,
        )
        return 1
    except EgressPreflightError as exc:
        print(
            "Receipt not written: production selector preflight failed.\n"
            f"Detail: {exc}\n"
            "Impact: receipt was not modified.\n"
            "Next: restore a healthy production selector status, then rerun attestation.",
            file=sys.stderr,
        )
        return 1
    except (OSError, ValueError) as exc:
        print(
            "Receipt write failed before completion.\n"
            f"Detail: {type(exc).__name__}: {exc}\n"
            "Impact: a completed receipt write was not confirmed; inspect the target directory "
            "for partial side effects.\n"
            "Next: resolve the error and rerun the writer with the same attested identities.",
            file=sys.stderr,
        )
        return 1

    backup = str(result.backup_path) if result.backup_path is not None else "none"
    print(
        "Receipt written successfully.\n"
        f"Policy SHA-256: {result.policy_sha256}\n"
        f"Implementation SHA-256: {result.egress_implementation_sha256}\n"
        f"Receipt: {result.receipt_path}\n"
        f"Backup: {backup}\n"
        "Impact: the v2 receipt now binds the attested implementation to the live policy.\n"
        "Next: run the AI Radar consumer preflight and confirm it returns ok."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
