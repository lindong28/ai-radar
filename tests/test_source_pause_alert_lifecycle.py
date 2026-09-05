from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from airadar.admin import alerts as alerts_module
from airadar.admin.alerts import (
    AlertRuleResult,
    AlertSignals,
    _silent_source_signal,
    evaluate_rules,
    prepare_alert_source_pause,
    run_alert_results_state_machine,
)


def _a7_firing(*source_ids: str) -> AlertRuleResult:
    return AlertRuleResult(
        rule_id="A7",
        title="来源静默",
        firing=True,
        detail="source silence",
        action="inspect sources",
        values={
            "silent_sources": [
                {"source_id": source_id, "name": source_id}
                for source_id in source_ids
            ]
        },
    )


def _a7_paused_resolution(
    *paused_source_ids: str,
    detail: str = "source excluded from evaluation",
    evaluated_source_ids: tuple[str, ...] = (),
    evaluation_state: str = "healthy",
) -> AlertRuleResult:
    return AlertRuleResult(
        rule_id="A7",
        title="来源静默",
        firing=False,
        detail=detail,
        action="inspect sources",
        values={
            "paused_source_ids": list(paused_source_ids),
            "evaluated_source_ids": list(evaluated_source_ids),
        },
        evaluation_state=evaluation_state,  # type: ignore[arg-type]
    )


def _sender(calls: list[str]):
    def send(text: str, *, severity: str = "page") -> dict[str, object]:
        calls.append(f"{severity}:{text}")
        return {"skipped": False}

    return send


