# ADR-030: Remove derived WeChat discovery fields and make public-biz names explicit

Status: Accepted; deprecated — the backend `appmsgpublish` family is unavailable at the platform level, see ADR-061

Date: 2026-08-13

Supersedes: ADR-029 only where it retains duplicated target, observed-biz, kind, change-basis, and successful probe subtype fields.

## Context

Schema v5 made the resolution-to-probe edge and article snapshots authoritative, but it still persisted values that were fully derivable from those records. A verified probe repeated its target name and public `biz`; each candidate repeated the same public `biz`; every probe repeated a fixed `kind` and change basis; and two successful outcomes encoded whether a URL was new even though immutable snapshots already determine that fact. The human-edited configuration also called the public article identifier `biz`, beside a separate backend `fakeid`, so a reader could not infer the distinction from field names alone.

The private database had already reached v5 before stricter resolved-identity and invalidation checks were added. Reusing version 5 would therefore give fresh and existing databases different contracts.

## Decision

Schema v6 persists one successful probe terminal outcome, `success`; specific failure outcomes remain distinct. Whether a successful snapshot contains a URL not seen in earlier snapshots is derived at read time and returned as `new_candidate_count`, never stored as a success subtype.

A verified probe derives its account name and public `biz` from its referenced resolution. Only `predates_resolution` rows retain `legacy_target_account_name` and `legacy_target_public_biz`, because no resolution exists from which those values could be recovered. Candidate rows store `probe_attempt_id`, URL, title, author, and publication time; their account identity comes from the parent probe. The fixed probe kind and fixed URL-set change basis are removed from storage and projected by the model when needed.

A resolution stores the configured public `biz` once. `public_biz_match_origin` records whether the backend response was actually checked against it without copying the same value into an observed column. Configuration version 3 renames `biz` to `public_biz` and `identity.observed_biz` to `identity.observed_public_biz`; version 2 is rejected instead of being silently reinterpreted.

All newly written timestamps must be timezone-aware and are stored in UTC before SQLite text ordering. The v5-to-v6 migration parses and normalizes every historical timestamp, validates recoverable resolved identity and non-empty invalidation reasons, maps both former successful outcomes to `success`, and rolls back in full when evidence is missing or contradictory. An exact pre-v6 SQLite backup and a migrated copy must be verified before migrating the private live shadow database.

## Consequences

The persisted ledgers have one source for each current fact, while legacy-only fields state why they cannot be derived. Status and compare still expose concrete failure reasons; a successful probe reports returned and newly observed URL counts without embedding that derived classification in the immutable request outcome.

Schema v6 and configuration v3 are separate versioned contracts delivered together. The migration and naming cleanup improve recoverability and reader interpretation, but they do not establish live WeChat backend compatibility or Mp2RSS coverage. Those claims still require a successful authorized identity resolution, a later successful probe, and explicit windowed comparison.
