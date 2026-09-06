from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from airadar import cli
from airadar.curator.dedup import deduplicate_candidates
from airadar.curator.score import ScoredCandidate, weighted_score
from airadar.curator.select import DEFAULT_SOURCE_QUOTA, SourceQuota, curate, parse_source_quota
from airadar.curator.weights import DEFAULT_WEIGHTS, Weights, load_weights, weights_from_mapping
from airadar.db import migrate


def test_ranking_no_longer_multiplies_by_source_tier() -> None:
    """Retired 2026-09-06, deliberately: the multiplier ordered the tiers backwards.

    It was T1 1.25 / T1.5 1.0 / T2 0.75, and AIHOT -- the reference this ranking is now fitted
    to -- scores those tiers 44.68 / 39.82 / 51.68. Carrying it on the fitted vector costs 0.093
    Spearman. A vector that wants it back sets uses_tier_multiplier, which is what the case below
    checks still works.
    """
    numeric = {"relevance": 10.0, "density": 8.0, "recency": 6.0, "authority": 4.0, "engineering": 2.0}
    numeric_with_new_signal = {**numeric, "significance": 5.0}

    assert weighted_score(numeric_with_new_signal, DEFAULT_WEIGHTS, "T1") == pytest.approx(
        weighted_score(numeric_with_new_signal, DEFAULT_WEIGHTS, "T2")
    )
    assert weighted_score(numeric_with_new_signal, DEFAULT_WEIGHTS, "T1") == pytest.approx(6.1)


def test_a_vector_can_still_ask_for_the_tier_multiplier() -> None:
    weights = Weights(relevance=0.2, density=0.4, recency=0.2, authority=0.1, engineering=0.1)
    keeps_tier = Weights(
        relevance=0.2, density=0.4, recency=0.2, authority=0.1, engineering=0.1, uses_tier_multiplier=True
    )
    numeric = {"relevance": 10.0, "density": 8.0, "recency": 6.0, "authority": 4.0, "engineering": 2.0}

    assert weighted_score(numeric, weights, "T1") == pytest.approx(7.0)
    assert weighted_score(numeric, keeps_tier, "T1") == pytest.approx(8.75)


def test_load_weights_rejects_zero_or_negative_totals(tmp_path: Path) -> None:
    zero = tmp_path / "weights_zero.json"
    zero.write_text(json.dumps({"relevance": 0, "density": 0, "recency": 0, "authority": 0, "engineering": 0}))
    negative = tmp_path / "weights_negative.json"
    negative.write_text(json.dumps({"relevance": -1, "density": 1, "recency": 0, "authority": 0, "engineering": 0}))

    with pytest.raises(ValueError):
        load_weights(zero)
    with pytest.raises(ValueError):
        load_weights(negative)


def test_deduplicate_candidates_keeps_highest_score_by_hash_and_url() -> None:
    low = ScoredCandidate(1, "item-low", "h1", "https://example.com/a", "2026-05-08T00:00:00Z", 7.0, {})
    high_same_hash = ScoredCandidate(2, "item-high", "h1", "https://example.com/b", "2026-05-08T00:00:00Z", 8.0, {})
    low_same_url = ScoredCandidate(3, "item-url-low", "h2", "https://example.com/b", "2026-05-08T00:00:00Z", 7.5, {})
    unique = ScoredCandidate(4, "item-unique", "h3", "https://example.com/c", "2026-05-08T00:00:00Z", 6.5, {})

    deduped = deduplicate_candidates([low, high_same_hash, low_same_url, unique])

    assert [candidate.item_id for candidate in deduped] == ["item-high", "item-unique"]


