from __future__ import annotations

import gzip
import json
import sqlite3
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from test_aihot_dataset import synthetic_api_item
from test_eval_system_interval import capture

from airadar.db import migrate
from airadar.eval import aihot_dataset as ds
from airadar.fetcher.dedup import FetchedItem, upsert_item
from airadar.sources.loader import SourceConfig
from airadar.sources.sync import sync_to_db

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals._shared import dataset  # noqa: E402
from evals._shared.assets import load_dataset  # noqa: E402


def source(slug="gazette", name="Fictional Gazette", kind="feed"):
    return {"slug": slug, "name": name, "kind": kind, "derived_aihot_identity": f"{kind}:{slug}",
            "aihot_aliases": [], "enabled": True, "paused": False,
            "ai_radar_main_timeline_member": True, "tier": "T1"}


CONTRACT = {"sources": [source()]}


def test_content_identity_matches_real_upsert_for_equal_bodies(tmp_path):
    db = tmp_path / "isolated.db"
    migrate(db)
    with sqlite3.connect(db) as conn:
        sync_to_db([SourceConfig(slug="gazette", name="Gazette", url="https://origin.invalid/feed", tier="T1", enabled=True, meta={})], conn)
        records = [raw("a"), raw("b"), raw("c")]
        records[-1]["extra"] = {"x_post_id": "123"}
        for record in records:
            assert upsert_item(conn, FetchedItem(**record))
        persisted = dict(conn.execute("SELECT url, content_hash FROM items"))
    assert len(set(persisted.values())) == 3
    assert {r["url"]: dataset.raw_content_hash(r) for r in records} == persisted


def hot(item_id="a", time="2026-09-17T07:00:00Z"):
    return ds.parse_api_item(synthetic_api_item(item_id, published_at=time, discovered_at=time))


def raw(item_id="a", time="2026-09-17T07:00:00Z"):
    return {"source_id": "gazette", "url": f"https://origin.invalid/articles/{item_id}",
            "title": item_id, "author": None, "published_at": time, "fetched_at": "2026-09-17T07:00:00Z",
            "content_text": "raw content", "content_html": None, "extra": {}}


def case(item_id="a", time="2026-09-17T07:00:00Z"):
    record = raw(item_id, time)
    return {"case_id": dataset.news_key("gazette", record["url"]), "input": record,
            "reference": {"member": False}, "provenance": {}, "split": "dev"}


def proof(date="Fri, 18 Sep 2026 13:00:00 GMT"):
    return {"passes": [{"raw_pages": [{"date": date}]}, {"raw_pages": [{"date": date}]}]}


class Coverage:
    def __init__(self, complete=True):
        self.complete = complete
        self.calls = 0

    def check(self, begin, end):
        self.calls += 1
        return {"complete": self.complete, "start": begin.isoformat(), "end": end.isoformat()}


def label(rows, passes, *, coverage=None, manifest=None):
    return dataset.admission_cases(rows, all_pass_items=passes, manifest=manifest or proof(),
        mapped={"Fictional Gazette": source()}, contract=CONTRACT, raw_coverage=coverage or Coverage())


def test_non_x_alias_beats_linked_x_account():
    contract = {"sources": [source("hn", "Hacker News"), source("alice", "X Alice (@alice)", "x")]}
    mapped, _ = dataset.resolve_sources([{"upstream_publisher_name": "Hacker News", "original_url": "https://x.com/alice/status/123"}], contract)
    assert mapped["Hacker News"]["slug"] == "hn"


@pytest.mark.parametrize("urls", [["https://x.com/alice/status/1", "https://x.com/bob/status/2"],
                                   ["https://x.com/alice/status/1", "https://example.com/article"]])
def test_x_group_checks_every_url(urls):
    items = [{"upstream_publisher_name": "X Alice (@alice)", "original_url": url} for url in urls]
    items.append({"upstream_publisher_name": "Fictional Gazette", "original_url": "https://origin.invalid/a"})
    mapped, excluded = dataset.resolve_sources(items, {"sources": [source(), source("alice", "X Alice (@alice)", "x")]})
    assert "X Alice (@alice)" not in mapped
    assert excluded


def test_positive_does_not_need_24_hour_coverage():
    coverage = Coverage(False)
    accepted, excluded = label([case()], [[hot()], [hot()]], coverage=coverage,
        manifest=proof("Thu, 17 Sep 2026 13:00:00 GMT"))
    assert accepted[0]["reference"] == {"member": True}
    assert not excluded and coverage.calls == 0


@pytest.mark.parametrize("edge", ["2026-09-16T19:00:00Z", "2026-09-17T19:00:00Z"])
def test_matching_interval_is_strictly_open(edge):
    accepted, excluded = label([case()], [[hot(time=edge)], [hot(time=edge)]])
    assert accepted[0]["reference"] == {"member": False}
    assert not excluded


def test_positive_from_earlier_pass_outside_input_capture_window():
    accepted, _ = label([case(time="2026-09-16T22:00:00Z")], [[hot(time="2026-09-16T23:00:00Z")], [hot("b")]])
    assert accepted[0]["reference"]["member"] is True


def test_missing_published_time_uses_fetched_time():
    accepted, _ = label([case(time=None)], [[hot()], [hot()]])
    assert accepted[0]["reference"]["member"] is True
    assert accepted[0]["provenance"]["admission"]["time_field"] == "fetched_at"


