from __future__ import annotations

import hashlib
import json
import platform
import secrets
import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from ..ruleset import current_version
from .dedup import deduplicate_candidates
from .score import ScoredCandidate, tier_multiplier, weighted_score
from .weights import DEFAULT_WEIGHTS, Weights

DEFAULT_THRESHOLD = 6.5
DEFAULT_LIMIT = 40
DEFAULT_FRESHNESS_QUOTA = 36
DEFAULT_FRESHNESS_FLOOR = 4.0
DEFAULT_FRESHNESS_WINDOW_HOURS = 48
DISPLAY_SCORE_HIGH = 92
DISPLAY_SCORE_LOW = 62
SOURCE_QUOTA_POLICY = "source-quota-v1"
# What the per-item ``baseline_selected`` flag and the run-level ``baseline_only`` list
# are measured against: the same run, same candidates and ordering, quotas disabled.
SOURCE_QUOTA_BASELINE = "same_run_without_source_quota"
# Which score ``baseline_only[].raw_weighted_score`` carries. It has to move when the score
# behind it moves -- the tier multiplier was retired on 2026-09-06 (ADR-20260906-7c31), and
# leaving the old value would have made runs from either side of that change read alike in the
# audit trail, which is the thing this record exists to prevent.
SOURCE_QUOTA_SCORE_SEMANTICS = "unadjusted_before_rank_calibration"
# What validation accepts. ADR-20260903-bc36 freezes the run *shape*, not a single value of this
# field: a run recorded before the multiplier was retired legitimately carries the old string,
# and rollback has to keep working on it. Comparing every stored run against the current constant
# broke rollback for the entire archive the moment the constant moved. New values are appended
# here, never substituted.
KNOWN_SOURCE_QUOTA_SCORE_SEMANTICS = frozenset(
    {"tier_adjusted_before_rank_calibration", SOURCE_QUOTA_SCORE_SEMANTICS}
)


@dataclass(frozen=True)
class SourceQuota:
    kind_caps: dict[str, float]
    per_source: float | None


DEFAULT_SOURCE_QUOTA = SourceQuota(kind_caps={"x": 0.20}, per_source=0.075)


@dataclass(frozen=True)
class CurationRun:
    id: str
    ruleset_version: str
    weights: Weights
    threshold: float
    input_eval_ids: list[int]
    output_curated_ids: list[str]


def _json(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _run_id() -> str:
    return f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(2)}"


def _parse_utc(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _shanghai_date(value: str) -> str | None:
    parsed = _parse_utc(value)
    if parsed is None:
        return None
    return (parsed + timedelta(hours=8)).date().isoformat()


def parse_source_quota(text: str) -> SourceQuota | None:
    """Parse ``x=0.20,source=0.075``; ``off`` disables quotas.

    An empty or whitespace-only value is rejected: callers that read the
    environment treat it as "unset" (defaults apply) before calling this.
    """
    stripped = text.strip()
    if not stripped:
        raise ValueError("source quota is empty; expected e.g. x=0.20,source=0.075 or off")
    if stripped.lower() == "off":
        return None
    kind_caps: dict[str, float] = {}
    per_source: float | None = None
    for assignment in stripped.split(","):
        name, separator, raw_share = assignment.partition("=")
        name = name.strip()
        if not separator or not name or not raw_share.strip():
            raise ValueError(f"invalid source quota assignment: {assignment!r}")
        try:
            share = float(raw_share)
        except ValueError:
            raise ValueError(f"share for {name!r} is not a number: {raw_share.strip()!r}") from None
        if not 0 < share <= 1:
            raise ValueError(f"source quota share must be within (0, 1]: {assignment!r}")
        if name == "source":
            per_source = share
        else:
            kind_caps[name] = share
    return SourceQuota(kind_caps=kind_caps, per_source=per_source)


def _quota_cap(limit: int, share: float | None) -> int:
    return limit if share is None else max(1, round(limit * share))


def _fill(
    candidates_fresh: list[ScoredCandidate],
    candidates_filtered: list[ScoredCandidate],
    limit: int,
    freshness_quota: int,
    quota: SourceQuota | None,
) -> list[ScoredCandidate]:
    selected: list[ScoredCandidate] = []
    seen: set[str] = set()
    kind_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}

    def admit(candidate: ScoredCandidate) -> bool:
        if candidate.item_id in seen:
            return False
        if quota is not None:
            kind_cap = _quota_cap(limit, quota.kind_caps.get(candidate.kind))
            source_cap = _quota_cap(limit, quota.per_source)
            if kind_counts.get(candidate.kind, 0) >= kind_cap:
                return False
            if source_counts.get(candidate.source_id, 0) >= source_cap:
                return False
        selected.append(candidate)
        seen.add(candidate.item_id)
        kind_counts[candidate.kind] = kind_counts.get(candidate.kind, 0) + 1
        source_counts[candidate.source_id] = source_counts.get(candidate.source_id, 0) + 1
        return True

    # Same bound as the pre-quota code path (``fresh[:freshness_quota]``): the
    # fresh segment is not clamped to ``limit`` so ``source_quota=None`` keeps
    # reproducing the previous selection byte for byte.
    fresh_limit = max(0, freshness_quota)
    for candidate in candidates_fresh:
        if len(selected) >= fresh_limit:
            break
        admit(candidate)
    for candidate in candidates_filtered:
        if len(selected) >= limit:
            break
        admit(candidate)
    return selected


