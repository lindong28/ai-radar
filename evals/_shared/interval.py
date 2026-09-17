"""Capture a closed observation interval without changing daily archive contracts."""
from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from airadar.eval import aihot_dataset as ds

ARTIFACT_TYPE = "aihot_interval_v1"


def _read(root: Path, relative: str) -> bytes:
    ds._validate_relative_path(relative)
    target = (root / relative).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError("interval reference escapes its root")
    return target.read_bytes()


def _replay_pass(root: Path, payload: dict[str, Any], window: dict[str, str], capture_id: str, pass_index: int):
    pages = payload["raw_pages"]
    if not pages or payload["reached_has_more_false"] is not True:
        raise ValueError("interval API pass is not terminal")
    for index, page in enumerate(pages):
        expected_path = f"captures/{capture_id}/raw/api/pass-{pass_index:02d}/page-{index:03d}.json.gz"
        if page["raw_path"] != expected_path:
            raise ValueError("API page does not belong to its declared pass and position")
    position = 0
    dates = [ds._http_date(page["date"]) for page in pages]
    if dates != sorted(dates):
        raise ValueError("API response dates decrease")

    def fetch(cursor):
        nonlocal position
        if position >= len(pages):
            raise ValueError("API pagination ended before terminal page")
        page = pages[position]
        position += 1
        expected = {"mode": "all", "by": "timeline", "window": "7d", "limit": 100, "cursor": cursor}
        if page["canonical_query"] != expected:
            raise ValueError("API cursor chain differs from archived requests")
        compressed = _read(root, page["raw_path"])
        body = ds._read_compressed_raw(
            path=page["raw_path"], expected_raw_sha256=page["compressed_raw_sha256"],
            expected_body_sha256=page["response_body_sha256"],
            raw_files={page["raw_path"]: compressed},
        )
        if page["status"] != 200:
            raise ValueError("API response was not successful")
        return ds.HttpResponse(page["status"], {"Content-Type": page["content_type"], "Date": page["date"]}, body)

    traversal = ds.traverse_api_pages(
        fetch, limiter=ds.GlobalRateLimiter(monotonic=lambda: 0.0, sleep=lambda _: None), max_attempts=1,
    )
    if position != len(pages):
        raise ValueError("API pass contains pages after its terminal response")
    ds.ensure_window_covered(
        start=window["start_inclusive"], end=window["end_exclusive"],
        first_response_date=pages[0]["date"], last_response_date=pages[-1]["date"],
    )
    return ds.filter_window(traversal.items, start=window["start_inclusive"], end=window["end_exclusive"])


def validate_interval(path: str | Path) -> dict[str, Any]:
    """Replay all API pages and bound SSR bytes; return the verified manifest."""
    path = Path(path)
    if path.is_dir():
        path = path / "manifest.json"
    root = path.parent
    manifest = json.loads(path.read_bytes())
    if manifest["artifact_type"] != ARTIFACT_TYPE:
        raise ValueError("not an AIHOT interval artifact")
    window = manifest["window"]
    if window["time_basis"] != "aihot_timeline_v1":
        raise ValueError("unknown interval time basis")
    for relative, digest in manifest["raw_sha256"].items():
        if ds.sha256_hex(_read(root, relative)) != digest:
            raise ValueError("archived raw response hash mismatch")
    passes = manifest["passes"]
    canonical = manifest["canonical_pass_index"]
    if not 2 <= len(passes) <= 3 or canonical != len(passes) - 1:
        raise ValueError("interval requires two or three passes and the last canonical pass")
    targets = [_replay_pass(root, p, window, manifest["capture_id"], index) for index, p in enumerate(passes)]
    if {i.id for i in targets[-2]} != {i.id for i in targets[-1]}:
        raise ValueError("interval target membership changed between adjacent passes")
    for previous, current in zip(passes, passes[1:]):
        if ds._http_date(previous["raw_pages"][-1]["date"]) > ds._http_date(current["raw_pages"][0]["date"]):
            raise ValueError("API response dates decrease between passes")
    responses = [ds.SsrTagObservationResponse.model_validate(r) for r in manifest["tag_observation_responses"]]
    bindings = [ds.SsrTagObservationBinding.model_validate(b) for b in manifest["tag_observation_bindings"]]
    raw_files = {r.response_raw_path: _read(root, r.response_raw_path) for r in responses}
    observations = ds._load_ssr_tag_observations(
        SimpleNamespace(tag_observation_responses=responses, tag_observation_bindings=bindings),
        capture=SimpleNamespace(capture_id=manifest["capture_id"], source=ds.SourceIdentity.model_validate(manifest["source"])),
        raw_files=raw_files, target_item_ids=[i.id for i in targets[-1]],
    )
    expected = ds.serialize_items_jsonl(ds.reconcile_tags(targets[-1], observations).items)
    reference = manifest["items"]
    actual = _read(root, reference["path"])
    if ds.sha256_hex(actual) != reference["sha256"] or actual != expected:
        raise ValueError("interval items differ from raw API/SSR replay")
    return manifest


