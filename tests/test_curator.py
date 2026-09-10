from __future__ import annotations

import json
import platform
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from airadar import cli
from airadar.curator import select as select_module
from airadar.curator.dedup import deduplicate_candidates
from airadar.curator.score import ScoredCandidate, weighted_score
from airadar.curator.select import (
    CATEGORY_MULTIPLIERS,
    DEFAULT_SOURCE_QUOTA,
    SourceQuota,
    curate,
    parse_source_quota,
)
from airadar.curator.weights import DEFAULT_WEIGHTS, DIMENSIONS, Weights, load_weights, weights_from_mapping
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


def test_category_multipliers_hold_exactly_the_fitted_table() -> None:
    """Only `paper` is scaled; everything else, including the empty category, scores unchanged.

    An item can be scored before it is enriched, so "no category" is the ordinary state of
    the newest candidates -- not an error, and not a reason to change their score.
    """
    from airadar.curator.select import CATEGORY_MULTIPLIERS, category_multiplier

    # `paper` only, fitted 2026-09-09 against AIHOT's own score with one enrich stamp behind the
    # labels; see the comment at the definition for the readings and for why the other four are
    # 1.0. Pinning the exact table catches both a silent refill and a silent emptying.
    assert CATEGORY_MULTIPLIERS == {"paper": 0.95}
    assert category_multiplier("paper") == 0.95
    for untouched in ("tutorial", "model", "product", "industry", "", "unknown-slug"):
        assert category_multiplier(untouched) == 1.0


def test_paper_multiplier_reorders_without_changing_eligibility_or_the_archived_score(
    tmp_path: Path,
) -> None:
    """The behaviour test for the category factor. Pins WHERE it is applied, not just its value.

    Three items, all above the 6.5 threshold on their raw score, none of them fresh (published
    long ago, so the freshness segment is empty and the whole run is filled from `filtered` in
    ranking order -- no dependence on the clock).

      p-high  paper     raw 8.2   ->  ranks at 8.2 * 0.95 = 7.79
      i-mid   industry  raw 7.9   ->  ranks at 7.9
      p-low   paper     raw 6.8   ->  ranks at 6.46, but 6.8 is what the threshold sees

    The two raw scores straddle the factor: 8.2 > 7.9 unmultiplied, 7.79 < 7.9 multiplied. Any
    coefficient change has to keep that straddle or this test stops testing anything -- pick the
    scores from the coefficient, not the other way round.

    Each assertion below fails on a different way of getting this wrong:
      order      -> the factor is not applied at all (removing it from ranking_key)
      p-low in   -> the factor is applied to weighted_score, so it reaches the 6.5 gate too
      8.2 stored -> the factor leaked into the archived score, making
                    SOURCE_QUOTA_SCORE_SEMANTICS ("unadjusted_...") false

    This exercises the `filtered` sort only -- nothing here is fresh, on purpose, so the run is
    clock-independent. The fresh sort carries 36 of the 40 slots and has its own test below;
    reverting only `fresh.sort` leaves this one green, which is why both exist.
    """
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO sources (id,name,url,tier,enabled,meta_json,synced_at)"
        " VALUES ('s','S','https://example.com','T1.5',1,'{}','2026-05-08T00:00:00Z')"
    )
    for item_id, category, raw in (("p-high", "paper", 8.2), ("i-mid", "industry", 7.9), ("p-low", "paper", 6.8)):
        conn.execute(
            """
            INSERT INTO items (id, source_id, url, title, author, published_at, fetched_at,
                               content_text, content_html, content_hash, extra_json)
            VALUES (?, 's', ?, ?, NULL, '2026-05-08T00:00:00Z', '2026-05-08T00:00:00Z',
                    'content', NULL, ?, '{}')
            """,
            (item_id, f"https://example.com/{item_id}", item_id, f"hash-{item_id}"),
        )
        conn.execute(
            """
            INSERT INTO item_evaluations (item_id, stage, ruleset_version, model_id, input_json,
                                          output_json, numeric_json, latency_ms, cost_usd,
                                          evaluated_at, error)
            VALUES (?, 'scoring', 'test.r1', 'fake', '{}', '{}', ?, 1, 0, '2026-05-08T00:00:00Z', NULL)
            """,
            (item_id, json.dumps(dict.fromkeys(DIMENSIONS, raw))),
        )
        conn.execute(
            """
            INSERT INTO item_evaluations (item_id, stage, ruleset_version, model_id, input_json,
                                          output_json, numeric_json, latency_ms, cost_usd,
                                          evaluated_at, error)
            VALUES (?, 'enrich', 'test.r1', 'fake', '{}', ?, NULL, 1, 0, '2026-05-08T00:00:00Z', NULL)
            """,
            (item_id, json.dumps({"primary_category": category})),
        )
    conn.commit()

    run = curate(conn, ruleset_version="test.r1", weights=Weights.default(), source_quota=None)

    assert run.output_curated_ids == ["i-mid", "p-high", "p-low"]
    rows = dict(
        conn.execute(
            "SELECT item_id, weighted_score FROM curated_items WHERE run_id=?", (run.id,)
        ).fetchall()
    )
    assert set(rows) == {"i-mid", "p-high", "p-low"}
    reasons = {
        item_id: json.loads(reason)
        for item_id, reason in conn.execute(
            "SELECT item_id, reason_json FROM curated_items WHERE run_id=?", (run.id,)
        ).fetchall()
    }
    assert reasons["p-high"]["weighted_score"] == pytest.approx(8.2)
    assert reasons["p-high"]["raw_weighted_score"] == pytest.approx(8.2)
    assert reasons["p-high"]["category_multiplier"] == pytest.approx(0.95)
    assert reasons["i-mid"]["category_multiplier"] == pytest.approx(1.0)


