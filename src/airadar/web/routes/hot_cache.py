"""Background-refreshed candidate cache for the hot-topics ranking (ADR-060).

The hot ranking used to hydrate 600 archive rows per request to return the top
2, which cost 14.3s on the origin host. This module holds the expensive half —
the hydrated candidate rows, including their related discussions — so the
request path only has to filter, score and sort dicts in memory.

Two invariants carry the design; both are load-bearing and neither is obvious:

1. **The cache holds candidates, never a finished payload.** ``generated_at``,
   the relative timestamps on ``/hot`` and "this item aged out of the window"
   are recomputed on every request from the caller's own clock. Caching the
   finished payload would freeze all three, and items nearest the cutoff would
   go wrong first.
2. **The request path never computes.** Only the prewarm and refresh threads
   fill the cache. Sync routes run on Starlette's shared threadpool, so any
   wait here turns compute concurrency into worker queueing, which can stall
   unrelated sync pages. Callers that find nothing usable must degrade rather
   than block — see ``curated.hot`` and the three-state contract in ADR-060.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from . import curated_archive

_LOGGER = logging.getLogger("airadar.hot_cache")

# The public ``hours`` parameter tops out here, and every smaller window is a
# subset of this one, so a single cached candidate set serves all of them. One
# key means no per-hours slots, hence no LRU eviction, no cross-key concurrency
# and no way for an exotic ``hours`` value to starve the default one.
WINDOW_HOURS_MAX = 168

# Age at which a still-servable entry triggers a background refresh.
#
# Only the unsignaled writes below need this sweep — a write the generation
# signal *does* cover already invalidates on the next peek, immediately. So the
# cadence is a pure cost knob, and it is not free: one hydration measured 3.5-6s
# against the 3.8GB local database, on a two-vCPU origin that also runs the
# pipeline. 120s keeps the 180s ceiling intact with a minute of slack for the
# refresh to land, at half the CPU of a 60s sweep.
REFRESH_AFTER_SECONDS = 120.0

# How often the keeper looks. It bounds how long a *signalled* change waits
# before rehydration starts, so it has to be well under the pipeline's 15-minute
# cadence; each poll is one sub-millisecond version query.
KEEPER_POLL_SECONDS = 10.0
# Hard staleness ceiling. Beyond this the entry is unusable, full stop.
#
# This is not a tuning knob: it stands in for proving that the cache-generation
# triggers cover every write that can change the payload, and they demonstrably
# do not. `_batch_related_discussions` draws on *all* items while
# `archive_cache_items_ai` only bumps for curated ones, and
# `archive_cache_sources_au_id` ignores `sources.name` changes even though the
# name is rendered. Bounding staleness is cheaper than enumerating writers.
MAX_STALE_SECONDS = 180.0

# Timestamps are stored as ISO-8601 UTC (`2026-05-31T11:08:16Z`), sometimes with
# a millisecond part. Only those two exact shapes compare correctly as strings,
# so they are the *allowlist*: anything else escapes into the candidate set and
# lets `hot_datetime` decide.
#
# Allowlisting rather than blocklisting is the point. Each blocklist attempt
# leaked a different shape: a prefix-only pattern let `...T07:30:00-05:00`
# through (it sorts by local wall clock, not UTC); adding a `Z` suffix still let
# `...T00:00:00ZZ` through (the `*` absorbed the first `Z`, while Python turns
# *every* `Z` into `+00:00` and fails to parse); patching that still let
# `...T00:00:00+bogusZ` through. Enumerating malformed shapes does not converge;
# enumerating the two well-formed ones does.
_SECONDS_PREFIX = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]"
_TRUSTED_STAMP_GLOBS = (f"{_SECONDS_PREFIX}Z", f"{_SECONDS_PREFIX}.[0-9][0-9][0-9]Z")

# The SQL cutoff is pushed back two seconds before being rendered without a
# fractional part. Same instant, different precision, compares wrong otherwise:
# `.000Z` < `Z` because `.` (0x2E) sorts below `Z` (0x5A). With the two-second
# slack, the whole-second prefix of any qualifying timestamp is already strictly
# greater, so comparison settles at character 19 and the fraction never
# participates — at any number of decimal places.
_CUTOFF_SAFETY_SECONDS = 2

_UNTRUSTED_STAMP_SQL = " AND ".join(
    f"i.published_at NOT GLOB '{pattern}'" for pattern in _TRUSTED_STAMP_GLOBS
)

_HOT_WINDOW_CLAUSE = f" AND (i.published_at >= ? OR ({_UNTRUSTED_STAMP_SQL}))"


def _cutoff_literal(now: datetime, hours: int) -> str:
    cutoff = now - timedelta(hours=hours, seconds=_CUTOFF_SAFETY_SECONDS)
    return cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")


def candidate_version(conn: sqlite3.Connection) -> tuple[Any, ...]:
    """Version tuple that must change whenever the cached candidates could.

    ``MAX(curation_runs.id)`` is in here because migration 014 narrowed
    ``archive_cache_curated_ai`` to fire only the first time an item is
    curated. Without it, a fresh run that re-scores an already-curated item
    leaves ``archive_generation`` untouched while every heat value moves.
    """
    base = curated_archive._curated_data_version(conn, include_enrichment=True)
    # COUNT 与 MAX 都要：run id 是 TEXT，MAX 取的是字典序最大值，一次 id 比现有
    # 最大值小的插入不会改变 MAX——只靠 MAX 会漏掉那一次。
    run_row = conn.execute("SELECT COUNT(*), MAX(id) FROM curation_runs").fetchone()
    run_count, latest_run = (run_row[0], run_row[1]) if run_row is not None else (0, None)
    # `sources.enabled` 与 `kind` 直接决定候选的成员资格（`_archive_where` 用
    # 它们过滤），`name` 直接进 payload——而 `archive_cache_sources_au_id` 只认
    # `UPDATE OF id`，所以 `admin sources reload` 停用一个来源不会推进
    # generation。
    #
    # 用内容摘要而不是几个聚合量：聚合会碰撞，而碰撞的方向是**漏失效**。等长
    # 改名（`Wire` → `News`）或"一开一关"都能让 COUNT/SUM 那组值原封不动，于是
    # 缓存继续供旧成员或旧名称。这段只在 keeper 线程上按 KEEPER_POLL_SECONDS
    # 跑，不在请求路径上，所以读全表几百行做摘要的成本无关紧要。
    rows = conn.execute(
        "SELECT id, enabled, COALESCE(kind, ''), COALESCE(name, '') FROM sources ORDER BY id"
    ).fetchall()
    digest = hashlib.blake2b(digest_size=16)
    for row in rows:
        for value in row:
            # 长度前缀而不是分隔符：分隔符只在"数据里不会出现它"时才无歧义，而
            # `name` 是配置里来的自由文本，没有任何东西禁止它含分隔符。歧义的
            # 后果是两组不同的 sources 摘出同一个值 → 漏失效。
            encoded = str(value).encode("utf-8")
            digest.update(len(encoded).to_bytes(4, "big"))
            digest.update(encoded)
    return (*base, run_count, latest_run, len(rows), digest.hexdigest())


def compute_candidates(conn: sqlite3.Connection, now: datetime) -> list[dict[str, Any]]:
    """Hydrate every archive row whose event time may fall inside the window.

    The result is a superset of what the Python filter keeps, which is the only
    property the caller may rely on — see the two-branch argument in ADR-060.
    """
    where, params, _ = curated_archive._archive_where(None, None)
    return curated_archive._archive_items(
        conn,
        where + _HOT_WINDOW_CLAUSE,
        [*params, _cutoff_literal(now, WINDOW_HOURS_MAX)],
        None,
        q=None,
        normalized_category=None,
        limit=-1,
        offset=0,
    )


def hot_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def rank_hot_items(
    candidates: list[dict[str, Any]],
    *,
    now: datetime,
    hours: int,
    limit: int,
) -> list[dict[str, Any]]:
    """把候选集按热度排出前 ``limit`` 条。

    整段逻辑对 ``now`` 是纯函数——缓存只供给 ``candidates``，年龄过滤、热度、
    排序都用调用方的时钟现算，所以缓存住的东西不会把时间冻住（ADR-060）。
    """
    ranked: list[dict[str, Any]] = []
    for item in candidates:
        published_at = item.get("published_at")
        fetched_at = item.get("fetched_at")
        published_ts = hot_datetime(published_at)
        use_published = published_ts is not None and published_ts <= now
        event_time = published_at if use_published else fetched_at
        event_ts = published_ts if use_published else hot_datetime(fetched_at)
        if event_ts is None:
            continue
        age_seconds = (now - event_ts).total_seconds()
        if age_seconds < 0 or age_seconds > hours * 3600:
            continue
        score = float(str(item.get("weighted_score") or 0.0))
        related = item.get("related_discussions")
        related_discussions = related if isinstance(related, list) else []
        heat = round(score * 10 + len(related_discussions) * 5)
        ranked.append(
            {
                "id": item.get("id"),
                "title": item.get("title_zh") or item.get("title"),
                "url": item.get("url"),
                "source_name": item.get("source_name"),
                "published_at": published_at,
                "fetched_at": fetched_at,
                "event_time": event_time,
                "source_kind": item.get("source_kind"),
                "author": item.get("author"),
                "related_discussions": related_discussions,
                "heat": heat,
            }
        )
    ranked.sort(key=lambda entry: (-int(str(entry["heat"] or 0)), str(entry["id"])))
    return ranked[:limit]


class HotCandidateCache:
    """Single-entry cache filled only by background threads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._version: tuple[Any, ...] | None = None
        self._candidates: list[dict[str, Any]] | None = None
        self._stored_at = 0.0
        self._db_path: str | None = None
        self._keeper: threading.Thread | None = None
        self._unready_since: float | None = None
        self._last_unready_log = 0.0

    def bind(self, db_path: object) -> None:
        """Point the cache at a database, discarding anything held for another one.

        ``peek`` no longer checks the version, and the version is what used to
        carry the database path — so a rebind that kept the old candidates
        would serve the previous database's rows for up to ``MAX_STALE_SECONDS``.
        Production runs one app against one database and never hits this, but a
        lifespan restart in-process, an embedded second app, or a test suite
        does.
        """
        resolved = str(db_path)
        with self._lock:
            if self._db_path is not None and self._db_path != resolved:
                self._candidates = None
                self._version = None
                self._stored_at = 0.0
            self._db_path = resolved

    def peek(self) -> list[dict[str, Any]] | None:
        """Return usable candidates, or ``None``. In-memory only — no DB, no compute.

        The only bound on serving is age: past ``MAX_STALE_SECONDS`` the entry
        is unusable, full stop. A *version* mismatch deliberately does not
        reject the entry. Rejecting on version sounds stricter, and it is
        exactly what produced the symptom this whole change exists to remove:
        every curation run (every 15 minutes in production) invalidated the
        cache instantly while rehydration still needed one keeper poll plus
        several seconds, leaving a 4-16s window in which arriving visitors got
        a blank hot section. Serving the previous generation through that
        window costs at most ~16s of extra lag on a 48-hour ranking whose
        upstream only moves every 15 minutes.

        This also does not kick off a refresh. When requests could start
        hydrations, the last client retry could be the one that started a
        successful hydration and then never read it — it got its 503 and gave
        up while the data landed a second later. The keeper owns every
        hydration, so that shape cannot occur.
        """
        with self._lock:
            fresh = (
                self._candidates is not None
                and (time.monotonic() - self._stored_at) < MAX_STALE_SECONDS
            )
            candidates = self._candidates if fresh else None
            if fresh:
                self._unready_since = None
        if candidates is None:
            self._log_unready()
        return candidates

    def _log_unready(self) -> None:
        now = time.monotonic()
        with self._lock:
            if self._unready_since is None:
                self._unready_since = now
            unready_for = now - self._unready_since
            # Throttled: this fires on the request path, and an unready cache
            # under load would otherwise flood the log with one line per hit.
            if now - self._last_unready_log < 30.0 and self._last_unready_log:
                return
            self._last_unready_log = now
            reason = (
                "never populated"
                if self._candidates is None
                else f"older than max_stale ({MAX_STALE_SECONDS:.0f}s)"
            )
        _LOGGER.warning(
            "hot candidates unready (%s); serving degraded for %.1fs",
            reason,
            unready_for,
        )

    def start_keeper(self) -> None:
        """Start the one thread that is allowed to hydrate. Idempotent.

        ``start()`` happens *inside* the lock on purpose. Storing the thread
        under the lock and starting it outside leaves a window where a second
        caller sees ``is_alive() == False`` on the not-yet-started thread and
        creates another keeper — which would put duplicate hydration back.
        """
        with self._lock:
            if self._keeper is not None and self._keeper.is_alive():
                return
            if self._db_path is None:
                return
            self._keeper = threading.Thread(
                target=self._keep_warm, name="hot-candidate-keeper", daemon=True
            )
            self._keeper.start()

    def _keep_warm(self) -> None:
        """Poll for staleness and rehydrate ahead of traffic, forever.

        The poll exists because nothing notifies us: curation runs in a separate
        process on its own schedule, and the only way this process learns that
        the run happened is to look. Reacting to a request instead would put the
        entire cost on whoever arrives first after each run.

        The poll itself is a sub-millisecond version query; the expensive
        hydration only runs when that query says something changed, or when the
        entry has aged past REFRESH_AFTER_SECONDS.
        """
        while True:
            try:
                self._refresh_if_needed()
            except Exception:  # noqa: BLE001 - the keeper must outlive any single failure
                _LOGGER.exception("hot candidate keeper iteration failed")
            time.sleep(KEEPER_POLL_SECONDS)

    def _refresh_if_needed(self) -> None:
        # A dedicated connection, closed in `finally`. A leaked reader on this
        # path once grew the WAL until /healthz returned 500 with CANTOPEN, so
        # this is not defensive boilerplate.
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=5000")
            version = candidate_version(conn)
            with self._lock:
                age = time.monotonic() - self._stored_at
                current = (
                    self._candidates is not None
                    and self._version == version
                    and age < REFRESH_AFTER_SECONDS
                )
            if current:
                return
            started = time.monotonic()
            candidates = compute_candidates(conn, datetime.now(UTC))
        finally:
            conn.close()
        with self._lock:
            self._version = version
            self._candidates = candidates
            self._stored_at = time.monotonic()
        _LOGGER.info(
            "hot candidates refreshed: %d candidates in %.2fs",
            len(candidates),
            time.monotonic() - started,
        )

    def reset_for_tests(self) -> None:
        with self._lock:
            self._version = None
            self._candidates = None
            self._stored_at = 0.0
            self._unready_since = None
            self._last_unready_log = 0.0


HOT_CANDIDATE_CACHE = HotCandidateCache()


def prewarm_hot_candidates(db_path: object) -> None:
    """Bind the cache to the database and start the keeper thread.

    Deliberately not synchronous: the lifespan hook it runs from gates
    readiness, and a cold hydration takes seconds that the deploy health check
    should not have to wait through.
    """
    HOT_CANDIDATE_CACHE.bind(db_path)
    HOT_CANDIDATE_CACHE.start_keeper()
