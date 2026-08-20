# ADR-040: Verify provisional searchbiz mappings with returned article URL public biz

Status: Accepted; deprecated — the backend `appmsgpublish` family is unavailable at the platform level, see ADR-061 (the supersede of ADR-028 below still stands as a historical relation)

Date: 2026-08-14

Supersedes: ADR-028's claim that `/cgi-bin/searchbiz` can verify a mapping by returning both the configured public `biz` and a private `fakeid`. It preserves ADR-025's temporary request cadence, ADR-026's explicit comparison boundary, and ADR-029 through ADR-031's single-authority and evidence-preserving migration rules.

## Context

The first authorized live `searchbiz` request ended as `response_invalid`. The raw private response was intentionally not persisted, so that result alone does not prove which field was absent. Independent source inspection of an active implementation that calls the same endpoint showed that its search result consumes `fakeid`, `nickname`, `alias`, avatar, and service type, but no public `biz`. Together these observations invalidate the old parser contract strongly enough to redesign it, but they do not establish a live successful response shape.

A normalized display-name match is not account identity. It is only enough to select one private `fakeid` for a single bounded probe. Public identity can be observed later in canonical returned article URLs when they contain one non-empty `__biz` value. The shadow ledger must preserve whether that observation happened and why it did not happen without inventing a second proof table or copying a derived verified boolean.

## Decision

`searchbiz` parsing requires an explicit complete result count and candidate rows with a non-empty nickname and private `fakeid`. Exactly one normalized configured-name match produces a provisional mapping. Zero matches are `no_match`; more than one matching row is `ambiguous_match`, including duplicate rows. The private `fakeid` is redacted from representations and human-facing output. Search results never synthesize the configured public `biz` as an observed value.

The resolution ledger distinguishes new provisional matches from historical v6 name-and-biz matches. Only a new, unconsumed, non-invalidated provisional match can reserve one probe. Reservation consumes it once regardless of the terminal request outcome. Historical v6 matches remain auditable but cannot be selected for a new probe because their raw search result did not prove uniqueness under the new name-only contract.

The probe ledger is the single persisted authority for target identity evidence. Its `target_identity_evidence` value is one of:

- `pending` for a reserved probe;
- `not_observed` when the request ends before usable article identity is observed;
- `empty_article_list` for a valid empty returned page;
- `article_url_public_biz_unavailable` when a canonical returned URL lacks exactly one non-empty `__biz`;
- `article_url_public_biz_verified` when at least one returned article exists and every canonical URL has exactly one `__biz` equal to the configured public `biz`;
- `article_url_public_biz_mismatch` when a returned URL exposes one different `__biz`;
- `predates_v7_verification` for a migrated historical success whose original full response is unavailable.

The request outcome and target identity evidence are cross-checked. A new `success` requires non-empty candidates and `article_url_public_biz_verified`. Empty or unavailable identity ends as `identity_unverified` with no stored candidates. A mismatching public `biz` ends as `identity_mismatch`, stores no candidates, and invalidates the provisional mapping without claiming which account owns the private `fakeid`. Authentication, rate limit, request, and response-shape failures store `not_observed`. The parser rejects malformed publish groups instead of silently skipping them, so a new verified success proves the complete returned page passed the URL identity rule.

Comparison accepts only a successful probe with `article_url_public_biz_verified`, a non-empty candidate snapshot, and the valid provisional resolution relation. It never treats the provisional search result itself as verified identity.

## Migration

Schema v6 `resolved` rows migrate as historical name-and-biz matches, not new provisional matches. Their linked `verified_resolution` probe edges remain historical relations. Every v6 success, `identity_unverified`, or `identity_mismatch` probe migrates with `target_identity_evidence=predates_v7_verification` and remains non-comparable, even when its stored candidate URLs all contain the configured public `biz`: the old parser could silently skip malformed response groups, so the surviving snapshot cannot prove the full response satisfied the v7 rule. Authentication, rate-limit, request, and response-shape failures migrate with `not_observed`; legacy `predates_resolution` targets remain explicit.

Migration is transactional and preserves IDs, timestamps, target snapshots, outcomes, invalidation and supersession relationships. Contradictory or unrecoverable relationships roll back rather than being repaired by inference. The live v6 database currently has one `response_invalid` resolution, two legacy failed probes, and no candidates, so its expected v7 result has the same three ledger rows and no verified target evidence.

## Rejected alternatives

An independent one-to-one verification table was rejected because the probe row, candidate snapshot, and completion time already own the verification event; another table would duplicate the relationship and add trigger, migration, and recovery surfaces.

A narrow `identity_verification_origin` field was rejected because both a valid empty page and a URL without usable `__biz` would collapse into `identity_unverified`, leaving status and CLI unable to reconstruct the distinct next actions. A separate reason column was also rejected because state and reason would become two independently writable versions of one evidence classification.

Backfilling v7 verification from v6 candidate snapshots was rejected because the old parser did not prove that every original response item survived into that snapshot.

## Scope and unverified boundaries

This decision applies only to the default-disabled private shadow resolver, probe ledger, status, CLI, and comparison gate. It does not increase request frequency, enable scheduling, write production items, expose private `fakeid`, disable `wx_mp2rss`, or claim Mp2RSS replacement readiness.

A later authorized live `searchbiz` produced one provisional unique normalized-name match for “歸藏的AI工具箱” under this contract. That single positive establishes neither public-biz identity nor endpoint stability, and no successful live `appmsgpublish` response has yet established empty-page frequency, missing-`__biz` frequency, repost behavior, or multi-day coverage. A reposted article may safely produce a false-negative mismatch because its URL can identify another public account. Empty pages cannot verify identity. Those remaining live questions require the cooled-down one-shot probe followed by explicit same-window Mp2RSS comparison.
