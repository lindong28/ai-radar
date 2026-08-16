# ADR-041: Version WeChat discovery invariant hardening as schema v8

Status: Accepted

Date: 2026-08-14

Clarifies: ADR-029 through ADR-031 and ADR-040. It does not change the provisional searchbiz or article-URL public-biz verification semantics established by ADR-040.

## Context

The private WeChat discovery database has already migrated to schema v7. Independent schema review then found three contract gaps: a reserved probe could be finalized after its provisional mapping had been superseded, a verified candidate snapshot could be changed after completion through raw SQL, and disabled status used a weaker verification predicate than compare. Changing the v7 DDL in place would leave existing v7 databases on the old contract because `connect()` does not rebuild a database whose `user_version` already equals the current version.

The live v7 database currently contains one `response_invalid` resolution, two failed `predates_resolution` probes, and no candidates. That sparse live instance is not sufficient evidence that a migration preserves candidate-bearing success and contradictory relationship cases.

## Decision

Publish the invariant hardening as schema v8 with a transactional v7-to-v8 migration. Do not alter the meaning of an already published v7 schema in place and do not restore the pre-v7 backup merely to reuse the v7 version number.

Schema v8 rejects completion of an article-URL-biz-verified success unless its provisional resolution remains neither invalidated nor superseded. A completed verified candidate snapshot is immutable: raw insertion, update, and deletion are rejected after completion. Migration-only `predates_v7_verification` cannot be written for a new provisional relation. Disabled status and compare consume the same candidate URL identity and active-resolution conditions; a stored marker or candidate count alone is not verification.

Schema evolution is an explicit state-changing operation. `status`, `compare`, and store read APIs open the private database in read-only mode and reject older schemas without changing them; `wechat-discovery migrate` is the sole operator-facing migration entrypoint and reports the before/after schema version. Write commands may initialize or migrate their own private shadow state as part of an explicitly requested state-changing action.

The provenance-field reductions proposed during v7 review are not part of this decision. They are a separate contract simplification and would broaden this repair beyond the confirmed invariant gaps.

## Release gate

Schema v8 is not considered published until all of the following pass:

- a fresh v7 fixture migrates to v8;
- a persistent copy of the exact pre-v7 backup completes the full v6-to-v7-to-v8 chain with preserved row summaries, integrity, and foreign keys;
- positive and negative fixtures cover candidate-bearing verified success, superseded relations, post-completion candidate insert/update/delete, and attempts to write migration-only evidence for a new provisional relation;
- the real private database is backed up at v7, then its before/after version, row summaries, integrity, and foreign keys are checked.

Any failure rolls the transaction back and leaves the source database at its prior version. The exact v6 backup remains a disaster-recovery artifact, not a normal downgrade path.

## Rejected alternatives

Keeping `user_version=7` while changing source DDL was rejected because fresh and existing v7 databases would silently implement different contracts. Restoring the exact v6 backup and replaying a revised v7 was rejected because it rewrites an already materialized schema history and cannot account for other v7 data. Fixing only CLI or application validation was rejected because raw writers would still be able to persist impossible states.

## Scope and unverified boundaries

This decision covers only this repository's private official WeChat discovery ledger and its local migrations and consumers. It does not cover Mp2RSS, WeRead evidence, or databases owned by other repositories. The current v8 instance now contains one live provisional resolution, but still has no live article-list probe or candidate-bearing real instance; those runtime questions remain for the cooled-down one-shot probe and explicit comparison.