def _setup_curator_db(tmp_path: Path, count: int = 35) -> sqlite3.Connection:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO sources (id,name,url,tier,enabled,meta_json,synced_at) VALUES ('s','S','https://example.com','T1.5',1,'{}','2026-05-08T00:00:00Z')"
    )
    for idx in range(count):
        item_id = f"item-{idx:02d}"
        score = 9.0 if idx < 28 else 6.49
        if idx == 29:
            score = 6.6
        if idx == 30:
            score = 6.5
        content_hash = f"h-{idx:02d}"
        conn.execute(
            """
            INSERT INTO items (
              id, source_id, url, title, author, published_at, fetched_at,
              content_text, content_html, content_hash, extra_json
            )
            VALUES (?, 's', ?, ?, NULL, '2026-05-08T00:00:00Z', '2026-05-08T00:00:00Z', 'content', NULL, ?, '{}')
            """,
            (item_id, f"https://example.com/{content_hash}", item_id, content_hash),
        )
        conn.execute(
            """
            INSERT INTO item_evaluations (
              item_id, stage, ruleset_version, model_id, input_json, output_json,
              numeric_json, latency_ms, cost_usd, evaluated_at, error
            )
            VALUES (?, 'scoring', 'test.r1', 'fake', '{}', '{}', ?, 1, 0, '2026-05-08T00:00:00Z', NULL)
            """,
            (
                item_id,
                json.dumps(
                    {
                        "relevance": score,
                        "density": score,
                        "recency": score,
                        "authority": score,
                        "engineering": score,
                        "reasoning": "ok",
                    }
                ),
            ),
        )
    conn.commit()
    return conn


def _setup_quota_db(
    tmp_path: Path,
    candidates: list[tuple[str, str, str, float, datetime | None]],
) -> sqlite3.Connection:
    db_path = tmp_path / "quota.db"
    migrate(db_path)
    conn = sqlite3.connect(db_path)
    for source_id, kind in sorted({(source_id, kind) for _, source_id, kind, _, _ in candidates}):
        conn.execute(
            """
            INSERT INTO sources (id, name, url, tier, enabled, kind, meta_json, synced_at)
            VALUES (?, ?, ?, 'T1.5', 1, ?, '{}', '2026-05-08T00:00:00Z')
            """,
            (source_id, source_id, f"https://{source_id}.example.com", kind),
        )
    for item_id, source_id, _kind, score, published_at in candidates:
        published = (published_at or datetime(2026, 5, 8, tzinfo=UTC)).isoformat().replace("+00:00", "Z")
        conn.execute(
            """
            INSERT INTO items (
              id, source_id, url, title, author, published_at, fetched_at,
              content_text, content_html, content_hash, extra_json
            )
            VALUES (?, ?, ?, ?, NULL, ?, ?, 'content', NULL, ?, '{}')
            """,
            (item_id, source_id, f"https://example.com/{item_id}", item_id, published, published, f"h-{item_id}"),
        )
        conn.execute(
            """
            INSERT INTO item_evaluations (
              item_id, stage, ruleset_version, model_id, input_json, output_json,
              numeric_json, latency_ms, cost_usd, evaluated_at, error
            )
            VALUES (?, 'scoring', 'test.r1', 'fake', '{}', '{}', ?, 1, 0, ?, NULL)
            """,
            (
                item_id,
                json.dumps(
                    {
                        "relevance": score,
                        "density": score,
                        "recency": score,
                        "authority": score,
                        "engineering": score,
                        "reasoning": "ok",
                    }
                ),
                published,
            ),
        )
    conn.commit()
    return conn


def test_parse_source_quota_supports_default_off_and_rejects_empty() -> None:
    with pytest.raises(ValueError, match="empty"):
        parse_source_quota("")
    with pytest.raises(ValueError, match="not a number"):
        parse_source_quota("x=abc")
    with pytest.raises(ValueError, match=r"within \(0, 1\]"):
        parse_source_quota("x=1.5")
    with pytest.raises(ValueError, match="invalid source quota assignment"):
        parse_source_quota("x")
    assert parse_source_quota("off") is None
    assert parse_source_quota(" OFF ") is None
    assert parse_source_quota("x=0.20,source=0.075") == SourceQuota(
        kind_caps={"x": 0.20},
        per_source=0.075,
    )
    assert DEFAULT_SOURCE_QUOTA == SourceQuota(kind_caps={"x": 0.20}, per_source=0.075)


