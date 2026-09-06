from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from airadar import cli
from airadar.curator.score import ScoredCandidate
from airadar.curator.select import _calibrate_selected_scores
from airadar.db import migrate


def _insert_run(
    conn: sqlite3.Connection,
    run_id: str,
    rows: list[tuple[str, float, bool]],
    *,
    shadow_json: str | None = "__DEFAULT__",
) -> None:
    if shadow_json == "__DEFAULT__":
        # the complete frozen v1 run shape, as curate() writes it
        shadow_json = json.dumps(
            {
                "policy": "source-quota-v1",
                "baseline": "same_run_without_source_quota",
                "score_semantics": "tier_adjusted_before_rank_calibration",
                "baseline_only": [{"item_id": "shadow-only", "raw_weighted_score": 8.1}],
                "quota_only_count": sum(1 for _, _, selected in rows if not selected),
            }
        )
    conn.execute(
        """
        INSERT INTO curation_runs (
          id, ruleset_version, weights_json, threshold, input_eval_ids,
          output_curated_ids, created_at, shadow_json
        )
        VALUES (?, 'test.r1', '{}', 6.5, '[]', ?, '2026-09-03T00:00:00Z', ?)
        """,
        (run_id, json.dumps([item_id for item_id, _, _ in rows]), shadow_json),
    )
    for rank, (item_id, raw_score, baseline_selected) in enumerate(rows, start=1):
        conn.execute(
            """
            INSERT INTO items (
              id, source_id, url, title, author, published_at, fetched_at,
              content_text, content_html, content_hash, extra_json
            )
            VALUES (?, 'source-a', ?, ?, NULL, '2026-09-03T00:00:00Z',
                    '2026-09-03T00:00:00Z', 'content', NULL, ?, '{}')
            """,
            (item_id, f"https://example.com/{item_id}", item_id, f"hash-{item_id}"),
        )
        reason = {
            "raw_weighted_score": raw_score,
            "score_calibration": {"method": "rank_linear_v1", "display_score": 99},
            "source_quota": {
                "policy": "source-quota-v1",
                "kind": "feed",
                "kind_cap": None,
                "source_cap": 3,
                "baseline": "same_run_without_source_quota",
                "baseline_selected": baseline_selected,
            },
        }
        conn.execute(
            """
            INSERT INTO curated_items (
              run_id, item_id, weighted_score, rank, reason_json, summary_json
            )
            VALUES (?, ?, 9.9, ?, ?, '{"summary":"stale"}')
            """,
            (run_id, item_id, rank, json.dumps(reason)),
        )