def _capture(writer: ds.CaptureWriter, start: str, end: str, root: Path) -> Path:
    window = {"start_inclusive": start, "end_exclusive": end, "time_basis": "aihot_timeline_v1"}
    ds.filter_window([], start=start, end=end)
    root.mkdir(parents=True, exist_ok=False)
    started = datetime.now(UTC).isoformat()
    raw_files: dict[str, bytes] = {}
    passes = []
    previous = None
    for index in range(3):
        traversal, payload, _ = writer._capture_pass(index, root, raw_files)
        passes.append(payload)
        ds.ensure_window_covered(start=start, end=end,
            first_response_date=payload["raw_pages"][0]["date"], last_response_date=payload["raw_pages"][-1]["date"])
        targets = ds.filter_window(traversal.items, start=start, end=end)
        ids = {i.id for i in targets}
        if previous is not None and ids == previous:
            break
        previous = ids
    else:
        raise ValueError("interval target membership was unstable across three passes")
    observations, references = writer._capture_ssr_observations(target_items=targets, staging_root=root, raw_files=raw_files)
    projected, _, items_bytes = writer._window_payload(
        start=start, end=end, capture_path=f"captures/{writer.capture_id}/capture.json", capture_bytes=b"",
        items=targets, observations=observations, references=references,
    )
    manifest = {
        "artifact_type": ARTIFACT_TYPE, "capture_id": writer.capture_id,
        "window": window, "source": {"base_url": writer.base_url},
        "started_at": started, "finished_at": datetime.now(UTC).isoformat(),
        "producer": {
            "git_commit": subprocess.check_output(["git", "-C", str(writer.tool_repo_root), "rev-parse", "HEAD"], text=True).strip(),
            "interval_code_sha256": ds.sha256_hex(Path(__file__).read_bytes()),
            "dataset_code_sha256": ds.sha256_hex(Path(ds.__file__).read_bytes()),
        },
        "passes": passes, "canonical_pass_index": len(passes) - 1,
        "raw_sha256": {name: ds.sha256_hex(body) for name, body in raw_files.items()},
        "items": {"path": "items.jsonl", "sha256": ds.sha256_hex(items_bytes)},
        "tag_observation_responses": projected["tag_observation_responses"],
        "tag_observation_bindings": projected["tag_observation_bindings"],
    }
    (root / "items.jsonl").write_bytes(items_bytes)
    path = root / "manifest.json"
    path.write_bytes(ds.canonical_json_bytes(manifest))
    validate_interval(path)
    return path


def capture_interval(start: str, end: str, output_root: str | Path) -> Path:
    """Write one new interval capture; existing targets are never overwritten."""
    capture_id = f"interval-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    root = Path(output_root)
    user_agent = "AI-Radar-AIHOT-Interval-Capture/1.0"
    transport = ds.HttpxTransport(timeout_seconds=30, user_agent=user_agent)
    writer = ds.CaptureWriter(
        tool_repo_root=Path(__file__).resolve().parents[2], output_root=root,
        base_url="https://aihot.news", user_agent=user_agent, transport=transport,
        limiter=ds.GlobalRateLimiter(), now=lambda: datetime.now(UTC), capture_id=capture_id,
        schema_bytes=(Path(ds.__file__).parent / "schemas/aihot-item-v1.schema.json").read_bytes(),
    )
    try:
        return _capture(writer, start, end, root)
    finally:
        transport.close()