def _calibrate_selected_scores(selected: list[ScoredCandidate]) -> list[ScoredCandidate]:
    if len(selected) <= 1:
        return selected
    span = DISPLAY_SCORE_HIGH - DISPLAY_SCORE_LOW
    calibrated: list[ScoredCandidate] = []
    for index, candidate in enumerate(selected):
        display_score = round(DISPLAY_SCORE_HIGH - (span * index / (len(selected) - 1)))
        reason = dict(candidate.reason)
        reason["raw_weighted_score"] = candidate.weighted_score
        reason["score_calibration"] = {
            "method": "rank_linear_v1",
            "display_score": display_score,
            "range": [DISPLAY_SCORE_LOW, DISPLAY_SCORE_HIGH],
        }
        calibrated.append(replace(candidate, weighted_score=display_score / 10, reason=reason))
    return calibrated


# Per-category ranking multipliers. One entry, `paper`, fitted 2026-09-09 against AIHOT's own
# published 0-100 score. An earlier set shipped and was withdrawn the same day; that history
# is kept below because it defines the precondition this fit had to satisfy.
#
# The mechanism is kept (candidates carry `primary_category`, and reason_json records the
# factor) because the characterisation behind it still holds and the next attempt will need
# both. What did not hold was the evidence for those particular numbers.
#
# WHAT WENT WRONG, because it will recur otherwise. AIHOT's own selection is near
# category-neutral (tutorial 0.90x, product 0.88x, paper 0.92x, industry 0.69x) with model at
# 2.19x; ours lifted paper 2.63x, which looked like a clear defect to correct. Coefficients
# {paper: 0.90, tutorial: 1.12} were fitted against a paired reading on one pool and shipped
# at TV 0.193 -> 0.151. Then the cross-window check: TV is computed over CATEGORIES, and each
# day's categories come from whichever enrich prompt was live that day. Re-running enrich for
# 900 items at one prompt so the windows became comparable flipped the answer --
#
#   window   no multipliers -> with them
#   09-04    0.325 -> 0.259   (-0.066)
#   09-05    0.178 -> 0.107   (-0.071)
#   09-06    0.271 -> 0.403   (+0.132)
#   09-07    0.255 -> 0.333   (+0.078)
#   09-08    0.169 -> 0.191   (+0.022)
#   09-09    0.183 -> 0.209   (+0.026)   <- the same window read -0.041 before the re-run
#
# 4 of 6 worse, mean +0.020. The 09-09 reversal is the whole lesson: the reading that
# justified shipping was taken over stale category labels, and it inverted once they were
# recomputed. Any future fit here must re-enrich its windows at one prompt FIRST -- otherwise
# it is fitting the labelling regime, not the ranking.
#
# THE 2026-09-09 `paper` FIT, and how it differs from the withdrawn one. Two things changed.
# The precondition above is met ON THE FITTING SIDE: every label the coefficient was fitted
# against comes from one enrich stamp (2026-09-08.r2.31b2065e). It is NOT met on the applying
# side and cannot be -- `_load_candidates` reads whatever the newest clean enrich row says, and
# 56% of the rows it can reach still carry `2026-05-13.r2`. Measured exposure where it matters,
# the fresh pool of the last eight windows: 57 of 206 items labelled `paper` (27.7%) carry an
# older stamp. Their labels are not worse -- `paper` precision against AIHOT's own label is 98%
# (n=66) on the old stamp against 94% (n=49) on the current one -- so the drift is real but
# points the harmless way today. Nothing binds this table to an enrich version; the next prompt
# revision silently changes who gets demoted, with no test and no alert. See
# docs/issues/aihot-fit-eval.md. And the fitting target is no longer composition TV -- it is
# per-input agreement with AIHOT's own score, which is a function of the input rather than a
# marginal proportion. TV was the wrong target twice over: it is measured across two different
# labellers (ours agrees with AIHOT's on 68.5% of identical items, worth 0.129 TV by itself),
# and per day it is saturated -- AIHOT's own daily composition sits 0.269 from its own pooled
# average while pure resampling noise at 5-35 picks/day already produces 0.253.
#
# The defect: our scorer puts papers at the 71st percentile of the pool where AIHOT's puts them
# at the 47th (n=2000 matched items, AIHOT's labels). Papers score high on density (6.30, the
# highest of any category) and authority (6.91), which carry 0.40 and 0.10 of the weight -- but
# a digest is news, and a dense authoritative preprint is usually not news.
#
# Readings, all 20-seed half/half holdout:
#   per-input Spearman vs AIHOT's score   0.2977 -> 0.3679   +0.086, 20/20 seeds, t=15.5
#   pooled page composition over 9 windows, AIHOT's labels on both sides, via
#   scripts/eval/measure_curated_composition.py:
#     paper 14.2% -> 12.0% (AIHOT 8.2%), TV 0.176 -> 0.156
# Corroborated independently by a hand-labelled estimate of the whole page (not just the
# AIHOT-matched 30% of it): paper ran +14.7pp over AIHOT's share.
#
# WHY 0.95 AND NOT THE POOLED OPTIMUM. The per-input curve keeps improving past 0.70 because
# Spearman over the whole pool rewards pushing a systematically over-scored class to the bottom,
# and the pooled composition keeps improving to 0.80. Both of those are pooled quantities. The
# single-page behaviour is not, and it decided this value:
#
#   factor   pooled paper / TV      live pool 2026-09-10, papers of 40
#   1.00     14.2%  0.176           4
#   0.95     12.0%  0.156           3      <- this. AIHOT's 8.2% is 3.3 of 40.
#   0.90     11.9%  0.161           2
#   0.85     10.6%  0.154           1
#   0.80      9.8%  0.150           0      <- pooled-best, and an empty category on the page
#
# 0.80 wins pooled by 0.006 -- and that metric has no stated bandwidth at n=234, so the gap is
# inside the noise -- while producing a page with no papers at all on the window that happened to
# be live. 0.95 lands at 3 of 40 against the reference's 3.3. Chosen by the user 2026-09-10 after
# both readings were on the table.
#
# The mechanism behind the single-page cliff, because it is not obvious: 36 of the 40 slots come
# from `fresh`, which is the newest day's candidates -- about 200 of them -- cut at rank 36.
# Demoting a class inside that pool moves it past the cut in one step. Papers ranked
# [3, 18, 24, 25, 34] there before the factor and [31, 72, 86, 87, 97] after it at 0.80.
#
# RECONCILING TWO EARLIER NEGATIVES that look like they forbid this. Both were checked, and
# neither measured this mechanism:
#   ISSUE-FIT-23 (2026-09-06) fitted offsets for ALL FIVE categories into the composite score and
#     got +0.0011 (sd 0.0034, 13/20 seeds) -- indistinguishable from zero, and its learned paper
#     offset was +0.7, i.e. almost nothing. Re-measured here on the current data the five-offset
#     version reads -0.0135 (sd 0.0514, 12/20): the SAME null. The difference is parameter count,
#     not disagreement -- five offsets fitted against 121 reference picks overfit, while the
#     single paper coefficient holds at +0.084 (20/20). FIT-23 never tested a one-parameter form.
#   ADR-20260907-a1c4's wire-and-withdraw (2026-09-08) replaced the whole score scale with a
#     category-conditional affine map plus quantile alignment AND moved the admission threshold;
#     it pushed TV 0.394 -> 0.615. That is a much larger intervention than an ordering factor and
#     is not evidence about this one. It does stand as a warning that category terms have failed
#     here before, which is why this entry is one category, applied in one place, with the
#     detection channel below.
# Stratifying this fit by enrich stamp does NOT explain the FIT-23 gap: paper demotion helps
# under both regimes (current stamp +0.0839, old stamp +0.0145, 20/20 seeds each).
#
# HOW YOU FIND OUT IF THIS IS WRONG. No existing alert watches page composition, so this is a
# manual channel with named checks. Owner: whoever deploys this change.
#
#   T+1 run (within 15 minutes of deploy -- pipeline.sh runs every 15). This step verifies the
#   DEPLOY, not the decision. It has no revert criterion, and that is not an oversight:
#     sqlite3 data/radar.db "SELECT DISTINCT json_extract(reason_json,'\$.category'),
#       json_extract(reason_json,'\$.category_multiplier') FROM curated_items
#       WHERE run_id=(SELECT id FROM curation_runs ORDER BY id DESC LIMIT 1)"
#     Expect paper -> 0.95 and every other category -> 1.0. Anything else means the new code is
#     not what ran.
#   TWO SINGLE-RUN CHECKS THAT LOOK RIGHT AND ARE NOT -- both were written into this comment and
#   both were wrong, so they are recorded rather than deleted:
#     * "revert if paper = 0 of 40, one run is enough, because a reorder-only factor would leave
#       ~4". False, and it misfired on its first real run. Reordering alone DOES produce 0 of 40
#       when the fresh segment is cut deep: 36 of 40 slots come from the newest day's top 36, and
#       demoting a class inside a ~200-candidate fresh pool moves it past that cut in one step.
#       Live pool 2026-09-10, papers of 40 by factor: 1.00 -> 4, 0.95 -> 3, 0.90 -> 2, 0.85 -> 1,
#       0.80 -> 0. Composition on a single page is not evidence about this change.
#     * "revert if any selected item has raw_weighted_score < 6.5, since the factor cannot reach
#       the gates". Also false: the fresh segment gates at freshness_floor (4.0), not threshold,
#       so sub-6.5 selections are ordinary -- the live run returns 16 of them. More fundamentally
#       there is NO crisp single-run test for a gate leak, because a leak makes items ABSENT and
#       absence is not visible in the output.
#   So the revert criterion lives entirely in the pooled step below.
#
#   T+24h, and then weekly. The daily AIHOT capture refreshes the reference, so:
#     uv run python scripts/eval/measure_curated_composition.py
#     uv run python scripts/eval/measure_curated_composition.py --multiplier paper=1.0
#     REVERT IF: the first POOLED TV is worse than the second. This is the ONLY revert criterion.
#     Reproduced 2026-09-10 at 0.156 against 0.176. Both arms must be run back to back -- the
#     pairing assumes the pool did not change between them, and pipeline.sh writes every 15
#     minutes (recorded, not enforced; see docs/issues/aihot-fit-eval.md). Read only the POOLED line; that script prints the per-day noise floor
#     (0.176 on this data -- equal to the no-factor pooled value, which is exactly why per-day
#     numbers cannot be used here) precisely because the per-day numbers cannot resolve this effect.
#     LATEST DETECTION: one week. Past that the archive cost below stops being bounded by
#     anything anyone is watching.
#
# WHAT THIS CHANNEL DOES NOT CATCH, stated because it reads like full coverage otherwise: a page
# with a normal paper count where the WRONG papers were demoted, or where the items promoted into
# the freed slots are worse. Both need per-item judgement against the reference and there is no
# instrument for it -- the aihot-fit harness cannot see this factor at all (see
# docs/issues/aihot-fit-eval.md), so its own verdicts stay green either way.
# The cost of being wrong is NOT symmetric with the code change: reverting this constant is one
# line, but `/all` and the curated archive accumulate across runs, and no rollback path un-selects
# an item that was already archived (`admin curate rollback-quota` covers quotas only). So the
# real reversal cost is 40 items per run for however many runs it takes to notice -- which is why
# the first-round check above is part of this decision and not a follow-up.
#
# The other four stay at 1.0 deliberately. The largest remaining gap is model (ours 12.0% of the
# page against AIHOT's 30.2%), but a model multiplier does NOT survive cross-window holdout
# (+0.010 pooled TV, 9/20 splits better) -- AIHOT's 121 selected items over 9 windows cannot
# support fitting it. Ranking our own pool purely by our own score already yields 28.3% model,
# so that gap is in what reaches the ranker, not in the score. Revisit when the daily captures
# have accumulated roughly 500 selected items.
CATEGORY_MULTIPLIERS: dict[str, float] = {"paper": 0.95}
# The enrich ruleset the coefficient above was calibrated against. It is NOT enforced at runtime:
# `_load_candidates` reads each item's newest clean enrich row whatever its stamp, and on this
# machine 27.7% of the fresh pool's `paper` labels come from an older one. Filtering by stamp
# would make the coefficient's coverage lurch during every recompute, which is worse. So the
# guard is a test instead -- `test_category_multipliers_declare_the_enrich_stamp_they_were_fitted_on`
# goes red when the enrich ruleset moves, which is the moment a human has to decide whether
# `paper` still means what it meant here. Without it a prompt revision silently changes who gets
# demoted, and 2026-09-09's withdrawal was caused by exactly that kind of silent label drift.
CATEGORY_MULTIPLIERS_FITTED_ON_ENRICH = "2026-09-08.r2.31b2065e"