def _setup_rollback_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "rollback.db"
    migrate(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO sources (id, name, url, tier, enabled, kind, meta_json, synced_at)
            VALUES ('source-a', 'Source A', 'https://example.com', 'T1.5', 1, 'feed', '{}',
                    '2026-09-03T00:00:00Z')
            """
        )
        _insert_run(conn, "20260902T235959Z-old", [("old-item", 8.0, False)])
        _insert_run(
            conn,
            "20260903T000000Z-target",
            [
                ("keep-a", 9.7, True),
                ("remove-b", 9.6, False),
                ("keep-c", 9.9, True),
                ("keep-d", 7.2, True),
            ],
        )
        _insert_run(
            conn,
            "20260903T000001Z-no-shadow",
            [("no-shadow-item", 8.5, False)],
            shadow_json=None,
        )
    return db_path


def _database_state(db_path: Path) -> tuple[list[tuple[object, ...]], list[tuple[object, ...]]]:
    with sqlite3.connect(db_path) as conn:
        runs = conn.execute("SELECT id, output_curated_ids, shadow_json FROM curation_runs ORDER BY id").fetchall()
        items = conn.execute(
            """
            SELECT run_id, item_id, weighted_score, rank, reason_json, summary_json
            FROM curated_items ORDER BY run_id, rank
            """
        ).fetchall()
    return runs, items


def test_rollback_quota_dry_run_reports_counts_without_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = _setup_rollback_db(tmp_path)
    before = _database_state(db_path)
    monkeypatch.setenv("AI_RADAR_DB", str(db_path))
    args = cli.build_parser().parse_args(
        ["admin", "curate", "rollback-quota", "--since", "20260903T000000Z-target", "--dry-run"]
    )

    assert cli._admin(args) == 0

    assert _database_state(db_path) == before
    output = capsys.readouterr().out
    assert "run_id=20260903T000000Z-target rows_would_remove=1 rows_kept=3 mode=dry-run" in output
    assert "DRY RUN complete runs=1 rows_would_remove=1 rows_kept=3; no curated rows were changed" in output
    assert "rerun without --dry-run to apply" in output


def test_rollback_quota_failure_is_scoped_and_does_not_claim_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = _setup_rollback_db(tmp_path)
    with sqlite3.connect(db_path) as conn:
        reason = json.loads(conn.execute("SELECT reason_json FROM curated_items WHERE item_id='keep-a'").fetchone()[0])
        reason.pop("raw_weighted_score")
        conn.execute(
            "UPDATE curated_items SET reason_json=? WHERE item_id='keep-a'",
            (json.dumps(reason),),
        )
    before = _database_state(db_path)
    monkeypatch.setenv("AI_RADAR_DB", str(db_path))
    args = cli.build_parser().parse_args(["admin", "curate", "rollback-quota", "--since", "20260903T000000Z-target"])

    assert cli._admin(args) == 1

    assert _database_state(db_path) == before
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "FAILED at run_id=20260903T000000Z-target" in captured.err
    assert "this run: not changed" in captured.err
    assert "earlier runs: none" in captured.err
    assert "later matching runs: 0 not processed" in captured.err
    assert "rerun the same command" in captured.err
    assert "fix the offending run/item named above" in captured.err
    assert "mode=write" not in captured.err


def test_rollback_quota_removes_only_quota_unique_rows_and_restores_invariants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = _setup_rollback_db(tmp_path)
    monkeypatch.setenv("AI_RADAR_DB", str(db_path))
    args = cli.build_parser().parse_args(["admin", "curate", "rollback-quota", "--since", "20260903T000000Z-target"])

    assert cli._admin(args) == 0

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT item_id, weighted_score, rank, reason_json, summary_json
            FROM curated_items
            WHERE run_id='20260903T000000Z-target'
            ORDER BY rank
            """
        ).fetchall()
        run_row = conn.execute(
            "SELECT output_curated_ids FROM curation_runs WHERE id='20260903T000000Z-target'"
        ).fetchone()
        old_count = conn.execute("SELECT COUNT(*) FROM curated_items WHERE run_id='20260902T235959Z-old'").fetchone()[0]
        no_shadow_count = conn.execute(
            "SELECT COUNT(*) FROM curated_items WHERE run_id='20260903T000001Z-no-shadow'"
        ).fetchone()[0]

    assert [row["item_id"] for row in rows] == ["keep-a", "keep-c", "keep-d"]
    assert [row["rank"] for row in rows] == [1, 2, 3]
    expected = _calibrate_selected_scores(
        [
            ScoredCandidate(0, "keep-a", "", "", "", 9.7, {}),
            ScoredCandidate(0, "keep-c", "", "", "", 8.4, {}),
            ScoredCandidate(0, "keep-d", "", "", "", 7.2, {}),
        ]
    )
    assert [row["weighted_score"] for row in rows] == [candidate.weighted_score for candidate in expected]
    assert [json.loads(row["reason_json"])["score_calibration"] for row in rows] == [
        candidate.reason["score_calibration"] for candidate in expected
    ]
    assert all(json.loads(row["reason_json"])["raw_weighted_score"] == raw for row, raw in zip(rows, [9.7, 9.9, 7.2]))
    assert all(row["summary_json"] is None for row in rows)
    assert json.loads(run_row["output_curated_ids"]) == [row["item_id"] for row in rows]
    assert old_count == 1
    assert no_shadow_count == 1
    output = capsys.readouterr().out
    assert "run_id=20260903T000000Z-target rows_removed=1 rows_kept=3 mode=write" in output
    assert "complete runs=1 rows_removed=1 rows_kept=3; rank/display metadata rewritten for 1 of 1 run(s)" in output
    with sqlite3.connect(db_path) as conn:
        shadow = json.loads(
            conn.execute("SELECT shadow_json FROM curation_runs WHERE id='20260903T000000Z-target'").fetchone()[0]
        )
    assert shadow["policy"] == "source-quota-v1"
    assert shadow["quota_only_count"] == 0
    assert shadow["rollback"]["removed_item_ids"] == ["remove-b"]
    assert shadow["rollback"]["at"].endswith("Z")
    assert "no further action needed" in output


def test_rollback_quota_second_pass_reports_already_rolled_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = _setup_rollback_db(tmp_path)
    monkeypatch.setenv("AI_RADAR_DB", str(db_path))
    args = cli.build_parser().parse_args(["admin", "curate", "rollback-quota", "--since", "20260903T000000Z-target"])
    assert cli._admin(args) == 0
    capsys.readouterr()

    assert cli._admin(args) == 0

    output = capsys.readouterr().out
    assert "run_id=20260903T000000Z-target rows_removed=0 rows_kept=3 mode=write" in output
    assert "rows_removed=0 rows_kept=3; already rolled back, nothing to remove" in output
    assert "rewritten" not in output