def test_run_record_carries_the_category_multipliers_that_produced_its_ordering(
    tmp_path: Path,
) -> None:
    """`weights_json` has to describe the whole ranking function, not just the weight vector.

    Without this the stored run says nothing about which coefficients ordered it, so two runs
    from either side of a change to CATEGORY_MULTIPLIERS read alike -- the same failure
    SOURCE_QUOTA_SCORE_SEMANTICS exists to prevent, and the reason that constant carries a
    comment about the retired tier multiplier.
    """
    conn = _setup_curator_db(tmp_path, count=3)
    # The fixture scores items but never enriches them, so without this the watermark is
    # legitimately None and says nothing about whether it tracks enrich rows at all.
    _insert_enrich_row(conn, "item-00")
    conn.commit()

    run = curate(conn, ruleset_version="test.r1", weights=Weights.default(), source_quota=None)

    stored = json.loads(
        conn.execute("SELECT weights_json FROM curation_runs WHERE id=?", (run.id,)).fetchone()[0]
    )
    assert "category_multipliers" in stored
    assert stored["category_multipliers"] == CATEGORY_MULTIPLIERS
    # The weight vector itself must still be readable from the same record.
    assert stored["significance"] == pytest.approx(Weights.default().significance)

    # Tie-breakers carry a DIRECTION, not just field names. ranking_key sorts ascending on a
    # tuple whose first element is negated, so score descends while the other two ascend; a bare
    # field list replays the ordering backwards on ties and reads perfectly correct doing it.
    assert stored["ranking_tiebreakers"] == [
        {"field": "weighted_score_x_category_multiplier", "direction": "desc"},
        {"field": "published_at", "direction": "asc"},
        {"field": "item_id", "direction": "asc"},
    ]

    # The category snapshot, as one integer. It has to be a real upper bound on the enrich rows
    # this run could have read -- a watermark taken after the load, or left null, names a
    # different set of categories than the ones that ordered the page, and the replay would come
    # back subtly different with nothing to indicate why.
    # With no concurrent writer the largest enrich row read IS the largest in the table. This
    # equality is therefore necessary but NOT sufficient -- it holds for a `SELECT MAX(id)` taken
    # at any point in a single-threaded fixture, which is exactly why the concurrency test below
    # exists. An adversarial reviewer showed that this assert alone left the original (wrong)
    # implementation green.
    live_max = conn.execute(
        "SELECT MAX(id) FROM item_evaluations WHERE stage='enrich' AND error IS NULL"
    ).fetchone()[0]
    assert stored["enrich_watermark"] == live_max

    # And it must actually move when the table grows, otherwise a constant would satisfy the
    # assert above on a database that never changes.
    # Has to be an item that is actually a candidate: the watermark is the max enrich id this
    # load READ, so a row for some unrelated item_id is correctly ignored.
    _insert_enrich_row(conn, "item-00")
    conn.commit()
    second = curate(conn, ruleset_version="test.r1", weights=Weights.default(), source_quota=None)
    stored_second = json.loads(
        conn.execute("SELECT weights_json FROM curation_runs WHERE id=?", (second.id,)).fetchone()[0]
    )
    assert stored_second["enrich_watermark"] > stored["enrich_watermark"]