def category_multiplier(category: str) -> float:
    """1.0 for anything unlisted, including items with no enrich row yet."""

    return CATEGORY_MULTIPLIERS.get(category, 1.0)


def ranking_key(candidate: ScoredCandidate) -> tuple[float, str, str]:
    """Ordering key for curation. The category factor lives HERE and nowhere else.

    Deliberately not folded into ``weighted_score``: that value is read by two absolute gates
    (``threshold`` and ``freshness_floor``), by dedup, and by the archived ``raw_weighted_score``.
    Scaling it there would turn a ranking multiplier into a category-specific ADMISSION bar --
    at 0.80 a paper's effective threshold becomes 8.125 rather than 6.5, which is a different
    mechanism from the one the coefficient was fitted for, and one measurement said it drops 95
    papers out of the candidate pool over nine windows. It would also make
    ``SOURCE_QUOTA_SCORE_SEMANTICS`` ("unadjusted_before_rank_calibration") a false statement.

    Reproduce with `scripts/eval/measure_curated_composition.py` (nine windows, pooled, AIHOT's
    own labels on both sides). Readings 2026-09-10:

        --multiplier paper=1.0   paper 14.2%   TV 0.176
        (default, this)          paper 12.0%   TV 0.156
        --multiplier paper=0.80  paper  9.8%   TV 0.150   (better pooled, but see WHY 0.95)
        --gate at 0.80           paper  9.8%   TV 0.150   and 219 items dropped from the pool

    The last row is the point: scaling the gates as well produces the SAME page while making 219
    items ineligible. So this is not a trade of effect for safety -- the ordering-only form is
    strictly the smaller mechanism at the same outcome. (An earlier version of this comment
    claimed ordering-only keeps "77% of the improvement", from a scratch script that mismatched
    the day basis between the two sides and narrowed the tail slots to one day's candidates. That
    number does not reproduce; the script above is the corrected instrument.)

    WHAT IT DOES CHANGE, in full -- ordering is relative, so "only the order" understates it:
      * which items are selected, because the limit cuts a reordered list at a different place;
      * their rank;
      * their displayed score, which ``_calibrate_selected_scores`` derives linearly FROM the
        rank -- a demoted paper therefore shows a lower number to readers;
      * their score badge, since ``app.js`` buckets that displayed number at 80/65.
    The last two are not uniform across categories, which is the point of the factor but also
    means the reader-facing score moves for a reason the About page has to state. It does not
    change ``weighted_score`` itself, so the two absolute gates, dedup, and the archived
    ``raw_weighted_score`` are all untouched.
    """

    return (
        -candidate.weighted_score * category_multiplier(candidate.primary_category),
        candidate.published_at,
        candidate.item_id,
    )


