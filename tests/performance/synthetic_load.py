from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class LedgerSnapshot:
    cursor: int
    observed_ns: int


@dataclass(frozen=True, slots=True)
class IntervalProof:
    authority: str
    classification: str
    budget_eligible: bool
    cursor_continuous: bool
    start_cursor: int
    end_cursor: int
    interval_started_ns: int
    interval_ended_ns: int
    intersecting_stages: tuple[str, ...]
    events: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "authority": self.authority,
            "classification": self.classification,
            "budget_eligible": self.budget_eligible,
            "cursor_continuous": self.cursor_continuous,
            "start_cursor": self.start_cursor,
            "end_cursor": self.end_cursor,
            "interval_started_ns": self.interval_started_ns,
            "interval_ended_ns": self.interval_ended_ns,
            "intersecting_stages": list(self.intersecting_stages),
            "events": list(self.events),
        }


@dataclass(slots=True)
class _StageEvent:
    event_id: int
    stage: str
    producer: str
    started_ns: int
    ended_ns: int | None = None


class StageHandle:
    def __init__(self, ledger: SyntheticIntervalLedger, event_id: int) -> None:
        self._ledger = ledger
        self._event_id = event_id
        self._finished = False

    def finish(self) -> None:
        if not self._finished:
            self._ledger._finish_stage(self._event_id)
            self._finished = True


class SyntheticIntervalLedger:
    authority = "phase0_test_owned_synthetic_interval_ledger"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cursor = 0
        self._cursor_history: list[int] = []
        self._events: list[_StageEvent] = []

    def _advance_cursor(self) -> int:
        self._cursor += 1
        self._cursor_history.append(self._cursor)
        return self._cursor

    def snapshot(self) -> LedgerSnapshot:
        with self._lock:
            return LedgerSnapshot(cursor=self._cursor, observed_ns=time.monotonic_ns())

    def start_stage(self, stage: str, *, producer: str) -> StageHandle:
        with self._lock:
            event_id = self._advance_cursor()
            self._events.append(
                _StageEvent(
                    event_id=event_id,
                    stage=stage,
                    producer=producer,
                    started_ns=time.monotonic_ns(),
                )
            )
        return StageHandle(self, event_id)

    def _finish_stage(self, event_id: int) -> None:
        with self._lock:
            event = next(event for event in self._events if event.event_id == event_id)
            if event.ended_ns is not None:
                return
            event.ended_ns = time.monotonic_ns()
            self._advance_cursor()

    def inject_cursor_gap_for_test(self) -> None:
        with self._lock:
            self._cursor += 2
            self._cursor_history.append(self._cursor)

    def prove_interval(self, before: LedgerSnapshot, after: LedgerSnapshot) -> IntervalProof:
        if before.observed_ns > after.observed_ns or before.cursor > after.cursor:
            raise ValueError("measurement interval is reversed")
        with self._lock:
            relevant_history = [cursor for cursor in self._cursor_history if cursor <= after.cursor]
            cursor_continuous = relevant_history == list(range(1, after.cursor + 1))
            events = tuple(
                {
                    "event_id": event.event_id,
                    "stage": event.stage,
                    "producer": event.producer,
                    "started_ns": event.started_ns,
                    "ended_ns": event.ended_ns,
                }
                for event in self._events
                if event.started_ns <= after.observed_ns
                and (event.ended_ns is None or event.ended_ns >= before.observed_ns)
            )
        intersecting_stages = tuple(sorted({str(event["stage"]) for event in events}))
        if not cursor_continuous:
            classification = "unknown"
        elif intersecting_stages:
            classification = "busy"
        else:
            classification = "idle"
        return IntervalProof(
            authority=self.authority,
            classification=classification,
            budget_eligible=classification in {"idle", "busy"},
            cursor_continuous=cursor_continuous,
            start_cursor=before.cursor,
            end_cursor=after.cursor,
            interval_started_ns=before.observed_ns,
            interval_ended_ns=after.observed_ns,
            intersecting_stages=intersecting_stages,
            events=events,
        )