def test_curate_cli_source_quota_uses_env_default_and_accepts_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AI_RADAR_CURATE_SOURCE_QUOTA", raising=False)
    default_args = cli.build_parser().parse_args(["curate"])
    monkeypatch.setenv("AI_RADAR_CURATE_SOURCE_QUOTA", "x=0.25,source=0.10")
    env_args = cli.build_parser().parse_args(["curate"])
    off_args = cli.build_parser().parse_args(["curate", "--source-quota", "off"])

    assert default_args.source_quota is cli._SOURCE_QUOTA_FROM_ENV
    assert env_args.source_quota is cli._SOURCE_QUOTA_FROM_ENV
    monkeypatch.delenv("AI_RADAR_CURATE_SOURCE_QUOTA")
    assert cli._resolve_source_quota(default_args.source_quota) == DEFAULT_SOURCE_QUOTA
    monkeypatch.setenv("AI_RADAR_CURATE_SOURCE_QUOTA", "x=0.25,source=0.10")
    assert cli._resolve_source_quota(env_args.source_quota) == SourceQuota(kind_caps={"x": 0.25}, per_source=0.10)
    assert off_args.source_quota is None


def test_invalid_source_quota_env_is_lazy_and_does_not_break_unrelated_commands(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AI_RADAR_CURATE_SOURCE_QUOTA", "bad")

    unrelated_args = cli.build_parser().parse_args(["fetch"])
    curate_args = cli.build_parser().parse_args(["curate"])

    assert unrelated_args.command == "fetch"
    assert cli._curate(curate_args) == 2
    err = capsys.readouterr().err
    assert "AI_RADAR_CURATE_SOURCE_QUOTA is invalid" in err
    assert "x=0.20,source=0.075 or off" in err
    assert "curate did not run" in err


def test_curate_kind_cap_refills_from_other_kinds(tmp_path: Path) -> None:
    conn = _setup_quota_db(
        tmp_path,
        [
            ("x-1", "x-a", "x", 9.9, None),
            ("x-2", "x-b", "x", 9.8, None),
            ("x-3", "x-c", "x", 9.7, None),
            ("x-4", "x-d", "x", 9.6, None),
            ("feed-1", "feed-a", "feed", 9.5, None),
            ("feed-2", "feed-b", "feed", 9.4, None),
            ("web-1", "web-a", "web", 9.3, None),
        ],
    )

    run = curate(
        conn,
        ruleset_version="test.r1",
        limit=5,
        freshness_quota=0,
        source_quota=SourceQuota(kind_caps={"x": 0.4}, per_source=None),
    )

    assert run.output_curated_ids == ["x-1", "x-2", "feed-1", "feed-2", "web-1"]


def test_curate_per_source_cap_refills_from_other_sources(tmp_path: Path) -> None:
    conn = _setup_quota_db(
        tmp_path,
        [
            ("a-1", "source-a", "feed", 9.9, None),
            ("a-2", "source-a", "feed", 9.8, None),
            ("a-3", "source-a", "feed", 9.7, None),
            ("b-1", "source-b", "feed", 9.6, None),
            ("c-1", "source-c", "feed", 9.5, None),
            ("d-1", "source-d", "feed", 9.4, None),
        ],
    )

    run = curate(
        conn,
        ruleset_version="test.r1",
        limit=4,
        freshness_quota=0,
        source_quota=SourceQuota(kind_caps={}, per_source=0.25),
    )

    assert run.output_curated_ids == ["a-1", "b-1", "c-1", "d-1"]


def test_curate_fresh_and_filtered_share_quota_counts(tmp_path: Path) -> None:
    fresh_at = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=1)
    conn = _setup_quota_db(
        tmp_path,
        [
            ("fresh-a-1", "source-a", "feed", 5.2, fresh_at),
            ("fresh-a-2", "source-a", "feed", 5.1, fresh_at - timedelta(minutes=1)),
            ("old-a", "source-a", "feed", 9.9, None),
            ("old-b", "source-b", "feed", 9.8, None),
            ("old-c", "source-c", "feed", 9.7, None),
        ],
    )

    run = curate(
        conn,
        ruleset_version="test.r1",
        limit=4,
        freshness_quota=2,
        source_quota=SourceQuota(kind_caps={}, per_source=0.5),
    )

    assert run.output_curated_ids == ["fresh-a-1", "fresh-a-2", "old-b", "old-c"]