def _primary_category(output_json: str | None) -> str:
    """Empty string when the item has no usable enrich row -- it then scores unmultiplied.

    Not an error: scoring and enrich are separate stages and an item can be scored before it
    is enriched, so this is the ordinary state for the newest candidates.
    """

    if not output_json:
        return ""
    try:
        payload = json.loads(output_json)
    except (TypeError, ValueError):
        return ""
    if not isinstance(payload, dict):
        return ""
    category = payload.get("primary_category")
    return category if isinstance(category, str) else ""


def _load_candidates(conn: sqlite3.Connection, weights: Weights) -> list[ScoredCandidate]:
    rows = conn.execute(
        """
        SELECT
          e.id, i.id, i.content_hash, i.url, i.published_at, s.tier,
          s.id, COALESCE(s.kind, 'feed'), e.numeric_json,
          (SELECT en.output_json FROM item_evaluations en
            WHERE en.item_id=e.item_id AND en.stage='enrich' AND en.error IS NULL
            ORDER BY en.id DESC LIMIT 1),
          (SELECT en.id FROM item_evaluations en
            WHERE en.item_id=e.item_id AND en.stage='enrich' AND en.error IS NULL
            ORDER BY en.id DESC LIMIT 1)
        FROM item_evaluations e
        JOIN items i ON i.id=e.item_id
        JOIN sources s ON s.id=i.source_id
        WHERE e.stage='scoring'
          AND s.enabled=1
          AND COALESCE(s.kind, 'feed') != 'wechat'
          AND e.error IS NULL
          AND e.id = (
            SELECT MAX(latest.id) FROM item_evaluations latest
            WHERE latest.item_id=e.item_id
              AND latest.stage='scoring'
              AND latest.error IS NULL
          )
        """
    ).fetchall()
    candidates: list[ScoredCandidate] = []
    for row in rows:
        numeric: dict[str, Any] = json.loads(row[8])
        category = _primary_category(row[9])
        score = weighted_score(numeric, weights, row[5])
        reason = {
            "scores": numeric,
            "tier": row[5],
            # What was actually applied, not what the tier would map to. Recording the mapping
            # while the score no longer carries it puts a 1.25 next to a number that was never
            # multiplied, and every consumer of reason_json reads them as a pair.
            "tier_multiplier": tier_multiplier(row[5]) if weights.uses_tier_multiplier else 1.0,
            "category": category,
            # Unlike tier_multiplier above, this factor is NOT in "weighted_score" -- it is
            # applied only in the ordering key (see ranking_key). Recorded anyway because a
            # consumer that reproduces the ordering needs it, and because a run where the table
            # was empty and one where it was filled would otherwise read alike.
            "category_multiplier": category_multiplier(category),
            "weighted_score": score,
        }
        candidates.append(
            ScoredCandidate(
                eval_id=row[0],
                item_id=row[1],
                content_hash=row[2],
                url=row[3],
                published_at=row[4],
                weighted_score=score,
                reason=reason,
                source_id=row[6],
                kind=row[7],
                primary_category=category,
                enrich_eval_id=int(row[10]) if row[10] is not None else None,
            )
        )
    return candidates


