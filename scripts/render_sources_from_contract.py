#!/usr/bin/env python3
"""Render data/sources.toml from the reviewed machine source contract."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from airadar.sources.contract import load_source_contract, validate_source_union_receipt  # noqa: E402

CONTRACT = ROOT / "tests/fixtures/aihot_sources.json"
UNION_RECEIPT = ROOT / "artifacts/source-union-receipt.json"


def _quote(value: object) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def render() -> str:
    rows = load_source_contract(CONTRACT)["sources"]
    lines = [
        "# AIHOT original-source alignment pool.",
        "# Generated from tests/fixtures/aihot_sources.json by scripts/render_sources_from_contract.py.",
        "# AIHOT is comparison-only; production fetches only the original endpoints below.",
        "schema_version = 2",
        "",
    ]
    for row in rows:
        lines.extend([
            "[[source]]",
            f"slug = {_quote(row['slug'])}",
            f"name = {_quote(row['name'])}",
            f"fetch_url = {_quote(row['fetch_url'])}",
            f"tier = {_quote(row['tier'])}",
            f"enabled = {'true' if row['enabled'] else 'false'}",
            f"paused = {'true' if row['paused'] else 'false'}",
            f"kind = {_quote(row['kind'])}",
            f"homepage_url = {_quote(row['homepage_url'])}",
            f"icon_url = {_quote(row['icon_url'])}",
        ])
        for key in ("optional", "required_env", "wechat_only", "public_url_override"):
            if key not in row:
                continue
            value = row[key]
            rendered = ("true" if value else "false") if isinstance(value, bool) else _quote(value)
            lines.append(f"{key} = {rendered}")
        meta = row.get("meta") or {}
        if meta:
            lines.append("")
            lines.append("[source.meta]")
            for key, value in meta.items():
                if isinstance(value, bool):
                    rendered = "true" if value else "false"
                elif isinstance(value, (int, float)):
                    rendered = str(value)
                else:
                    rendered = _quote(value)
                lines.append(f"{key} = {rendered}")
        lines.append("")
    return "\n".join(lines)


def render_union_receipt() -> str:
    payload = load_source_contract(CONTRACT)
    rows = [row for row in payload["sources"] if row["ai_radar_main_timeline_member"]]
    receipt = {
        "schema_version": 1,
        "artifact_type": "aihot_source_union",
        "status": "generated_current_contract_projection",
        "contract_sha256": hashlib.sha256(CONTRACT.read_bytes()).hexdigest(),
        "source_counts": {
            "total": len(rows),
            "feed": sum(row["kind"] == "feed" for row in rows),
            "web": sum(row["kind"] == "web" for row in rows),
            "x": sum(row["kind"] == "x" for row in rows),
        },
        "identities": [
            {
                "derived_aihot_identity": row["derived_aihot_identity"], "slug": row["slug"],
            }
            for row in rows
        ],
        "limitations": "Generated projection of the current contract only; it is not per-identity observation or live retrieval evidence.",
    }
    validate_source_union_receipt(receipt, contract_path=CONTRACT)
    return json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    if sys.argv[1:] == ["--write"]:
        (ROOT / "data/sources.toml").write_text(render(), encoding="utf-8")
    elif sys.argv[1:] == ["--write-union-receipt"]:
        UNION_RECEIPT.write_text(render_union_receipt(), encoding="utf-8")
    elif sys.argv[1:]:
        raise SystemExit("usage: render_sources_from_contract.py [--write]")
    else:
        print(render(), end="")