def test_rollback_quota_no_match_says_nothing_to_do(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = _setup_rollback_db(tmp_path)
    monkeypatch.setenv("AI_RADAR_DB", str(db_path))
    args = cli.build_parser().parse_args(["admin", "curate", "rollback-quota", "--since", "20991231T000000Z-none"])

    assert cli._admin(args) == 0

    output = capsys.readouterr().out
    assert "no matching runs since=20991231T000000Z-none" in output
    assert "nothing to do" in output
    assert "complete" not in output


def test_rollback_quota_keeps_relative_order_and_writes_literal_display_scores(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = _setup_rollback_db(tmp_path)
    monkeypatch.setenv("AI_RADAR_DB", str(db_path))
    args = cli.build_parser().parse_args(["admin", "curate", "rollback-quota", "--since", "20260903T000000Z-target"])

    assert cli._admin(args) == 0

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT item_id, rank, weighted_score, reason_json FROM curated_items "
            "WHERE run_id='20260903T000000Z-target' ORDER BY rank"
        ).fetchall()
    # kept rows stay in their original rank order even though raw scores are not monotonic
    assert [row[0] for row in rows] == ["keep-a", "keep-c", "keep-d"]
    assert [row[1] for row in rows] == [1, 2, 3]
    # rank-linear 92..62 over 3 rows, independent of the helper: 92, 77, 62
    assert [row[2] for row in rows] == [9.2, 7.7, 6.2]
    assert [json.loads(row[3])["score_calibration"]["display_score"] for row in rows] == [92, 77, 62]


def test_rollback_quota_single_row_runs_follow_curate_single_row_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = _setup_rollback_db(tmp_path)
    with sqlite3.connect(db_path) as conn:
        # (a) a quota run whose single row was never calibrated: curate() stores the raw
        #     score only under reason.weighted_score
        _insert_run(conn, "20260903T000002Z-single", [("solo", 8.8, True)])
        reason = json.loads(conn.execute("SELECT reason_json FROM curated_items WHERE item_id='solo'").fetchone()[0])
        reason.pop("raw_weighted_score")
        reason.pop("score_calibration")
        reason["weighted_score"] = 8.8
        conn.execute("UPDATE curated_items SET reason_json=? WHERE item_id='solo'", (json.dumps(reason),))
        # (b) a run that shrinks to one row after rollback
        _insert_run(conn, "20260903T000003Z-pair", [("pair-keep", 7.9, True), ("pair-drop", 7.5, False)])
    monkeypatch.setenv("AI_RADAR_DB", str(db_path))
    args = cli.build_parser().parse_args(["admin", "curate", "rollback-quota", "--since", "20260903T000002Z-single"])

    assert cli._admin(args) == 0

    with sqlite3.connect(db_path) as conn:
        solo = conn.execute(
            "SELECT rank, weighted_score, reason_json FROM curated_items WHERE item_id='solo'"
        ).fetchone()
        pair = conn.execute(
            "SELECT item_id, rank, weighted_score, reason_json FROM curated_items WHERE run_id='20260903T000003Z-pair'"
        ).fetchall()
    # (a) nothing to remove: the run is left exactly as curate() wrote it
    assert solo[0] == 1 and solo[1] == 9.9
    assert json.loads(solo[2])["weighted_score"] == 8.8
    # (b) shrinks to one row: curate()'s single-row shape (raw score, no calibration block)
    assert len(pair) == 1 and pair[0][0] == "pair-keep" and pair[0][1] == 1 and pair[0][2] == 7.9
    assert "score_calibration" not in json.loads(pair[0][3])
    assert "raw_weighted_score" not in json.loads(pair[0][3])
    assert "complete runs=2 rows_removed=1 rows_kept=2" in capsys.readouterr().out


def test_rollback_quota_dry_run_does_not_migrate_an_unmigrated_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = _setup_rollback_db(tmp_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("ALTER TABLE curation_runs DROP COLUMN shadow_json")
    monkeypatch.setenv("AI_RADAR_DB", str(db_path))
    args = cli.build_parser().parse_args(
        ["admin", "curate", "rollback-quota", "--since", "20260903T000000Z-target", "--dry-run"]
    )

    assert cli._admin(args) == 0

    with sqlite3.connect(db_path) as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(curation_runs)")]
    assert "shadow_json" not in columns
    output = capsys.readouterr().out
    assert "no quota shadow column yet" in output
    assert "nothing to do" in output


def test_rollback_quota_recomputes_summaries_for_the_latest_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = _setup_rollback_db(tmp_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE curation_runs SET created_at='2026-09-05T00:00:00Z' WHERE id='20260903T000000Z-target'")
    monkeypatch.setenv("AI_RADAR_DB", str(db_path))
    args = cli.build_parser().parse_args(["admin", "curate", "rollback-quota", "--since", "20260903T000000Z-target"])

    assert cli._admin(args) == 0

    with sqlite3.connect(db_path) as conn:
        summaries = conn.execute(
            "SELECT summary_json FROM curated_items WHERE run_id='20260903T000000Z-target' ORDER BY rank"
        ).fetchall()
    assert all(row[0] is not None for row in summaries)
    assert "summaries recomputed for 3 rows (latest run)" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            'UPDATE curation_runs SET shadow_json=\'{"policy":"source-quota-v2"}\' '
            "WHERE id='20260903T000000Z-target'",
            "shadow policy 'source-quota-v2'",
        ),
        (
            "UPDATE curated_items SET reason_json=json_set(reason_json,'$.source_quota.policy','other') "
            "WHERE item_id='remove-b'",
            "source_quota policy 'other'",
        ),
        (
            "UPDATE curated_items SET reason_json=json_remove(reason_json,'$.source_quota') WHERE item_id='remove-b'",
            "has no source_quota block",
        ),
        (
            "UPDATE curated_items SET reason_json=json_set(reason_json,'$.source_quota.baseline_selected','no') "
            "WHERE item_id='remove-b'",
            "non-boolean baseline_selected",
        ),
        (
            "UPDATE curated_items SET reason_json=json_remove(reason_json,'$.source_quota.baseline') "
            "WHERE item_id='keep-a'",
            "source_quota keys are",
        ),
        (
            "UPDATE curation_runs SET shadow_json=json_remove(shadow_json,'$.score_semantics') "
            "WHERE id='20260903T000000Z-target'",
            "shadow_json.score_semantics is None",
        ),
        (
            "UPDATE curation_runs SET shadow_json=json_set(shadow_json,'$.quota_only_count',7) "
            "WHERE id='20260903T000000Z-target'",
            "quota_only_count=7 but 1 rows",
        ),
        (
            "UPDATE curation_runs SET shadow_json=json_set(shadow_json,'$.baseline_only',"
            "json('[{\"item_id\":\"x\"}]')) WHERE id='20260903T000000Z-target'",
            "baseline_only entry is not",
        ),
    ],
)
def test_rollback_quota_refuses_unknown_policy_or_shape_without_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mutate: str,
    expected: str,
) -> None:
    db_path = _setup_rollback_db(tmp_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(mutate)
    before = _database_state(db_path)
    monkeypatch.setenv("AI_RADAR_DB", str(db_path))
    args = cli.build_parser().parse_args(["admin", "curate", "rollback-quota", "--since", "20260903T000000Z-target"])

    assert cli._admin(args) == 1

    assert _database_state(db_path) == before
    assert expected in capsys.readouterr().err


def test_rollback_quota_dry_run_opens_read_only_and_never_creates_a_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = _setup_rollback_db(tmp_path)
    monkeypatch.setenv("AI_RADAR_DB", str(db_path))

    def _forbidden(*_args: object, **_kwargs: object) -> sqlite3.Connection:
        raise AssertionError("dry-run must not open the database through db.get_conn()")

    monkeypatch.setattr(cli.db, "get_conn", _forbidden)
    monkeypatch.setattr(cli.db, "migrate", _forbidden)
    args = cli.build_parser().parse_args(
        ["admin", "curate", "rollback-quota", "--since", "20260903T000000Z-target", "--dry-run"]
    )

    assert cli._admin(args) == 0
    assert "rows_would_remove=1 rows_kept=3" in capsys.readouterr().out

    missing = tmp_path / "missing" / "nope.db"
    monkeypatch.setenv("AI_RADAR_DB", str(missing))
    capsys.readouterr()
    assert cli._admin(args) == 1
    assert not missing.exists() and not missing.parent.exists()
    assert "database not found" in capsys.readouterr().err


def test_rollback_quota_second_pass_is_a_pure_no_op(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = _setup_rollback_db(tmp_path)
    monkeypatch.setenv("AI_RADAR_DB", str(db_path))
    args = cli.build_parser().parse_args(["admin", "curate", "rollback-quota", "--since", "20260903T000000Z-target"])
    assert cli._admin(args) == 0
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE curated_items SET summary_json='{\"summary\":\"fresh\"}' WHERE run_id='20260903T000000Z-target'"
        )
    after_first = _database_state(db_path)

    assert cli._admin(args) == 0

    assert _database_state(db_path) == after_first


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            "UPDATE curation_runs SET shadow_json=json_set(shadow_json,'$.baseline','other') "
            "WHERE id='20260903T000000Z-target'",
            "shadow_json.baseline is 'other'",
        ),
        (
            "UPDATE curation_runs SET shadow_json=json_set(shadow_json,'$.baseline_only',json('{}')) "
            "WHERE id='20260903T000000Z-target'",
            "baseline_only is not a list",
        ),
        (
            "UPDATE curation_runs SET shadow_json=json_set(shadow_json,'$.baseline_only',"
            'json(\'[{"item_id":"a","raw_weighted_score":1},{"item_id":"a","raw_weighted_score":2}]\')) '
            "WHERE id='20260903T000000Z-target'",
            "duplicate item_id 'a'",
        ),
        (
            "UPDATE curation_runs SET shadow_json=json_set(shadow_json,'$.baseline_only',"
            'json(\'[{"item_id":"a","raw_weighted_score":"x"}]\')) '
            "WHERE id='20260903T000000Z-target'",
            "non-numeric raw_weighted_score",
        ),
        (
            "UPDATE curation_runs SET shadow_json=json_set(shadow_json,'$.quota_only_count','x') "
            "WHERE id='20260903T000000Z-target'",
            "quota_only_count is 'x'",
        ),
        (
            "UPDATE curated_items SET reason_json=json_set(reason_json,'$.source_quota.baseline','other') "
            "WHERE item_id='keep-a'",
            "source_quota baseline 'other'",
        ),
        (
            "UPDATE curated_items SET reason_json=json_set(reason_json,'$.source_quota.kind','') "
            "WHERE item_id='keep-a'",
            "empty source_quota kind",
        ),
        (
            "UPDATE curated_items SET reason_json=json_set(reason_json,'$.source_quota.source_cap',0) "
            "WHERE item_id='keep-a'",
            "source_cap=0; expected a positive int or null",
        ),
        (
            "UPDATE curated_items SET reason_json='[]' WHERE item_id='keep-a'",
            "non-object reason_json",
        ),
        (
            "UPDATE curated_items SET reason_json=json_remove(reason_json,'$.raw_weighted_score') "
            "WHERE item_id='keep-a'",
            "has no numeric raw_weighted_score/weighted_score",
        ),
    ],
)
def test_rollback_quota_rejects_each_malformed_value_without_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mutate: str,
    expected: str,
) -> None:
    db_path = _setup_rollback_db(tmp_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(mutate)
    before = _database_state(db_path)
    monkeypatch.setenv("AI_RADAR_DB", str(db_path))
    args = cli.build_parser().parse_args(["admin", "curate", "rollback-quota", "--since", "20260903T000000Z-target"])

    assert cli._admin(args) == 1

    assert _database_state(db_path) == before
    assert expected in capsys.readouterr().err


def test_resolve_source_quota_rejects_unsupported_argument_types() -> None:
    with pytest.raises(TypeError, match="unsupported value"):
        cli._resolve_source_quota("x=0.2")


@pytest.mark.parametrize(
    "semantics",
    ["tier_adjusted_before_rank_calibration", "unadjusted_before_rank_calibration"],
)
def test_rollback_accepts_every_semantics_a_run_could_legitimately_carry(semantics: str) -> None:
    """Both, because runs on either side of the tier multiplier's retirement are both valid.

    ADR-20260903-bc36 freezes the run *shape*; the field's value moves when the score behind it
    moves (ADR-20260906-7c31 retired the multiplier). Validating every stored run against the
    single current constant broke rollback for the entire archive the moment that constant was
    updated -- 24 cases in this file went red at once, and the only thing wrong was the check.
    """
    shadow = {
        "baseline": "same_run_without_source_quota",
        "baseline_only": [{"item_id": "a", "raw_weighted_score": 1.0}],
        "policy": "source-quota-v1",
        "quota_only_count": 1,
        "score_semantics": semantics,
    }
    cli._validate_source_quota_shadow(shadow)


def test_rollback_still_rejects_a_semantics_string_nobody_ever_wrote() -> None:
    shadow = {
        "baseline": "same_run_without_source_quota",
        "baseline_only": [{"item_id": "a", "raw_weighted_score": 1.0}],
        "policy": "source-quota-v1",
        "quota_only_count": 1,
        "score_semantics": "something_else",
    }
    with pytest.raises(ValueError, match="score_semantics"):
        cli._validate_source_quota_shadow(shadow)