def _enrich_watermark(candidates: list[ScoredCandidate]) -> int | None:
    """The whole category snapshot, as one integer: the largest enrich row id THIS LOAD READ.

    Since 2026-09-10 each candidate's category is a ranking input (see ranking_key), and it comes
    from "the latest successful enrich row", which is not a fixed thing: re-enriching an item
    appends a newer row and the same code then orders it differently. Measured on this database:
    19.1% of candidates have more than one successful enrich row and **626 (2.7%) have had their
    category actually change**. So an ordering cannot be replayed without pinning which enrich
    rows were current.

    **Why it is derived from the loaded rows and not from `SELECT MAX(id)`.** A separate `MAX(id)`
    query gets its own snapshot, and neither placement is correct. Taken BEFORE the load it is a
    *lower* bound -- a row committed in between is visible to the load but excluded from the
    watermark, so a replay at `id <= watermark` silently reads the item's PREVIOUS category.
    Reproduced in-process: the load ordered an item as `paper` (x0.95) off enrich row 4 while the
    recorded watermark was 3, and the replay came back with no category at all (x1.0). Taken
    AFTER the load it is wrong the other way, naming rows the load never saw. The first version
    of this function did it before the load and its comment claimed "upper bound"; an adversarial
    reviewer showed the claim was inverted, and that the test guarding it could not see the
    difference because `MAX(id)` is constant across the load in a single-threaded fixture.

    Taking the max of the ids actually read is exact. `item_evaluations.id` is
    `INTEGER PRIMARY KEY AUTOINCREMENT` (migrations/001_init.sql), and SQLite serialises writers,
    so id order is commit order and ids are never reused. Therefore "the snapshot contains id=N"
    implies it contains every existing row with a smaller id, and `id <= max(read)` is necessarily
    a subset of what this load saw.

    The alternative was writing the categories themselves: 23.6k rows per run, measured at 829 KB
    per run and ~6.93 GB/year, against a production DB that syncs as a ~5 GB snapshot. A
    fixed-length prefix does not work either -- on 2026-09-07 the source quotas pushed ``_fill``
    to rank 243 of a 243-item fresh pool, so any constant N can miss the cut.

    **What it rests on**, in the order that would bite:

    1. **Ids are never renumbered.** A migration that rebuilds the table (016 already did:
       RENAME / CREATE / INSERT..SELECT / DROP) preserves ids only because its INSERT lists `id`
       explicitly. One that omits the column silently invalidates every stored watermark, and it
       would contain no UPDATE or DELETE for a grep to find.
    2. **The table is append-only at runtime** -- the only UPDATE/DELETE anywhere are two
       one-time migrations (004 deletes, 017 nulls `cost_usd`); neither runs in production again.

    Neither is a database constraint. That is the price of the 8 bytes.
    """

    ids = [candidate.enrich_eval_id for candidate in candidates if candidate.enrich_eval_id is not None]
    return max(ids) if ids else None


