# ADR-029: Make WeChat discovery ledgers single-source and crash-safe

Status: Accepted; duplicated fields superseded by ADR-030; deprecated — the backend `appmsgpublish` family is unavailable at the platform level, see ADR-061

Date: 2026-08-13

## Context

ADR-028 introduced an identity-resolution ledger before any manual article probe. Its initial schema stored the resolution-to-probe consumption relationship in both ledgers, copied configured identity fields into columns that looked observed, and populated `finished_at` before a backend request had a terminal outcome. The attempt snapshot was also duplicated into a mutable global candidate table. Independent schema review showed that these shapes could produce contradictory status and comparison results even though each row passed its local checks.

The private production database already reached schema v4, so the correction must be an explicit migration rather than an in-place reinterpretation. It currently contains two legacy probe failures and no resolution rows or candidates.

## Decision

Schema v5 uses `discovery_attempts.identity_resolution_id` as the only persisted consumption edge. The nullable column keeps legacy attempts representable; a partial unique index makes every non-null resolution usable by at most one probe. The resolution ledger no longer stores a consumer or a duplicated consumption timestamp. It stores configured and observed account identity under distinct names, and a successful resolution must contain a normalized matching name, matching public `biz`, and non-empty `fakeid`.

A reserved resolution or probe has `finished_at=NULL`; every terminal outcome has a real non-null completion time. Resolution supersession stores only the successor reference, and its time is derived from the successor's terminal record. Invalidations retain their occurrence time and reason because neither is derivable from another persisted event.

The probe row owns its single target account and public `biz`; the redundant single-account result row is removed. The immutable per-attempt article snapshot remains the recovery and comparison evidence. The mutable global candidate table and `new_to_shadow_state` copy are removed; current URL sets, first/last observation, and whether an article was new for an attempt are derived from snapshots.

The v4-to-v5 migration validates every existing bidirectional consumption edge before discarding the duplicate side: both endpoints must exist, point to each other, and identify the same account. Duplicate consumers or contradictory edges abort and roll back the entire migration. A v4 successful resolution without independently persisted observed-`biz` provenance must become unavailable for future probes rather than copying configured `biz` into an observed field. Before migrating the live private database, preserve an exact SQLite backup under the gitignored recovery directory.

New probes can only be written through `reserve_probe` followed by `complete_probe`; the generic `record_attempt` writer is removed. Candidate URLs are revalidated at the store boundary so test doubles and future callers cannot bypass the URL `__biz` identity check.

## Consequences

Status, probe reservation, and comparison consume the same active-mapping predicate. A crash after reservation remains auditable as an unknown outcome without fabricating a completion time, and a second probe cannot reuse a mapping even under concurrent writers. Historical v3-and-earlier attempts remain readable with a null resolution reference and cannot form verified coverage evidence.

This decision does not remove the two successful probe outcome variants. They remain an explicit request-outcome classification even though the candidate snapshots can help derive them; no claim that all schema duplication is eliminated should rely on this ADR.

Live `searchbiz` and article-list behavior remain unverified until the authorized cooldown gates allow one real resolution and one later real probe. Schema correctness does not establish Mp2RSS replacement coverage.