def test_curate_small_limit_caps_never_round_to_zero(tmp_path: Path) -> None:
    conn = _setup_quota_db(tmp_path, [("x-1", "source-x", "x", 9.0, None)])

    run = curate(
        conn,
        ruleset_version="test.r1",
        limit=1,
        freshness_quota=0,
        source_quota=SourceQuota(kind_caps={"x": 0.01}, per_source=0.01),
    )

    assert run.output_curated_ids == ["x-1"]


def test_curate_records_baseline_membership_and_exact_shadow_difference(tmp_path: Path) -> None:
    conn = _setup_quota_db(
        tmp_path,
        [
            ("x-1", "x-a", "x", 9.9, None),
            ("x-2", "x-b", "x", 9.8, None),
            ("feed-1", "feed-a", "feed", 9.7, None),
            ("feed-2", "feed-b", "feed", 9.6, None),
        ],
    )

    run = curate(
        conn,
        ruleset_version="test.r1",
        limit=3,
        freshness_quota=0,
        source_quota=SourceQuota(kind_caps={"x": 1 / 3}, per_source=None),
    )

    reasons = {
        row[0]: json.loads(row[1])["source_quota"]
        for row in conn.execute(
            "SELECT item_id, reason_json FROM curated_items WHERE run_id=? ORDER BY rank",
            (run.id,),
        )
    }
    shadow = json.loads(conn.execute("SELECT shadow_json FROM curation_runs WHERE id=?", (run.id,)).fetchone()[0])
    assert run.output_curated_ids == ["x-1", "feed-1", "feed-2"]
    assert reasons["x-1"] == {
        "baseline": "same_run_without_source_quota",
        "baseline_selected": True,
        "kind": "x",
        "kind_cap": 1,
        "policy": "source-quota-v1",
        "source_cap": None,
    }
    # feed has no kind quota in this policy: null, not the run limit
    assert reasons["feed-2"]["kind_cap"] is None
    assert reasons["feed-2"]["baseline_selected"] is False
    assert shadow == {
        "baseline": "same_run_without_source_quota",
        "baseline_only": [{"item_id": "x-2", "raw_weighted_score": 9.8}],
        "policy": "source-quota-v1",
        "quota_only_count": 1,
        # Changed with the tier multiplier's retirement (ADR-20260906-7c31). The string is
        # part of the frozen shape ADR-20260903-bc36 validates, so it has to move when the
        # score behind it moves -- otherwise runs from either side read alike in the audit.
        "score_semantics": "unadjusted_before_rank_calibration",
    }


def test_curate_cli_empty_env_means_default_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_RADAR_CURATE_SOURCE_QUOTA", "   ")
    args = cli.build_parser().parse_args(["curate"])
    assert cli._resolve_source_quota(args.source_quota) == DEFAULT_SOURCE_QUOTA
    monkeypatch.setenv("AI_RADAR_CURATE_SOURCE_QUOTA", "")
    assert cli._resolve_source_quota(args.source_quota) == DEFAULT_SOURCE_QUOTA