def test_paper_multiplier_reorders_the_fresh_segment_too(tmp_path: Path) -> None:
    """The fresh sort carries 36 of the 40 slots; the test above only reaches the other 4.

    Reported by an adversarial reviewer: reverting `fresh.sort` alone to the pre-factor key left
    the whole suite green while losing three quarters of the effect on the real pool, because the
    other test deliberately makes the fresh segment empty to stay clock-independent.

    This one keeps the clock out a different way -- a freshness window wide enough that the fixed
    published_at is always inside it -- so every candidate lands in `fresh` and the ordering under
    test is the one production actually uses for 36 of its 40 slots.
    """
    db_path = tmp_path / "radar.db"
    migrate(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO sources (id,name,url,tier,enabled,meta_json,synced_at)"
        " VALUES ('s','S','https://example.com','T1.5',1,'{}','2026-05-08T00:00:00Z')"
    )
    for item_id, category, raw in (("p-high", "paper", 8.2), ("i-mid", "industry", 7.9)):
        conn.execute(
            """
            INSERT INTO items (id, source_id, url, title, author, published_at, fetched_at,
                               content_text, content_html, content_hash, extra_json)
            VALUES (?, 's', ?, ?, NULL, '2026-05-08T00:00:00Z', '2026-05-08T00:00:00Z',
                    'content', NULL, ?, '{}')
            """,
            (item_id, f"https://example.com/{item_id}", item_id, f"hash-{item_id}"),
        )
        conn.execute(
            """
            INSERT INTO item_evaluations (item_id, stage, ruleset_version, model_id, input_json,
                                          output_json, numeric_json, latency_ms, cost_usd,
                                          evaluated_at, error)
            VALUES (?, 'scoring', 'test.r1', 'fake', '{}', '{}', ?, 1, 0, '2026-05-08T00:00:00Z', NULL)
            """,
            (item_id, json.dumps(dict.fromkeys(DIMENSIONS, raw))),
        )
        conn.execute(
            """
            INSERT INTO item_evaluations (item_id, stage, ruleset_version, model_id, input_json,
                                          output_json, numeric_json, latency_ms, cost_usd,
                                          evaluated_at, error)
            VALUES (?, 'enrich', 'test.r1', 'fake', '{}', ?, NULL, 1, 0, '2026-05-08T00:00:00Z', NULL)
            """,
            (item_id, json.dumps({"primary_category": category})),
        )
    conn.commit()

    run = curate(
        conn,
        ruleset_version="test.r1",
        weights=Weights.default(),
        source_quota=None,
        freshness_window_hours=24 * 365 * 100,
    )

    assert run.output_curated_ids == ["i-mid", "p-high"]


def test_category_multipliers_declare_the_enrich_stamp_they_were_fitted_on() -> None:
    """Red when the enrich ruleset moves -- the coefficient was fitted on one specific one.

    Not a runtime guard: ranking applies the factor to whatever the newest enrich row says. This
    exists so a prompt revision cannot silently change which items are demoted. When it fails,
    the decision is not "bump the string" -- it is "re-measure whether `paper` still needs 0.80
    under the new labels", the same precondition the 2026-09-09 withdrawal wrote into select.py.
    """
    from airadar.curator.select import CATEGORY_MULTIPLIERS, CATEGORY_MULTIPLIERS_FITTED_ON_ENRICH
    from airadar.ruleset import current_version_v2

    if not CATEGORY_MULTIPLIERS:
        pytest.skip("no coefficients in force, so nothing is pinned to an enrich generation")
    assert current_version_v2() == CATEGORY_MULTIPLIERS_FITTED_ON_ENRICH


def test_category_multiplier_keys_are_real_categories() -> None:
    """A typo'd key is a silent no-op -- `{"papers": 0.80}` would leave ranking untouched."""
    from airadar.curator.select import CATEGORY_MULTIPLIERS
    from airadar.eval.aihot_fit.common import PRIMARY_CATEGORIES

    assert set(CATEGORY_MULTIPLIERS) <= set(PRIMARY_CATEGORIES)


def test_primary_category_reads_enrich_output_and_never_raises() -> None:
    """It runs per candidate inside the load loop; one malformed row must not fail a run."""
    import json as _json

    from airadar.curator.select import _primary_category

    assert _primary_category(_json.dumps({"primary_category": "paper"})) == "paper"
    # Every shape that reaches here from a real row, plus the ones that would raise.
    assert _primary_category(None) == ""
    assert _primary_category("") == ""
    assert _primary_category("not json at all") == ""
    assert _primary_category(_json.dumps(["a", "list"])) == ""
    assert _primary_category(_json.dumps({"other": "field"})) == ""
    assert _primary_category(_json.dumps({"primary_category": None})) == ""
    assert _primary_category(_json.dumps({"primary_category": 7})) == ""


def _insert_enrich_row(conn: sqlite3.Connection, item_id: str, category: str = "paper") -> int:
    conn.execute(
        "INSERT INTO item_evaluations (item_id, stage, ruleset_version, model_id, input_json,"
        " output_json, numeric_json, latency_ms, cost_usd, evaluated_at, error)"
        " VALUES (?, 'enrich', 'test.r1', 'fake', '{}', ?, '{}', 1, 0, '2026-09-10T00:00:00Z', NULL)",
        (item_id, json.dumps({"primary_category": category})),
    )
    return int(conn.execute("SELECT MAX(id) FROM item_evaluations").fetchone()[0])


