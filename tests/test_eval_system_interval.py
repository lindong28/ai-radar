from __future__ import annotations

import copy
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_aihot_dataset import FakeClock, page_payload, response, synthetic_api_item

from airadar.eval import aihot_dataset as ds

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals._shared import interval  # noqa: E402

START = "2026-09-17T07:00:00Z"
END = "2026-09-17T12:00:00Z"


class Transport:
    def __init__(self, memberships=None, *, missing_tags=False):
        self.memberships = memberships or [["a", "b"], ["a", "b"]]
        self.pass_index = -1
        self.missing_tags = missing_tags

    def get(self, url, *, params, headers):
        if url.endswith("/api/v1/items"):
            if "cursor" not in params:
                self.pass_index += 1
                ids = self.memberships[self.pass_index][:1]
                more = True
            else:
                ids = self.memberships[self.pass_index][1:]
                more = False
            items = [synthetic_api_item(i, published_at=START, discovered_at=START) for i in ids]
            # The upper-bound item must never enter the closed-observation interval.
            if not more:
                items.append(synthetic_api_item("outside", published_at=END, discovered_at=END))
            return response(ds, page_payload(items, has_more=more, next_cursor="next" if more else None),
                headers={"Date": "Thu, 17 Sep 2026 13:00:00 GMT"})
        if url.endswith("/all"):
            ids = self.memberships[self.pass_index] if params["page"] == 1 else []
        else:
            ids = []
        if self.missing_tags:
            ids = []
        cards = "".join(f'<article class="timeline-item" data-aihot-id="{i}" '
            f'data-aihot-url="https://aihot.invalid/items/{i}">'
            f'<span class="topic-tag">tag-{i}</span></article>' for i in ids)
        return ds.HttpResponse(200, {"Content-Type": "text/html"}, f"<html>{cards}</html>".encode())


def capture(tmp_path, *, memberships=None, missing_tags=False, start=START, end=END):
    root = tmp_path / "interval"
    clock = FakeClock()
    writer = ds.CaptureWriter(tool_repo_root=Path(__file__).resolve().parents[1], output_root=root,
        base_url="https://aihot.invalid", user_agent="test", transport=Transport(memberships, missing_tags=missing_tags),
        limiter=ds.GlobalRateLimiter(monotonic=clock.monotonic, sleep=clock.sleep),
        now=lambda: datetime.now(UTC), capture_id="fixture-interval", schema_bytes=b"")
    return interval._capture(writer, start, end, root)


def test_interval_round_trip_is_half_open_and_relocatable(tmp_path):
    path = capture(tmp_path)
    manifest = interval.validate_interval(path)
    assert manifest["artifact_type"] == "aihot_interval_v1"
    rows = [json.loads(line) for line in path.with_name("items.jsonl").read_text().splitlines()]
    assert {i["id"] for i in rows} == {"a", "b"}
    assert {tuple(i["tags"]) for i in rows} == {("tag-a",), ("tag-b",)}
    moved = tmp_path / "moved"
    path.parent.rename(moved)
    assert interval.validate_interval(moved)["window"]["end_exclusive"] == END


def test_third_pass_must_stabilize_requested_interval(tmp_path):
    path = capture(tmp_path, memberships=[["a"], ["b"], ["b"]])
    assert interval.validate_interval(path)["canonical_pass_index"] == 2


def test_empty_interval_is_explicit_success(tmp_path):
    path = capture(tmp_path, memberships=[[], []])
    assert path.with_name("items.jsonl").read_bytes() == b""
    assert interval.validate_interval(path)["tag_observation_bindings"] == []


def test_three_unstable_passes_do_not_publish_manifest(tmp_path):
    with pytest.raises(ValueError, match="unstable"):
        capture(tmp_path, memberships=[["a"], ["b"], ["c"]])
    assert not (tmp_path / "interval/manifest.json").exists()


@pytest.mark.parametrize("start,end", [(END, START), (START, "2026-09-17T14:00:00Z")])
def test_invalid_or_unclosed_window_fails(tmp_path, start, end):
    with pytest.raises(ds.DatasetContractError):
        capture(tmp_path, start=start, end=end)


def test_missing_ssr_reference_fails(tmp_path):
    with pytest.raises(ds.DatasetContractError):
        capture(tmp_path, missing_tags=True)


@pytest.mark.parametrize("mutation", ["raw", "items", "cursor", "terminal", "date", "bindings", "escape", "repeated_pass"])
def test_offline_replay_rejects_corruption(tmp_path, mutation):
    path = capture(tmp_path)
    m = json.loads(path.read_bytes())
    page = m["passes"][0]["raw_pages"][0]
    if mutation == "raw":
        (path.parent / page["raw_path"]).write_bytes(b"broken")
    elif mutation == "items":
        (path.parent / "items.jsonl").write_bytes(b"")
        m["items"]["sha256"] = ds.sha256_hex(b"")
    elif mutation == "cursor":
        m["passes"][0]["raw_pages"][1]["canonical_query"]["cursor"] = "wrong"
    elif mutation == "terminal":
        m["passes"][0]["raw_pages"].pop()
    elif mutation == "date":
        page["date"] = "Thu, 17 Sep 2026 13:00:01 GMT"
    elif mutation == "bindings":
        m["tag_observation_bindings"].pop()
    elif mutation == "repeated_pass":
        m["passes"][1] = copy.deepcopy(m["passes"][0])
    else:
        m["items"]["path"] = "../outside.jsonl"
    path.write_bytes(ds.canonical_json_bytes(m))
    with pytest.raises((ValueError, ds.DatasetContractError)):
        interval.validate_interval(path)
