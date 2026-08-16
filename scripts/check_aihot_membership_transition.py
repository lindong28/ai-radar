#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from airadar.audit.receipts import validate_archived_observation_receipt  # noqa: E402


def _identities(path: Path) -> set[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("sources", payload) if isinstance(payload, dict) else payload
    return {
        str(row["derived_aihot_identity"])
        for row in rows
        if row.get("ai_radar_main_timeline_member", True)
    }


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _referenced_file(root: Path, relative: object, digest: object, label: str) -> Path:
    resolved_root = root.resolve()
    path = (resolved_root / str(relative)).resolve()
    if not path.is_relative_to(resolved_root) or not path.is_file() or _sha(path) != digest:
        raise ValueError(f"{label} retirement evidence is missing or hash-invalid")
    return path


def _validate_authorization(row: dict[str, object], *, root: Path) -> None:
    expected_row = {
        "derived_aihot_identity", "evidence_class", "previous_contract_sha256", "next_contract_sha256",
        "evidence_path_relative_to_ledger_dir", "evidence_sha256",
    }
    if set(row) != expected_row:
        raise ValueError("invalid retirement authorization reference fields")
    path = _referenced_file(
        root,
        row["evidence_path_relative_to_ledger_dir"],
        row["evidence_sha256"],
        str(row["evidence_class"]),
    )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError("retirement authorization evidence must be parseable JSON") from exc
    expected_payload = {
        "schema_version", "artifact_type", "derived_aihot_identity", "action", "evidence_class",
        "previous_contract_sha256", "next_contract_sha256", "authorized_at", "allows_retirement",
    }
    evidence_class = row["evidence_class"]
    if evidence_class in {"official_shutdown", "official_migration"}:
        expected_payload |= {
            "official_evidence_artifact_path_relative_to_ledger_dir",
            "official_evidence_artifact_sha256",
            "official_evidence_source_url",
        }
    if not isinstance(payload, dict) or set(payload) != expected_payload:
        raise ValueError("invalid retirement authorization evidence fields")
    if payload["schema_version"] != 1 or payload["artifact_type"] != "aihot_retirement_evidence":
        raise ValueError("invalid retirement authorization evidence schema")
    if payload["action"] != "retire":
        raise ValueError("retirement evidence action must be retire")
    if payload["allows_retirement"] is not True:
        raise ValueError("retirement evidence must allow retirement")
    for field in ("derived_aihot_identity", "evidence_class", "previous_contract_sha256", "next_contract_sha256"):
        if payload[field] != row[field]:
            raise ValueError(f"retirement evidence {field} does not match ledger identity/action binding")
    try:
        authorized_at = datetime.fromisoformat(str(payload["authorized_at"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid retirement authorization time") from exc
    if authorized_at.tzinfo is None:
        raise ValueError("invalid retirement authorization time")
    if evidence_class in {"official_shutdown", "official_migration"}:
        official_path = _referenced_file(
            root,
            payload["official_evidence_artifact_path_relative_to_ledger_dir"],
            payload["official_evidence_artifact_sha256"],
            "official evidence artifact",
        )
        if not official_path.read_bytes().strip():
            raise ValueError("official evidence artifact must be non-empty")
        parsed = urlparse(str(payload["official_evidence_source_url"]))
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("official evidence source URL must be HTTP(S)")


def _validate_thirty_day_absence(
    row: dict[str, object],
    *,
    root: Path,
) -> None:
    expected = {
        "derived_aihot_identity", "evidence_class", "previous_contract_sha256", "next_contract_sha256",
        "observation_artifacts",
    }
    if set(row) != expected:
        raise ValueError("invalid thirty_day_absence evidence fields")
    references = row["observation_artifacts"]
    if not isinstance(references, list) or len(references) != 30:
        raise ValueError("thirty_day_absence needs exactly 30 observation artifacts")
    paths: set[str] = set()
    dates: set[str] = set()
    target = str(row["derived_aihot_identity"])
    for reference in references:
        if not isinstance(reference, dict) or set(reference) != {
            "artifact_path_relative_to_ledger_dir", "artifact_sha256",
            "contract_artifact_path_relative_to_ledger_dir", "contract_artifact_sha256",
        }:
            raise ValueError("invalid thirty_day_absence observation reference")
        relative = str(reference["artifact_path_relative_to_ledger_dir"])
        if relative in paths:
            raise ValueError("thirty_day_absence cannot reuse a duplicate artifact")
        paths.add(relative)
        path = _referenced_file(root, relative, reference["artifact_sha256"], "thirty_day_absence")
        historical_contract = _referenced_file(
            root,
            reference["contract_artifact_path_relative_to_ledger_dir"],
            reference["contract_artifact_sha256"],
            "thirty_day_absence contract artifact",
        )
        if reference["contract_artifact_sha256"] != row["previous_contract_sha256"]:
            raise ValueError("thirty_day_absence observation contract must match the previous contract")
        receipt = validate_archived_observation_receipt(
            json.loads(path.read_text(encoding="utf-8")),
            contract_path=historical_contract,
        )
        reconciliation = receipt["reconciliation"]
        if receipt["status"] != "success" or reconciliation["ambiguous"] or reconciliation["unmapped"] or reconciliation["conflicting"]:
            raise ValueError("thirty_day_absence requires successful reconciliation receipts")
        captured = datetime.fromisoformat(str(receipt["captured_at"]).replace("Z", "+00:00")).astimezone(UTC)
        dates.add(captured.date().isoformat())
        observed_identities = {
            str(item["identity"])
            for key in ("matched", "renamed")
            for item in reconciliation[key]
        }
        if target in observed_identities:
            raise ValueError("thirty_day_absence target identity is still present")
    if len(dates) != 30:
        raise ValueError("thirty_day_absence needs 30 distinct UTC receipt dates")
    ordered_dates = sorted(dates)
    first_date = datetime.fromisoformat(ordered_dates[0]).date()
    last_date = datetime.fromisoformat(ordered_dates[-1]).date()
    if (last_date - first_date).days != 29:
        raise ValueError("thirty_day_absence needs 30 consecutive UTC receipt dates")


def _validate_evidence(row: dict[str, object], *, root: Path) -> None:
    evidence = row.get("evidence_class")
    if evidence in {"user_decision", "official_shutdown", "official_migration"}:
        _validate_authorization(row, root=root)
    elif evidence == "thirty_day_absence":
        _validate_thirty_day_absence(row, root=root)
    else:
        raise ValueError("invalid retirement evidence_class")
    for key in ("derived_aihot_identity", "evidence_class", "previous_contract_sha256", "next_contract_sha256"):
        if not isinstance(row.get(key), str) or not row[key]:
            raise ValueError("invalid retirement evidence identity/hash fields")


def check_transition(
    previous: set[str],
    next_identities: set[str],
    retirements: list[dict[str, object]],
    *,
    previous_sha256: str | None = None,
    next_sha256: str | None = None,
    root: Path = Path("."),
    contract_path: Path | None = None,
) -> None:
    identities = [str(row.get("derived_aihot_identity")) for row in retirements]
    if len(identities) != len(set(identities)):
        raise ValueError("duplicate retirement identity")
    for row in retirements:
        _validate_evidence(row, root=root)
        if previous_sha256 and row["previous_contract_sha256"] != previous_sha256:
            raise ValueError("retirement authorization previous contract hash mismatch")
        if next_sha256 and row["next_contract_sha256"] != next_sha256:
            raise ValueError("retirement authorization next contract hash mismatch")
    by_identity = {str(row.get("derived_aihot_identity")): row for row in retirements}
    unauthorized = []
    for identity in sorted(previous - next_identities):
        retirement = by_identity.get(identity, {})
        if not retirement:
            unauthorized.append(identity)
    if unauthorized:
        raise ValueError(f"unauthorized AIHOT identity removal: {unauthorized}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--previous", type=Path, required=True)
    parser.add_argument("--next", dest="next_path", type=Path, required=True)
    parser.add_argument("--retirements", type=Path, required=True)
    args = parser.parse_args()
    ledger = json.loads(args.retirements.read_text(encoding="utf-8"))
    if not isinstance(ledger, dict) or set(ledger) != {"schema_version", "artifact_type", "retirements"} or ledger.get("schema_version") != 1 or ledger.get("artifact_type") != "aihot_retirement_ledger":
        raise ValueError("invalid retirement ledger schema")
    check_transition(
        _identities(args.previous),
        _identities(args.next_path),
        ledger["retirements"],
        previous_sha256=_sha(args.previous),
        next_sha256=_sha(args.next_path),
        root=args.retirements.parent,
        contract_path=args.next_path,
    )


if __name__ == "__main__":
    main()