def _ledger(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _normal_signals() -> AlertSignals:
    return AlertSignals(
        upstream_sample_size=20,
        upstream_error_rate=0.0,
        upstream_schema_error_rate=0.0,
        stage_error_rate={},
        stage_p95_latency_ms={},
        minutes_since_successful_pipeline=10,
        consecutive_skip_logs=0,
        server_error_rate=0.0,
        fetch_failed_ratio=0.0,
        items_today=300,
    )


def _write_a7_fixture(
    path: Path,
    *,
    now: datetime,
    sources: list[tuple[str, int, int]],
    item_hours: dict[str, tuple[float, ...]] | None = None,
) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE sources(
                id TEXT PRIMARY KEY,
                name TEXT,
                enabled INTEGER,
                paused INTEGER,
                kind TEXT,
                meta_json TEXT
            );
            CREATE TABLE items(id TEXT PRIMARY KEY, source_id TEXT, fetched_at TEXT);
            """
        )
        for source_id, enabled, paused in sources:
            conn.execute(
                "INSERT INTO sources VALUES (?,?,?,?,?,?)",
                (source_id, source_id, enabled, paused, "feed", "{}"),
            )
        for source_id, hours in (item_hours or {}).items():
            for index, age_hours in enumerate(hours):
                conn.execute(
                    "INSERT INTO items VALUES (?,?,?)",
                    (
                        f"{source_id}-{index}",
                        source_id,
                        (now - timedelta(hours=age_hours)).isoformat(),
                    ),
                )
        conn.commit()


def _evaluate_a7_from_db(path: Path, now: datetime) -> AlertRuleResult:
    evaluated_source_ids: list[str] = []
    silent, evaluated, unevaluable, faded, quiet_x, paused = _silent_source_signal(
        path,
        now,
        evaluated_source_ids=evaluated_source_ids,
    )
    signals = _normal_signals()
    signals.silent_sources = silent
    signals.evaluated_sources = evaluated
    signals.evaluated_source_ids = evaluated_source_ids
    signals.unevaluable_sources = unevaluable
    signals.faded_sources = faded
    signals.quiet_x_sources = quiet_x
    signals.paused_source_ids = paused
    return next(result for result in evaluate_rules(signals) if result.rule_id == "A7")


def _write_legacy_a7_state(path: Path, episode_since: str) -> None:
    path.write_text(
        json.dumps(
            {
                "A7": {
                    "state": "firing",
                    "since": episode_since,
                    "last_notified": episode_since,
                    "detail": "legacy A7 firing",
                    "announced": True,
                    "severity": "page",
                }
            }
        ),
        encoding="utf-8",
    )


def _legacy_firing_row(
    episode_since: str,
    silent_sources: object,
    *,
    ts: str | None = None,
) -> dict[str, object]:
    return {
        "ts": ts or episode_since,
        "rule_id": "A7",
        "severity": "page",
        "type": "firing",
        "channel": "ALERT",
        "episode_since": episode_since,
        "values": {"silent_sources": silent_sources},
    }


def _write_ledger_rows(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_a4_action_names_active_wechat_entry_and_paused_mp2rss_absence() -> None:
    a4 = next(result for result in evaluate_rules(_normal_signals()) if result.rule_id == "A4")

    assert "微信活跃抓取入口走 Wechat2RSS" in a4.action
    assert "暂停的 Mp2RSS 不应出现在 OK/FAIL 行" in a4.action
    assert a4.action.count("Mp2RSS") == 1
    assert "微信源走 Mp2RSS" not in a4.action


def test_a7_delivered_firing_ledger_omits_pause_control_metadata(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "alert-state.json"
    event_path = tmp_path / "alert-events.jsonl"
    calls: list[str] = []
    firing = _a7_firing("active")
    firing.values["paused_source_ids"] = ["wx_mp2rss"]
    firing.values["evaluated_source_ids"] = ["active"]

    opened = run_alert_results_state_machine(
        [firing],
        state_path=state_path,
        event_path=event_path,
        now=datetime.fromisoformat("2026-09-04T09:00:00+08:00"),
        send=_sender(calls),
    )

    assert opened["sent_count"] == 1
    assert len(calls) == 1
    firing_rows = [row for row in _ledger(event_path) if row.get("type") == "firing"]
    assert len(firing_rows) == 1
    assert "paused_source_ids" not in firing_rows[0]["values"]
    assert "evaluated_source_ids" not in firing_rows[0]["values"]


def test_prepare_legacy_a7_accepts_repeated_agreeing_episode_identity(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "alert-state.json"
    event_path = tmp_path / "alert-events.jsonl"
    episode_since = "2026-09-04T08:00:00+08:00"
    _write_legacy_a7_state(state_path, episode_since)
    _write_ledger_rows(
        event_path,
        [
            _legacy_firing_row(
                episode_since,
                [{"source_id": " active "}, {"source_id": "wx_mp2rss"}],
            ),
            _legacy_firing_row(
                episode_since,
                [
                    {"source_id": "wx_mp2rss"},
                    {"source_id": "active"},
                    {"source_id": "wx_mp2rss"},
                ],
            ),
        ],
    )

    preview = prepare_alert_source_pause(
        source_id="wx_mp2rss",
        state_path=state_path,
        event_path=event_path,
        dry_run=True,
    )

    assert preview["status"] == "SEEDABLE"
    assert preview["source_ids"] == ["active", "wx_mp2rss"]


def test_prepare_legacy_a7_blocks_conflicting_opening_episode_identity(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "alert-state.json"
    event_path = tmp_path / "alert-events.jsonl"
    episode_since = "2026-09-04T08:00:00+08:00"
    _write_legacy_a7_state(state_path, episode_since)
    _write_ledger_rows(
        event_path,
        [
            _legacy_firing_row(
                episode_since,
                [{"source_id": "active"}, {"source_id": "wx_mp2rss"}],
            ),
            _legacy_firing_row(
                "2026-09-04T00:00:00Z",
                [{"source_id": "wx_mp2rss"}],
                ts="2026-09-04T00:00:00+00:00",
            ),
        ],
    )

    preview = prepare_alert_source_pause(
        source_id="wx_mp2rss",
        state_path=state_path,
        event_path=event_path,
        dry_run=True,
    )

    assert preview["status"] == "BLOCKED_MISSING_EPISODE_IDENTITY"
    assert preview["source_ids"] == []
    assert "conflicting opening" in str(preview["reason"])


def test_prepare_legacy_a7_uses_timestamp_identified_opening_before_later_variations(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "alert-state.json"
    event_path = tmp_path / "alert-events.jsonl"
    episode_since = "2026-09-04T08:00:00+08:00"
    _write_legacy_a7_state(state_path, episode_since)
    _write_ledger_rows(
        event_path,
        [
            _legacy_firing_row(
                "2026-09-04T00:00:00Z",
                [{"source_id": "active"}, {"source_id": "wx_mp2rss"}],
                ts="2026-09-04T00:00:00+00:00",
            ),
            _legacy_firing_row(
                episode_since,
                [{"source_id": "wx_mp2rss"}],
                ts="2026-09-04T08:05:00+08:00",
            ),
            _legacy_firing_row(
                episode_since,
                [
                    {"source_id": "active"},
                    {"source_id": "quiet_x"},
                    {"source_id": "wx_mp2rss"},
                ],
                ts="2026-09-04T08:15:00+08:00",
            ),
        ],
    )

    preview = prepare_alert_source_pause(
        source_id="wx_mp2rss",
        state_path=state_path,
        event_path=event_path,
        dry_run=True,
    )

    assert preview["status"] == "SEEDABLE"
    assert preview["source_ids"] == ["active", "wx_mp2rss"]


def test_prepare_legacy_a7_blocks_when_only_later_episode_row_survives(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "alert-state.json"
    event_path = tmp_path / "alert-events.jsonl"
    episode_since = "2026-09-04T08:00:00+08:00"
    _write_legacy_a7_state(state_path, episode_since)
    _write_ledger_rows(
        event_path,
        [
            _legacy_firing_row(
                episode_since,
                [{"source_id": "wx_mp2rss"}],
                ts="2026-09-04T08:05:00+08:00",
            )
        ],
    )

    preview = prepare_alert_source_pause(
        source_id="wx_mp2rss",
        state_path=state_path,
        event_path=event_path,
        dry_run=True,
    )

    assert preview["status"] == "BLOCKED_MISSING_EPISODE_IDENTITY"
    assert preview["source_ids"] == []
    assert "opening firing ledger row" in str(preview["reason"])


@pytest.mark.parametrize(
    "opening_identity",
    [
        None,
        [{"source_id": 7}],
        [],
        [{"source_id": "active"}],
    ],
    ids=["missing", "malformed", "empty", "requested-source-absent"],
)
def test_prepare_legacy_a7_blocks_untrustworthy_opening_episode_identity(
    tmp_path: Path,
    opening_identity: object,
) -> None:
    state_path = tmp_path / "alert-state.json"
    event_path = tmp_path / "alert-events.jsonl"
    episode_since = "2026-09-04T08:00:00+08:00"
    _write_legacy_a7_state(state_path, episode_since)
    _write_ledger_rows(
        event_path,
        [
            _legacy_firing_row(episode_since, opening_identity),
            _legacy_firing_row(
                episode_since,
                [{"source_id": "active"}, {"source_id": "wx_mp2rss"}],
            ),
        ],
    )

    preview = prepare_alert_source_pause(
        source_id="wx_mp2rss",
        state_path=state_path,
        event_path=event_path,
        dry_run=True,
    )

    assert preview["status"] == "BLOCKED_MISSING_EPISODE_IDENTITY"


def test_a7_source_pause_close_retries_after_internal_ledger_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_path = tmp_path / "alert-state.json"
    event_path = tmp_path / "alert-events.jsonl"
    calls: list[str] = []
    started = datetime.fromisoformat("2026-09-04T09:00:00+08:00")
    run_alert_results_state_machine(
        [_a7_firing("wx_mp2rss")],
        state_path=state_path,
        event_path=event_path,
        now=started,
        send=_sender(calls),
    )

    real_write = alerts_module._write_ledger_rows
    failed_pause_writes = 0

    def fail_first_pause_write(path: Path, rows: list[dict[str, object]]) -> None:
        nonlocal failed_pause_writes
        if (
            failed_pause_writes == 0
            and any(row.get("reason") == "source_paused" for row in rows)
        ):
            failed_pause_writes += 1
            raise OSError("injected source_paused ledger failure")
        real_write(path, rows)

    monkeypatch.setattr(alerts_module, "_write_ledger_rows", fail_first_pause_write)
    failed = run_alert_results_state_machine(
        [_a7_paused_resolution("wx_mp2rss")],
        state_path=state_path,
        event_path=event_path,
        now=datetime.fromisoformat("2026-09-04T09:05:00+08:00"),
        send=_sender(calls),
    )

    assert failed["sent_count"] == 0
    assert len(calls) == 1
    assert failed_pause_writes == 1
    assert json.loads(state_path.read_text(encoding="utf-8"))["A7"]["state"] == "firing"
    assert not [row for row in _ledger(event_path) if row.get("reason") == "source_paused"]

    retried = run_alert_results_state_machine(
        [_a7_paused_resolution("wx_mp2rss")],
        state_path=state_path,
        event_path=event_path,
        now=datetime.fromisoformat("2026-09-04T09:10:00+08:00"),
        send=_sender(calls),
    )

    assert retried["sent_count"] == 0
    assert len(calls) == 1
    assert json.loads(state_path.read_text(encoding="utf-8"))["A7"]["state"] == "ok"
    pause_rows = [row for row in _ledger(event_path) if row.get("reason") == "source_paused"]
    assert len(pause_rows) == 1


@pytest.mark.parametrize("rollover", [False, True], ids=["full_pause", "rollover"])
def test_a7_source_pause_retry_after_state_write_failure_does_not_duplicate_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rollover: bool,
) -> None:
    state_path = tmp_path / "alert-state.json"
    event_path = tmp_path / "alert-events.jsonl"
    calls: list[str] = []
    started = datetime.fromisoformat("2026-09-04T09:00:00+08:00")
    run_alert_results_state_machine(
        [_a7_firing("wx_mp2rss")],
        state_path=state_path,
        event_path=event_path,
        now=started,
        send=_sender(calls),
    )
    state_before = json.loads(state_path.read_text(encoding="utf-8"))["A7"]
    result = _a7_firing("new_active") if rollover else _a7_paused_resolution("wx_mp2rss")
    if rollover:
        result.values["paused_source_ids"] = ["wx_mp2rss"]

    real_write_state = alerts_module._write_state
    failed_state_writes = 0

    def fail_after_pause_ledger(
        path: Path,
        state: dict[str, dict[str, object]],
    ) -> None:
        nonlocal failed_state_writes
        pause_rows = (
            [row for row in _ledger(event_path) if row.get("reason") == "source_paused"]
            if event_path.exists()
            else []
        )
        if failed_state_writes == 0 and pause_rows:
            failed_state_writes += 1
            raise OSError("injected state write failure after source_paused ledger success")
        real_write_state(path, state)

    monkeypatch.setattr(alerts_module, "_write_state", fail_after_pause_ledger)
    with pytest.raises(OSError, match="state write failure after source_paused ledger success"):
        run_alert_results_state_machine(
            [result],
            state_path=state_path,
            event_path=event_path,
            now=started + timedelta(minutes=5),
            send=_sender(calls),
        )

    assert failed_state_writes == 1
    assert json.loads(state_path.read_text(encoding="utf-8"))["A7"] == state_before
    pause_rows = [row for row in _ledger(event_path) if row.get("reason") == "source_paused"]
    assert len(pause_rows) == 1

    retried = run_alert_results_state_machine(
        [result],
        state_path=state_path,
        event_path=event_path,
        now=started + timedelta(minutes=10),
        send=_sender(calls),
    )

    assert retried["sent_count"] == 0
    pause_rows = [row for row in _ledger(event_path) if row.get("reason") == "source_paused"]
    assert len(pause_rows) == 1
    final_state = json.loads(state_path.read_text(encoding="utf-8"))["A7"]
    if rollover:
        assert final_state["state"] == "firing"
        assert final_state["source_ids"] == ["new_active"]
    else:
        assert final_state["state"] == "ok"


def test_a7_source_pause_idempotency_ignores_malformed_unrelated_row(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "alert-state.json"
    event_path = tmp_path / "alert-events.jsonl"
    calls: list[str] = []
    started = datetime.fromisoformat("2026-09-04T09:00:00+08:00")
    run_alert_results_state_machine(
        [_a7_firing("active", "wx_mp2rss")],
        state_path=state_path,
        event_path=event_path,
        now=started,
        send=_sender(calls),
    )
    malformed = {
        "ts": (started + timedelta(minutes=1)).isoformat(),
        "rule_id": "A7",
        "severity": "page",
        "type": "resolved",
        "channel": "INTERNAL",
        "reason": "source_paused",
        "episode_since": started.isoformat(),
        "values": {
            "episode_source_ids": ["active", "wx_mp2rss"],
            "paused_source_ids": ["wx_mp2rss"],
        },
    }
    _write_ledger_rows(event_path, [*_ledger(event_path), malformed])

    closed = run_alert_results_state_machine(
        [_a7_paused_resolution("active", "wx_mp2rss")],
        state_path=state_path,
        event_path=event_path,
        now=started + timedelta(minutes=5),
        send=_sender(calls),
    )

    assert closed["sent_count"] == 0
    pause_rows = [row for row in _ledger(event_path) if row.get("reason") == "source_paused"]
    assert len(pause_rows) == 2
    valid_rows = [
        row
        for row in pause_rows
        if row.get("values")
        == {
            "episode_source_ids": ["active", "wx_mp2rss"],
            "paused_source_ids": ["active", "wx_mp2rss"],
        }
    ]
    assert len(valid_rows) == 1


def test_a7_source_pause_idempotency_keeps_distinct_episodes(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "alert-state.json"
    event_path = tmp_path / "alert-events.jsonl"
    calls: list[str] = []
    started = datetime.fromisoformat("2026-09-04T09:00:00+08:00")
    run_alert_results_state_machine(
        [_a7_firing("wx_mp2rss")],
        state_path=state_path,
        event_path=event_path,
        now=started,
        send=_sender(calls),
    )
    run_alert_results_state_machine(
        [_a7_paused_resolution("wx_mp2rss")],
        state_path=state_path,
        event_path=event_path,
        now=started + timedelta(minutes=5),
        send=_sender(calls),
    )
    second_started = started + timedelta(minutes=10)
    run_alert_results_state_machine(
        [_a7_firing("wx_mp2rss")],
        state_path=state_path,
        event_path=event_path,
        now=second_started,
        send=_sender(calls),
    )
    run_alert_results_state_machine(
        [_a7_firing("wx_mp2rss")],
        state_path=state_path,
        event_path=event_path,
        now=second_started + timedelta(minutes=31),
        send=_sender(calls),
    )
    run_alert_results_state_machine(
        [_a7_paused_resolution("wx_mp2rss")],
        state_path=state_path,
        event_path=event_path,
        now=second_started + timedelta(minutes=35),
        send=_sender(calls),
    )

    pause_rows = [row for row in _ledger(event_path) if row.get("reason") == "source_paused"]
    assert [row["episode_since"] for row in pause_rows] == [
        started.isoformat(),
        second_started.isoformat(),
    ]


def test_a7_legacy_pause_waits_for_identity_prepare_before_closing(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "alert-state.json"
    event_path = tmp_path / "alert-events.jsonl"
    episode_since = "2026-09-04T08:00:00+08:00"
    calls: list[str] = []
    _write_legacy_a7_state(state_path, episode_since)
    _write_ledger_rows(
        event_path,
        [_legacy_firing_row(episode_since, [{"source_id": "wx_mp2rss"}])],
    )
    paused = _a7_paused_resolution("wx_mp2rss")

    blocked = run_alert_results_state_machine(
        [paused],
        state_path=state_path,
        event_path=event_path,
        now=datetime.fromisoformat("2026-09-04T09:00:00+08:00"),
        send=_sender(calls),
    )

    assert blocked["sent_count"] == 0
    assert calls == []
    assert json.loads(state_path.read_text(encoding="utf-8"))["A7"]["state"] == "firing"
    assert not [row for row in _ledger(event_path) if row.get("reason") == "source_paused"]

    preview = prepare_alert_source_pause(
        source_id="wx_mp2rss",
        state_path=state_path,
        event_path=event_path,
        dry_run=True,
    )
    assert preview["status"] == "SEEDABLE"
    seeded = prepare_alert_source_pause(
        source_id="wx_mp2rss",
        state_path=state_path,
        event_path=event_path,
        dry_run=False,
        expected_input_digest=str(preview["input_digest"]),
    )
    assert seeded["status"] == "SEEDED"

    closed = run_alert_results_state_machine(
        [paused],
        state_path=state_path,
        event_path=event_path,
        now=datetime.fromisoformat("2026-09-04T09:05:00+08:00"),
        send=_sender(calls),
    )

    assert closed["sent_count"] == 0
    assert calls == []
    assert json.loads(state_path.read_text(encoding="utf-8"))["A7"]["state"] == "ok"
    pause_rows = [row for row in _ledger(event_path) if row.get("reason") == "source_paused"]
    assert len(pause_rows) == 1


def test_a7_episode_identity_snapshot_does_not_shrink_during_firing(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "alert-state.json"
    event_path = tmp_path / "alert-events.jsonl"
    calls: list[str] = []

    run_alert_results_state_machine(
        [_a7_firing("active", "wx_mp2rss")],
        state_path=state_path,
        event_path=event_path,
        now=datetime.fromisoformat("2026-09-04T09:00:00+08:00"),
        send=_sender(calls),
    )
    run_alert_results_state_machine(
        [_a7_firing("wx_mp2rss")],
        state_path=state_path,
        event_path=event_path,
        now=datetime.fromisoformat("2026-09-04T09:05:00+08:00"),
        send=_sender(calls),
    )

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["A7"]["source_ids"] == ["active", "wx_mp2rss"]

    closed = run_alert_results_state_machine(
        [
            _a7_paused_resolution(
                "wx_mp2rss",
                detail="active source recovered",
                evaluated_source_ids=("active",),
            )
        ],
        state_path=state_path,
        event_path=event_path,
        now=datetime.fromisoformat("2026-09-04T09:10:00+08:00"),
        send=_sender(calls),
    )

    assert closed["sent_count"] == 1
    assert len(calls) == 2
    resolved_text = calls[-1]
    assert "部分来源不在评估范围内" in resolved_text
    assert "本次告警中已恢复来源：active" in resolved_text
    assert "因暂停不再评估的来源：wx_mp2rss" in resolved_text

    ledger = _ledger(event_path)
    assert not [row for row in ledger if row.get("reason") == "source_paused"]
    resolved_rows = [
        row
        for row in ledger
        if row.get("rule_id") == "A7"
        and row.get("type") == "resolved"
        and row.get("channel") != "INTERNAL"
    ]
    assert len(resolved_rows) == 1
    assert "paused_source_ids" not in resolved_rows[0]["values"]
    assert "evaluated_source_ids" not in resolved_rows[0]["values"]
    assert "本次告警中已恢复来源：active" in str(resolved_rows[0]["detail"])
    assert "因暂停不再评估的来源：wx_mp2rss" in str(
        resolved_rows[0]["detail"]
    )


def test_a7_evaluated_source_ids_are_exact_current_evaluable_set(
    tmp_path: Path,
) -> None:
    now = datetime.fromisoformat("2026-09-05T01:00:00+00:00")
    db_path = tmp_path / "evaluated-source-ids.db"
    _write_a7_fixture(
        db_path,
        now=now,
        sources=[
            ("z_healthy", 1, 0),
            ("a_healthy", 1, 0),
            ("sparse", 1, 0),
            ("paused", 1, 1),
            ("disabled", 0, 0),
        ],
        item_hours={
            "z_healthy": (1, 2, 3, 4, 5),
            "a_healthy": (1, 2, 3, 4, 5),
            "sparse": (1,),
            "paused": (1, 2, 3, 4, 5),
            "disabled": (1, 2, 3, 4, 5),
        },
    )

    result = _evaluate_a7_from_db(db_path, now)

    assert result.firing is False
    assert result.values["evaluated_sources"] == 2
    assert result.values["evaluated_source_ids"] == ["a_healthy", "z_healthy"]
    assert result.values["paused_source_ids"] == ["paused"]


@pytest.mark.parametrize("active_state", ["absent", "disabled", "unevaluable"])
def test_a7_mixed_pause_keeps_opening_source_firing_without_current_evidence(
    tmp_path: Path,
    active_state: str,
) -> None:
    now = datetime.fromisoformat("2026-09-05T01:00:00+00:00")
    db_path = tmp_path / f"opening-{active_state}.db"
    sources = [("paused", 1, 1)]
    if active_state == "disabled":
        sources.append(("active", 0, 0))
    elif active_state == "unevaluable":
        sources.append(("active", 1, 0))
    _write_a7_fixture(db_path, now=now, sources=sources)
    result = _evaluate_a7_from_db(db_path, now)
    assert result.values["evaluated_source_ids"] == []

    state_path = tmp_path / f"alert-state-{active_state}.json"
    event_path = tmp_path / f"alert-events-{active_state}.jsonl"
    calls: list[str] = []
    run_alert_results_state_machine(
        [_a7_firing("active", "paused")],
        state_path=state_path,
        event_path=event_path,
        now=now,
        send=_sender(calls),
    )
    unresolved = run_alert_results_state_machine(
        [result],
        state_path=state_path,
        event_path=event_path,
        now=now + timedelta(minutes=5),
        send=_sender(calls),
    )

    assert unresolved["sent_count"] == 0
    assert len(calls) == 1
    assert json.loads(state_path.read_text(encoding="utf-8"))["A7"]["state"] == "firing"
    assert not [row for row in _ledger(event_path) if row.get("type") == "resolved"]


def test_a7_mixed_pause_keeps_faded_opening_source_firing(
    tmp_path: Path,
) -> None:
    now = datetime.fromisoformat("2026-09-05T01:00:00+00:00")
    db_path = tmp_path / "opening-faded.db"
    _write_a7_fixture(
        db_path,
        now=now,
        sources=[("active", 1, 0), ("paused", 1, 1)],
        item_hours={"active": (31 * 24, 32 * 24, 33 * 24, 34 * 24, 35 * 24)},
    )
    result = _evaluate_a7_from_db(db_path, now)
    assert result.evaluation_state == "degraded"
    assert result.values["evaluated_source_ids"] == []
    assert [row["source_id"] for row in result.values["faded_sources"]] == ["active"]

    state_path = tmp_path / "alert-state.json"
    event_path = tmp_path / "alert-events.jsonl"
    calls: list[str] = []
    run_alert_results_state_machine(
        [_a7_firing("active", "paused")],
        state_path=state_path,
        event_path=event_path,
        now=now,
        send=_sender(calls),
    )
    unresolved = run_alert_results_state_machine(
        [result],
        state_path=state_path,
        event_path=event_path,
        now=now + timedelta(minutes=5),
        send=_sender(calls),
    )

    assert unresolved["sent_count"] == 0
    assert len(calls) == 1
    assert json.loads(state_path.read_text(encoding="utf-8"))["A7"]["state"] == "firing"
    assert not [row for row in _ledger(event_path) if row.get("type") == "resolved"]


def test_a7_mixed_pause_preserves_degraded_resolution_for_external_faded_source(
    tmp_path: Path,
) -> None:
    now = datetime.fromisoformat("2026-09-05T01:00:00+00:00")
    db_path = tmp_path / "external-faded.db"
    _write_a7_fixture(
        db_path,
        now=now,
        sources=[("active", 1, 0), ("paused", 1, 1), ("external", 1, 0)],
        item_hours={
            "active": (1, 2, 3, 4, 5),
            "external": (31 * 24, 32 * 24, 33 * 24, 34 * 24, 35 * 24),
        },
    )
    result = _evaluate_a7_from_db(db_path, now)
    assert result.evaluation_state == "degraded"
    assert result.values["evaluated_source_ids"] == ["active"]
    assert [row["source_id"] for row in result.values["faded_sources"]] == ["external"]

    state_path = tmp_path / "alert-state.json"
    event_path = tmp_path / "alert-events.jsonl"
    calls: list[str] = []
    run_alert_results_state_machine(
        [_a7_firing("active", "paused")],
        state_path=state_path,
        event_path=event_path,
        now=now,
        send=_sender(calls),
    )
    resolved = run_alert_results_state_machine(
        [result],
        state_path=state_path,
        event_path=event_path,
        now=now + timedelta(minutes=5),
        send=_sender(calls),
    )

    assert resolved["sent_count"] == 1
    assert len(calls) == 2
    assert "🟡 A7 来源静默 转为不可评估" in calls[-1]
    assert "本次告警中已恢复来源" not in calls[-1]
    assert "因暂停不再评估的来源" not in calls[-1]
    state = json.loads(state_path.read_text(encoding="utf-8"))["A7"]
    assert state["state"] == "ok"
    assert state["evaluation_state"] == "degraded"


def test_a7_no_pause_healthy_resolution_remains_ordinary(
    tmp_path: Path,
) -> None:
    now = datetime.fromisoformat("2026-09-05T01:00:00+00:00")
    db_path = tmp_path / "no-pause.db"
    _write_a7_fixture(
        db_path,
        now=now,
        sources=[("active", 1, 0)],
        item_hours={"active": (1, 2, 3, 4, 5)},
    )
    result = _evaluate_a7_from_db(db_path, now)
    assert result.values["evaluated_source_ids"] == ["active"]
    assert result.values["paused_source_ids"] == []

    state_path = tmp_path / "alert-state.json"
    event_path = tmp_path / "alert-events.jsonl"
    calls: list[str] = []
    run_alert_results_state_machine(
        [_a7_firing("active")],
        state_path=state_path,
        event_path=event_path,
        now=now,
        send=_sender(calls),
    )
    resolved = run_alert_results_state_machine(
        [result],
        state_path=state_path,
        event_path=event_path,
        now=now + timedelta(minutes=5),
        send=_sender(calls),
    )

    assert resolved["sent_count"] == 1
    assert len(calls) == 2
    assert "✅ A7 来源静默 已恢复" in calls[-1]
    assert "部分来源不在评估范围内" not in calls[-1]


def test_a7_episode_identity_snapshot_does_not_expand_during_firing(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "alert-state.json"
    event_path = tmp_path / "alert-events.jsonl"
    calls: list[str] = []

    run_alert_results_state_machine(
        [_a7_firing("wx_mp2rss")],
        state_path=state_path,
        event_path=event_path,
        now=datetime.fromisoformat("2026-09-04T09:00:00+08:00"),
        send=_sender(calls),
    )
    run_alert_results_state_machine(
        [_a7_firing("active", "wx_mp2rss")],
        state_path=state_path,
        event_path=event_path,
        now=datetime.fromisoformat("2026-09-04T09:05:00+08:00"),
        send=_sender(calls),
    )

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["A7"]["source_ids"] == ["wx_mp2rss"]

    closed = run_alert_results_state_machine(
        [_a7_paused_resolution("wx_mp2rss")],
        state_path=state_path,
        event_path=event_path,
        now=datetime.fromisoformat("2026-09-04T09:10:00+08:00"),
        send=_sender(calls),
    )

    assert closed["sent_count"] == 0
    assert len(calls) == 1
    pause_rows = [row for row in _ledger(event_path) if row.get("reason") == "source_paused"]
    assert len(pause_rows) == 1
    assert pause_rows[0]["values"] == {
        "episode_source_ids": ["wx_mp2rss"],
        "paused_source_ids": ["wx_mp2rss"],
    }


def test_a7_full_pause_rolls_over_to_new_unrelated_firing_episode(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "alert-state.json"
    event_path = tmp_path / "alert-events.jsonl"
    calls: list[str] = []
    started = datetime.fromisoformat("2026-09-04T09:00:00+08:00")
    run_alert_results_state_machine(
        [_a7_firing("wx_mp2rss")],
        state_path=state_path,
        event_path=event_path,
        now=started,
        send=_sender(calls),
    )
    unrelated = _a7_firing("new_active")
    unrelated.values["paused_source_ids"] = ["wx_mp2rss"]

    rolled_over = run_alert_results_state_machine(
        [unrelated],
        state_path=state_path,
        event_path=event_path,
        now=started + timedelta(minutes=5),
        send=_sender(calls),
    )

    assert rolled_over["sent_count"] == 0
    assert len(calls) == 1, "the new episode must retain the existing rule cooldown"
    state = json.loads(state_path.read_text(encoding="utf-8"))["A7"]
    assert state["state"] == "firing"
    assert state["since"] == (started + timedelta(minutes=5)).isoformat()
    assert state["source_ids"] == ["new_active"]
    assert state["announced"] is False
    pause_rows = [row for row in _ledger(event_path) if row.get("reason") == "source_paused"]
    assert len(pause_rows) == 1
    assert pause_rows[0]["episode_since"] == started.isoformat()
    assert pause_rows[0]["values"] == {
        "episode_source_ids": ["wx_mp2rss"],
        "paused_source_ids": ["wx_mp2rss"],
    }

    notified = run_alert_results_state_machine(
        [unrelated],
        state_path=state_path,
        event_path=event_path,
        now=started + timedelta(minutes=31),
        send=_sender(calls),
    )
    assert notified["sent_count"] == 1
    assert len(calls) == 2
    assert json.loads(state_path.read_text(encoding="utf-8"))["A7"]["source_ids"] == [
        "new_active"
    ]


def test_a7_full_pause_rollover_preserves_old_episode_when_ledger_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_path = tmp_path / "alert-state.json"
    event_path = tmp_path / "alert-events.jsonl"
    calls: list[str] = []
    started = datetime.fromisoformat("2026-09-04T09:00:00+08:00")
    run_alert_results_state_machine(
        [_a7_firing("wx_mp2rss")],
        state_path=state_path,
        event_path=event_path,
        now=started,
        send=_sender(calls),
    )
    state_before = json.loads(state_path.read_text(encoding="utf-8"))["A7"]
    real_write = alerts_module._write_ledger_rows
    failed_pause_writes = 0

    def fail_pause_write(path: Path, rows: list[dict[str, object]]) -> None:
        nonlocal failed_pause_writes
        if any(row.get("reason") == "source_paused" for row in rows):
            failed_pause_writes += 1
            raise OSError("injected rollover ledger failure")
        real_write(path, rows)

    monkeypatch.setattr(alerts_module, "_write_ledger_rows", fail_pause_write)
    unrelated = _a7_firing("new_active")
    unrelated.values["paused_source_ids"] = ["wx_mp2rss"]

    blocked = run_alert_results_state_machine(
        [unrelated],
        state_path=state_path,
        event_path=event_path,
        now=started + timedelta(minutes=5),
        send=_sender(calls),
    )

    assert blocked["sent_count"] == 0
    assert len(calls) == 1
    assert failed_pause_writes == 1
    assert json.loads(state_path.read_text(encoding="utf-8"))["A7"] == state_before
    assert not [row for row in _ledger(event_path) if row.get("reason") == "source_paused"]


@pytest.mark.parametrize("unsafe_shape", ["multiple_firing", "empty_current_identity"])
def test_a7_full_pause_rollover_fails_closed_for_ambiguous_identity(
    tmp_path: Path,
    unsafe_shape: str,
) -> None:
    state_path = tmp_path / "alert-state.json"
    event_path = tmp_path / "alert-events.jsonl"
    calls: list[str] = []
    started = datetime.fromisoformat("2026-09-04T09:00:00+08:00")
    run_alert_results_state_machine(
        [_a7_firing("wx_mp2rss")],
        state_path=state_path,
        event_path=event_path,
        now=started,
        send=_sender(calls),
    )
    state_before = json.loads(state_path.read_text(encoding="utf-8"))["A7"]
    if unsafe_shape == "multiple_firing":
        state_before["lifecycles"]["notice"] = {
            "state": "firing",
            "since": started.isoformat(),
            "last_notified": None,
            "detail": "pending notice",
            "announced": False,
            "notification_sequence": 0,
            "pending_notification": None,
            "evaluation_state": "healthy",
            "source_ids": ["pending"],
        }
        state_path.write_text(json.dumps({"A7": state_before}), encoding="utf-8")
    current = _a7_firing("new_active")
    if unsafe_shape == "empty_current_identity":
        current.values["silent_sources"] = []
    current.values["paused_source_ids"] = ["wx_mp2rss"]

    blocked = run_alert_results_state_machine(
        [current],
        state_path=state_path,
        event_path=event_path,
        now=started + timedelta(minutes=5),
        send=_sender(calls),
    )

    assert blocked["sent_count"] == 0
    assert len(calls) == 1
    assert json.loads(state_path.read_text(encoding="utf-8"))["A7"] == state_before
    assert not [row for row in _ledger(event_path) if row.get("reason") == "source_paused"]


def test_a7_source_pause_ledger_excludes_unrelated_paused_sources(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "alert-state.json"
    event_path = tmp_path / "alert-events.jsonl"
    calls: list[str] = []
    started = datetime.fromisoformat("2026-09-04T09:00:00+08:00")
    run_alert_results_state_machine(
        [_a7_firing("wx_mp2rss")],
        state_path=state_path,
        event_path=event_path,
        now=started,
        send=_sender(calls),
    )
    paused = _a7_paused_resolution("wx_mp2rss", "unrelated_paused")

    closed = run_alert_results_state_machine(
        [paused],
        state_path=state_path,
        event_path=event_path,
        now=datetime.fromisoformat("2026-09-04T09:05:00+08:00"),
        send=_sender(calls),
    )
    repeated = run_alert_results_state_machine(
        [paused],
        state_path=state_path,
        event_path=event_path,
        now=datetime.fromisoformat("2026-09-04T09:10:00+08:00"),
        send=_sender(calls),
    )

    assert closed["sent_count"] == repeated["sent_count"] == 0
    assert len(calls) == 1
    assert json.loads(state_path.read_text(encoding="utf-8"))["A7"]["state"] == "ok"
    pause_rows = [row for row in _ledger(event_path) if row.get("reason") == "source_paused"]
    assert len(pause_rows) == 1
    assert pause_rows[0]["values"] == {
        "episode_source_ids": ["wx_mp2rss"],
        "paused_source_ids": ["wx_mp2rss"],
    }