def test_source_quota_off_keeps_head_fresh_segment_when_freshness_quota_exceeds_limit(
    tmp_path: Path,
) -> None:
    # HEAD (pre-quota) took fresh[:freshness_quota] before honouring limit; ``off``
    # must reproduce that selection byte for byte, including this quirk.
    now = datetime.now(tz=UTC)
    fresh_rows = [(f"f{i}", f"src-{i}", "feed", 9.0 - i * 0.1, now) for i in range(6)]
    conn = _setup_quota_db(tmp_path, fresh_rows)

    run = curate(
        conn,
        ruleset_version="test.r1",
        limit=4,
        freshness_quota=6,
        freshness_floor=4.0,
        source_quota=None,
    )

    assert run.output_curated_ids == [f"f{i}" for i in range(6)]


def test_source_quota_off_reproduces_head_selection_and_null_shadow(tmp_path: Path) -> None:
    conn = _setup_quota_db(
        tmp_path,
        [
            ("a", "source-a", "x", 9.9, None),
            ("b", "source-a", "x", 9.8, None),
            ("c", "source-a", "x", 9.7, None),
            ("d", "source-b", "feed", 9.6, None),
        ],
    )

    run = curate(conn, ruleset_version="test.r1", limit=3, freshness_quota=0, source_quota=None)

    assert run.output_curated_ids == ["a", "b", "c"]
    assert conn.execute("SELECT shadow_json FROM curation_runs WHERE id=?", (run.id,)).fetchone()[0] is None
    reasons = [
        json.loads(row[0])
        for row in conn.execute("SELECT reason_json FROM curated_items WHERE run_id=? ORDER BY rank", (run.id,))
    ]
    assert all("source_quota" not in reason for reason in reasons)


def test_migration_021_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "migrate-twice.db"

    migrate(db_path)
    migrate(db_path)

    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(curation_runs)")}
    assert "shadow_json" in columns


def test_curate_applies_threshold_limit_sort_and_writes_run(tmp_path: Path) -> None:
    conn = _setup_curator_db(tmp_path)

    run = curate(
        conn,
        ruleset_version="test.r1",
        weights=Weights.default(),
        threshold=6.5,
        limit=30,
        source_quota=None,
    )

    assert run.threshold == 6.5
    assert len(run.output_curated_ids) == 30
    assert "item-34" not in run.output_curated_ids
    assert "item-30" in run.output_curated_ids
    rows = conn.execute(
        "SELECT rank, weighted_score FROM curated_items WHERE run_id=? ORDER BY rank",
        (run.id,),
    ).fetchall()
    assert len(rows) == 30
    assert [row[0] for row in rows] == list(range(1, 31))
    assert rows[0][1] >= rows[-1][1]


def test_curate_default_threshold_matches_prd_contract(tmp_path: Path) -> None:
    conn = _setup_curator_db(tmp_path, count=3)

    run = curate(conn, ruleset_version="test.r1", source_quota=None)

    assert run.threshold == 6.5


def test_curate_default_limit_matches_reference_feed_depth(tmp_path: Path) -> None:
    conn = _setup_curator_db(tmp_path, count=45)
    for idx in range(45):
        item_id = f"item-{idx:02d}"
        conn.execute(
            """
            UPDATE item_evaluations
            SET numeric_json=?
            WHERE item_id=? AND stage='scoring'
            """,
            (
                json.dumps(
                    {
                        "relevance": 8.0,
                        "density": 8.0,
                        "recency": 8.0,
                        "authority": 8.0,
                        "engineering": 8.0,
                        "reasoning": "ok",
                    }
                ),
                item_id,
            ),
        )
    conn.commit()

    run = curate(conn, ruleset_version="test.r1", source_quota=None)

    assert len(run.output_curated_ids) == 40