def _ordering_code_digest() -> str:
    """sha256 over the BYTECODE of every function that decides the ordering, first 12 hex.

    Editing any of them reorders every archived run on replay while no recorded field moves.
    `category_multipliers`, `ranking_tiebreakers` and `enrich_watermark` pin the DATA; this pins
    the code.

    **Hashes `__code__.co_code`, deliberately, not the source text.** A first attempt used
    `inspect.getsource` and was withdrawn the same day: it reads the file on DISK rather than the
    loaded code, and the deploy lock (`data/.deploy.lock`) is disjoint from the pipeline lock, so
    a deploy can rewrite `select.py` mid-curate. Measured on this interpreter, that gave a
    plausible-looking digest of an unrelated code block, an empty-file digest, or a
    `tokenize.TokenError` -- which is not an OSError, escaped the fail-soft clause, and would
    have taken curation down to compute an audit field. Bytecode comes from memory: none of those
    three states is reachable.

    **Covers four sites, not one.** `ranking_key` alone is a 4-line return; half of what decides
    the order lives outside it -- `category_multiplier`'s "unlisted means 1.0" default,
    `_primary_category`'s fallbacks (a bad enrich row silently becomes ""), and the
    `weighted_score` formula. A digest over `ranking_key` only would read unchanged across edits
    to any of the other three.

    **Two honest limits.** `co_code` excludes docstrings and comments, so a pure-comment edit no
    longer moves it -- that is the intended trade (the source-text version fired on every comment
    edit, and this docstring alone is ~40 lines of measurement notes). And bytecode is not stable
    across interpreter versions, so the digest can move on a Python upgrade with no code change;
    `python` records the version that produced it, making that case readable rather than
    mysterious. It is a change DETECTOR, not a version: it says two runs were ordered by
    different code, not which came first.
    """

    code = b"".join(
        function.__code__.co_code
        for function in (ranking_key, category_multiplier, _primary_category, weighted_score)
    )
    return hashlib.sha256(code).hexdigest()[:12]


