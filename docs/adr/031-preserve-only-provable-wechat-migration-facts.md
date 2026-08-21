# ADR-031: Preserve only provable facts during WeChat ledger migration

Status: Accepted; deprecated — the backend `appmsgpublish` family is unavailable at the platform level, see ADR-061

Date: 2026-08-13

Clarifies: ADR-028 through ADR-030 for v1-v5 history migrated to schema v6.

## Context

Schema v4 could prove that a resolved identity was consumed by a particular probe, but it did not persist the observed public `biz` independently from the configured value. Treating that relation as a v6 verified resolution would invent provenance. Rejecting the whole database would instead discard a recoverable probe target and outcome.

Weak or manually produced v5 databases can contain a different class of problem: a verified probe may reference an identity that finished after the probe began or had already been superseded, and supersession edges may contradict the normal writer's account, ordering, and single-consumer lifecycle. Repairing those contradictions would require guessing which timestamp or relationship was true.

## Decision

Migration preserves only facts supported by the source ledger. A valid v4 consumed relation whose public-biz observation predates persistence is migrated as `predates_resolution`: the probe keeps its target in `legacy_target_account_name` and `legacy_target_public_biz`, while the resolution remains as invalidated history and cannot support active mapping or comparison. Migration never upgrades `predates_persistence` to `recorded`.

A v5 `verified_resolution` relation is accepted only when the referenced resolution was resolved with recorded public-biz provenance before the probe reservation, matches the probe target, and was not already superseded or invalidated before use. Supersession is accepted only between resolved records for the same configured account and public `biz`, from an older record to a later record, when the source resolution has no consuming probe. Contradictory, cyclic, reversed, cross-account, or otherwise unrecoverable relationships make the whole migration roll back without changing the source database.

## Alternatives rejected

- Reject every legacy consumed relation. This prevents a valid v4 database from upgrading even though its probe target and result remain recoverable.
- Preserve the v4 relation as verified by labeling its public-biz provenance `recorded`. The source schema did not record that observation, so this would manufacture identity evidence.
- Normalize contradictory v5 timestamps or relationships. The ledger does not contain enough independent evidence to choose a truthful repair, and the result could become active or comparable.

## Scope and verification boundary

This decision applies only to private local WeChat discovery ledgers from schema v1-v5 being migrated to schema v6. It does not change the v6 runtime writer and does not establish live WeChat backend compatibility, session lifetime, account coverage, or Mp2RSS replacement readiness.

The decision was reviewed against three distinguishing counterexamples: a valid v4 consumed relation currently rejected, a v5 probe that references an identity resolved only after the probe, and a v5 probe that references a superseded identity. Implementation must add deterministic regression coverage for these cases and for invalid supersession topology, then rerun the complete v1-v6 migration matrix. A successful authorized live resolution and later probe remain separate, rate-limited evidence gates.

## Consequences

Legacy request history remains inspectable without being promoted into verified identity evidence. Recoverable v4 databases can upgrade, while contradictory v5 databases fail closed and remain byte-for-byte available for diagnosis or a later explicit repair. This is intentionally asymmetric: losing comparison eligibility is safer than silently creating a trusted identity relationship that never existed.