def test_uncertain_x_mapping_cannot_create_negative():
    alice = source("alice", "X Alice (@alice)", "x")
    items = [hot("a"), hot("b")]
    for item, handle in zip(items, ["alice", "bob"]):
        item.upstream_publisher_name = alice["name"]
        item.original_url = f"https://x.com/{handle}/status/123"
    row = case()
    row["input"]["source_id"] = "alice"
    row["input"]["url"] = "https://x.com/alice/status/456"
    row["case_id"] = dataset.news_key("alice", row["input"]["url"])
    included, excluded = dataset.admission_cases([row], all_pass_items=[items, items],
        manifest=proof(), mapped={alice["name"]: alice}, contract={"sources": [alice]}, raw_coverage=Coverage())
    assert included == []
    assert excluded[0]["reason"] == "source_mapping_incomplete"


@pytest.mark.parametrize("mode,reason", [("raw", "radar_window_incomplete"),
    ("future", "aihot_window_not_covered"), ("unstable", "aihot_window_unstable")])
def test_uncertain_negative_excluded(mode, reason):
    passes = [[hot("b")], [hot("c") if mode == "unstable" else hot("b")]]
    accepted, excluded = label([case()], passes, coverage=Coverage(mode != "raw"),
        manifest=proof("Thu, 17 Sep 2026 13:00:00 GMT") if mode == "future" else proof())
    assert accepted == [] and excluded[0]["reason"] == reason


def write_raw(root, begin, end, records, *, failed_source=None):
    stamp = dataset.timestamp(begin)
    while stamp < dataset.timestamp(end):
        run_id = stamp.strftime("%Y%m%dT%H%M%SZ")
        directory = root / "runs" / run_id
        directory.mkdir(parents=True)
        blob = gzip.compress(b"".join((json.dumps(r) + "\n").encode() for r in records))
        (directory / "items.jsonl.gz").write_bytes(blob)
        manifest = {"format": "radar_raw_v1", "run_id": run_id, "started_at": stamp.isoformat(),
            "state": "completed", "items_sha256": ds.sha256_hex(blob), "item_count": len(records),
            "sources": {"gazette": {"status": "success", "item_count": len(records), "configuration_sha256": "same"}}}
        if failed_source:
            manifest["sources"][failed_source] = {"status": "failed", "item_count": 0, "configuration_sha256": "same"}
        (directory / "manifest.json").write_text(json.dumps(manifest))
        stamp += timedelta(minutes=15)


def test_raw_coverage_caches_aligned_window_and_checks_all_sources(tmp_path, monkeypatch):
    write_raw(tmp_path, "2026-09-16T00:00:00Z", "2026-09-17T00:15:00Z", [], failed_source="other")
    coverage = dataset.RawCoverage(tmp_path, {"gazette"})
    begin = dataset.timestamp("2026-09-16T00:00:01Z")
    first = coverage.check(begin, begin + timedelta(days=1))
    assert first["complete"]
    monkeypatch.setattr(dataset, "read_run", lambda *args: pytest.fail("cached window reread raw"))
    assert coverage.check(begin + timedelta(seconds=1), begin + timedelta(days=1, seconds=1)) is first
    # Adding a required source changes the result; excluded-source failures do not.
    coverage.source_ids.add("other")
    coverage.proofs.clear()
    assert not coverage.check(begin, begin + timedelta(days=1))["complete"]


def test_build_separates_o1_and_freezes_outside_input_dependencies(tmp_path):
    reference = capture(tmp_path / "ref")
    root = tmp_path / "raw"
    records = [raw("a"), raw("negative", "2026-09-16T12:00:00Z"), raw("future")]
    write_raw(root, "2026-09-16T00:00:00Z", "2026-09-17T12:00:00Z", records)
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps(CONTRACT))
    result = dataset.build(raw_root=root, reference=reference, version="fixture", data_root=tmp_path / "data", contract_path=contract)
    assert result["counts"]["o1_positive"] == 1
    assert result["counts"]["o1_negative"] == 1
    assert result["counts"]["o1_excluded"] == 1
    primary = Path(result["datasets"]["news-admission"])
    _, admission = load_dataset(primary)
    assert len(admission) == 2 and all(row["input"] is not None for row in admission)
    for target in ("visible-score", "content-enrichment", "featured-members"):
        _, cases = load_dataset(Path(result["datasets"][target]))
        assert len(cases) == 4 and sum(c["input"] is None for c in cases) == 1
    assert (primary / "evidence/radar/runs/20260916T000000Z/manifest.json").is_file()
    assert len((primary / "raw-observations.jsonl").read_text().splitlines()) == 60


def test_build_all_o1_excluded_keeps_shared_evidence_and_raw(tmp_path):
    reference = capture(tmp_path / "ref")
    root = tmp_path / "raw"
    write_raw(root, "2026-09-17T07:00:00Z", "2026-09-17T12:00:00Z", [raw("future")])
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps(CONTRACT))
    result = dataset.build(raw_root=root, reference=reference, version="empty-o1", data_root=tmp_path / "data", contract_path=contract)
    primary = Path(result["datasets"]["news-admission"])
    _, admission = load_dataset(primary)
    assert admission == []
    assert (primary / "evidence/aihot/manifest.json").is_file()
    assert result["counts"]["unique_raw_news"] == 1
    assert len(load_dataset(Path(result["datasets"]["featured-members"]))[1]) == 3