def _ranking_record(weights: Weights, enrich_watermark: int | None) -> dict[str, Any]:
    """What goes into ``curation_runs.weights_json``: the ranking PARAMETERS, not just the weights.

    ``Weights.as_record()`` is everything needed to reproduce a *score*. Since 2026-09-10 the
    *ordering* also depends on ``CATEGORY_MULTIPLIERS`` (see ranking_key), which is not part of
    ``Weights`` and must not be -- it is not a per-dimension weight. Recording it here anyway,
    because otherwise a stored run no longer says which coefficients produced its ordering, and
    two runs from either side of a change to that table read alike. That is the same failure the
    ``SOURCE_QUOTA_SCORE_SEMANTICS`` comment above exists to prevent.

    The other two ordering inputs are recorded for the same reason. ``ranking_tiebreakers`` carries
    the direction, not just the field names: ranking_key sorts ascending on a tuple whose first
    element is negated, so score is descending while the two tie-breakers are ascending, and a
    field list alone does not say that. ``enrich_watermark`` pins the categories -- see
    ``_enrich_watermark``.

    ``ordering_code_sha256`` pins the ORDERING CODE -- see `_ordering_code_digest`.

    Purely additive: nothing in this repo parses ``weights_json`` structurally, so the extra keys
    break no consumer. (Checked 2026-09-10. An earlier version of this line said "every reference
    is an INSERT", which is false -- ``web/routes/curated.py`` does ``SELECT * FROM
    curation_runs`` and several tests select the column. What actually holds is the weaker,
    sufficient claim: every read site was inspected and none destructures this JSON; the route
    takes only ``id`` and ``ruleset_version``.)
    """

    return {
        **weights.as_record(),
        "category_multipliers": dict(CATEGORY_MULTIPLIERS),
        "ranking_tiebreakers": [
            {"field": "weighted_score_x_category_multiplier", "direction": "desc"},
            {"field": "published_at", "direction": "asc"},
            {"field": "item_id", "direction": "asc"},
        ],
        "enrich_watermark": enrich_watermark,
        "ordering_code_sha256": _ordering_code_digest(),
        "python": platform.python_version(),
    }