def test_enrich_watermark_covers_rows_that_appear_while_candidates_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The watermark must cover every enrich row the load actually read -- including one that
    lands mid-load.

    This is the only test on this axis that can fail. The equality assert in the test above is
    satisfied by a `SELECT MAX(id)` taken at ANY point, because nothing writes to
    `item_evaluations` during a single-threaded `curate`; measured, `(MAX before, MAX after)` was
    identical on every call. So the first implementation -- a separate `SELECT MAX(id)` before
    the load -- passed while being wrong in the direction that matters: a row committed between
    the two queries is visible to the load but excluded from the watermark, and replaying at
    `id <= watermark` then reads the item's PREVIOUS category and orders it differently.

    Simulating that here by writing from a second connection inside `_load_candidates`, which is
    what enrich does in production (it is a separate pipeline stage against the same file).
    """
    conn = _setup_curator_db(tmp_path, count=3)
    real_load = select_module._load_candidates
    intruder = sqlite3.connect(str(tmp_path / "radar.db"))
    injected: list[int] = []

    after: list[int] = []

    def load_with_a_concurrent_enrich_commit(connection, weights):  # type: ignore[no-untyped-def]
        # Lands BEFORE the load's own SELECT -> the load reads it -> must be covered.
        injected.append(_insert_enrich_row(intruder, "item-00", "paper"))
        intruder.commit()
        result = real_load(connection, weights)
        # Lands AFTER -> the load never saw it -> must NOT be covered. Without this half the
        # test is one-sided: moving the query to after the load also satisfies ">= injected",
        # and that placement is wrong the other way -- a replay would read a category newer
        # than the one this ordering used. Verified: this assert is what turns that mutation red.
        after.append(_insert_enrich_row(intruder, "item-01", "model"))
        intruder.commit()
        return result

    monkeypatch.setattr(select_module, "_load_candidates", load_with_a_concurrent_enrich_commit)
    run = curate(conn, ruleset_version="test.r1", weights=Weights.default(), source_quota=None)
    intruder.close()

    stored = json.loads(
        conn.execute("SELECT weights_json FROM curation_runs WHERE id=?", (run.id,)).fetchone()[0]
    )
    assert injected, "the fixture did not actually inject a row"
    # The row landed before the load's own SELECT, so the load read it. The watermark has to
    # reach it; a pre-load `SELECT MAX(id)` stops one short and this assert is what catches that.
    assert stored["enrich_watermark"] is not None
    assert stored["enrich_watermark"] >= injected[-1]
    assert after and stored["enrich_watermark"] < after[-1]


def test_ordering_code_digest_is_bound_to_all_four_ordering_functions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three properties. The previous attempt at this field satisfied none of them.

    1. The value in the run record IS the live digest. A reviewer showed the earlier test only
       asserted the stored field `is None` (inside a patched failure case), so setting it to a
       constant `None` on the happy path left the whole suite green.
    2. It moves when ANY of the four ordering functions changes -- not just `ranking_key`.
       Swapping the earlier digest's target to an unrelated function also left the suite green,
       because the test replaced the source-reading call rather than the function it read.
    3. It does not read the filesystem. `co_code` comes from the loaded objects, so the three
       measured failure modes of the `inspect.getsource` version (wrong code block, empty-file
       digest, `tokenize.TokenError` escaping the except clause) are unreachable.
    """
    conn = _setup_curator_db(tmp_path, count=3)
    _insert_enrich_row(conn, "item-00")
    conn.commit()
    run = curate(conn, ruleset_version="test.r1", weights=Weights.default(), source_quota=None)
    stored = json.loads(
        conn.execute("SELECT weights_json FROM curation_runs WHERE id=?", (run.id,)).fetchone()[0]
    )
    live = select_module._ordering_code_digest()
    assert stored["ordering_code_sha256"] == live
    assert len(live) == 12
    assert stored["python"] == platform.python_version()

    # Each of the four is load-bearing: replacing any one has to move the digest. Without this
    # loop the field can be bound to one function while three others silently reorder replays.
    for name in ("ranking_key", "category_multiplier", "_primary_category", "weighted_score"):
        original = getattr(select_module, name)

        def different(*_args: object, **_kwargs: object) -> float:
            unused = 1 + 1  # noqa: F841 -- distinct bytecode is the whole point
            return 0.0

        monkeypatch.setattr(select_module, name, different)
        assert select_module._ordering_code_digest() != live, f"digest ignores {name}"
        monkeypatch.setattr(select_module, name, original)
    assert select_module._ordering_code_digest() == live

    # No filesystem read, asserted structurally: the module does not import `inspect` at all,
    # so the three measured failure modes of the source-text version have no path in. Checking
    # the import rather than patching it, because patching something absent would pass for the
    # wrong reason.
    assert not hasattr(select_module, "inspect")