def test_curate_prioritizes_latest_visible_date_for_reference_parity(tmp_path: Path) -> None:
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO sources (id,name,url,tier,enabled,meta_json,synced_at) VALUES ('s','S','https://example.com','T1.5',1,'{}','2026-05-08T00:00:00Z')"
    )
    now = datetime.now(UTC).replace(microsecond=0)
    fresh_at = now.replace(hour=4, minute=0, second=0)
    if fresh_at > now:
        fresh_at -= timedelta(days=1)
    old_at = fresh_at - timedelta(days=2)

    def insert_item(item_id: str, published_at: datetime, score: float) -> None:
        conn.execute(
            """
            INSERT INTO items (
              id, source_id, url, title, author, published_at, fetched_at,
              content_text, content_html, content_hash, extra_json
            )
            VALUES (?, 's', ?, ?, NULL, ?, ?, 'content', NULL, ?, '{}')
            """,
            (
                item_id,
                f"https://example.com/{item_id}",
                item_id,
                published_at.isoformat().replace("+00:00", "Z"),
                published_at.isoformat().replace("+00:00", "Z"),
                f"h-{item_id}",
            ),
        )
        conn.execute(
            """
            INSERT INTO item_evaluations (
              item_id, stage, ruleset_version, model_id, input_json, output_json,
              numeric_json, latency_ms, cost_usd, evaluated_at, error
            )
            VALUES (?, 'scoring', 'test.r1', 'fake', '{}', '{}', ?, 1, 0, ?, NULL)
            """,
            (
                item_id,
                json.dumps(
                    {
                        "relevance": score,
                        "density": score,
                        "recency": score,
                        "authority": score,
                        "engineering": score,
                        "reasoning": "ok",
                    }
                ),
                published_at.isoformat().replace("+00:00", "Z"),
            ),
        )

    for idx in range(45):
        insert_item(f"old-{idx:02d}", old_at - timedelta(minutes=idx), 9.0)
    for idx in range(36):
        insert_item(f"fresh-{idx:02d}", fresh_at - timedelta(minutes=idx), 4.2)
    conn.commit()

    run = curate(conn, ruleset_version="test.r1", source_quota=None)

    assert len(run.output_curated_ids) == 40
    assert sum(item_id.startswith("fresh-") for item_id in run.output_curated_ids) == 36
    assert sum(item_id.startswith("old-") for item_id in run.output_curated_ids) == 4


def test_curate_with_high_threshold_returns_empty_run(tmp_path: Path) -> None:
    conn = _setup_curator_db(tmp_path, count=3)

    run = curate(conn, ruleset_version="test.r1", threshold=9.99, source_quota=None)

    assert run.output_curated_ids == []
    assert conn.execute("SELECT COUNT(*) FROM curated_items WHERE run_id=?", (run.id,)).fetchone()[0] == 0


def test_a_weights_file_can_carry_the_new_dimension_and_the_tier_switch() -> None:
    """Both were silently droppable: a file could name them and the loader would ignore it.

    `significance` reaching zero and `uses_tier_multiplier` reaching False are the same value the
    defaults produce, so a loader that discarded both looked identical to one that read them.
    """
    loaded = weights_from_mapping(
        {
            "relevance": 0.0,
            "density": 0.4,
            "recency": 0.0,
            "authority": 0.1,
            "engineering": 0.0,
            "significance": 0.5,
            "uses_tier_multiplier": True,
        }
    )
    assert loaded.significance == pytest.approx(0.5)
    assert loaded.uses_tier_multiplier is True
    # All five core dimensions present: they are required whatever their weight.
    numeric = {"relevance": 0.0, "density": 6.0, "recency": 0.0, "authority": 6.0,
               "engineering": 0.0, "significance": 6.0}
    assert weighted_score(numeric, loaded, "T1") == pytest.approx(7.5)


def test_a_weights_file_without_them_still_loads() -> None:
    loaded = weights_from_mapping(
        {"relevance": 0.2, "density": 0.4, "recency": 0.2, "authority": 0.1, "engineering": 0.1}
    )
    assert loaded.significance == pytest.approx(0.0)
    assert loaded.uses_tier_multiplier is False