def curate(
    conn: sqlite3.Connection,
    *,
    ruleset_version: str | None = None,
    weights: Weights | None = None,
    threshold: float | None = None,
    limit: int = DEFAULT_LIMIT,
    freshness_quota: int = DEFAULT_FRESHNESS_QUOTA,
    freshness_floor: float = DEFAULT_FRESHNESS_FLOOR,
    freshness_window_hours: int = DEFAULT_FRESHNESS_WINDOW_HOURS,
    source_quota: SourceQuota | None = DEFAULT_SOURCE_QUOTA,
) -> CurationRun:
    selected_weights = weights or DEFAULT_WEIGHTS
    selected_weights.validate()
    selected_threshold = DEFAULT_THRESHOLD if threshold is None else threshold
    selected_ruleset = ruleset_version or current_version()

    loaded = _load_candidates(conn, selected_weights)
    # Derived from the rows just read, not from a second `SELECT MAX(id)` -- see
    # `_enrich_watermark` for why neither placement of a separate query is correct. Taken over
    # `loaded` rather than the deduplicated list: dedup drops candidates, but their categories
    # were still read by this load and a replay has to see the same rows to reach the same
    # dedup decisions.
    enrich_watermark = _enrich_watermark(loaded)
    candidates = deduplicate_candidates(loaded)
    filtered = [candidate for candidate in candidates if candidate.weighted_score >= selected_threshold]
    filtered.sort(key=ranking_key)
    cutoff = datetime.now(UTC) - timedelta(hours=freshness_window_hours)
    fresh_pool = [
        candidate
        for candidate in candidates
        if candidate.weighted_score >= freshness_floor
        and (published_at := _parse_utc(candidate.published_at))
        and published_at >= cutoff
        and _shanghai_date(candidate.published_at)
    ]
    latest_fresh_date = max((_shanghai_date(candidate.published_at) for candidate in fresh_pool), default=None)
    fresh = [
        candidate
        for candidate in fresh_pool
        if latest_fresh_date and _shanghai_date(candidate.published_at) == latest_fresh_date
    ]
    fresh.sort(key=ranking_key)
    selected = _fill(fresh, filtered, limit, freshness_quota, source_quota)
    shadow_json: str | None = None
    if source_quota is not None:
        baseline = _fill(fresh, filtered, limit, freshness_quota, None)
        baseline_ids = {candidate.item_id for candidate in baseline}
        selected_ids = {candidate.item_id for candidate in selected}
        selected = [
            replace(
                candidate,
                reason={
                    **candidate.reason,
                    "source_quota": {
                        "policy": SOURCE_QUOTA_POLICY,
                        "kind": candidate.kind,
                        # null = no quota configured for this kind / no per-source cap
                        "kind_cap": (
                            _quota_cap(limit, source_quota.kind_caps[candidate.kind])
                            if candidate.kind in source_quota.kind_caps
                            else None
                        ),
                        "source_cap": (
                            _quota_cap(limit, source_quota.per_source) if source_quota.per_source is not None else None
                        ),
                        "baseline": SOURCE_QUOTA_BASELINE,
                        "baseline_selected": candidate.item_id in baseline_ids,
                    },
                },
            )
            for candidate in selected
        ]
        shadow_json = _json(
            {
                "policy": SOURCE_QUOTA_POLICY,
                "baseline": SOURCE_QUOTA_BASELINE,
                "score_semantics": SOURCE_QUOTA_SCORE_SEMANTICS,
                "baseline_only": [
                    {"item_id": candidate.item_id, "raw_weighted_score": candidate.weighted_score}
                    for candidate in baseline
                    if candidate.item_id not in selected_ids
                ],
                "quota_only_count": sum(candidate.item_id not in baseline_ids for candidate in selected),
            }
        )
    run = CurationRun(
        id=_run_id(),
        ruleset_version=selected_ruleset,
        weights=selected_weights,
        threshold=selected_threshold,
        input_eval_ids=[candidate.eval_id for candidate in candidates],
        output_curated_ids=[candidate.item_id for candidate in selected],
    )
    selected = _calibrate_selected_scores(selected)
    conn.execute(
        """
        INSERT INTO curation_runs (
          id, ruleset_version, weights_json, threshold, input_eval_ids,
          output_curated_ids, created_at, shadow_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run.id,
            run.ruleset_version,
            _json(_ranking_record(run.weights, enrich_watermark)),
            run.threshold,
            _json(run.input_eval_ids),
            _json(run.output_curated_ids),
            _utc_now(),
            shadow_json,
        ),
    )
    for rank, candidate in enumerate(selected, start=1):
        conn.execute(
            """
            INSERT INTO curated_items (run_id, item_id, weighted_score, rank, reason_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (run.id, candidate.item_id, candidate.weighted_score, rank, _json(candidate.reason)),
        )
    conn.commit()
    return run
