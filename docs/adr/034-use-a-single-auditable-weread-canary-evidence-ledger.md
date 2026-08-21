# ADR-034: Use a single auditable WeRead canary evidence ledger

Status: Accepted; deprecated — the WeRead canary line stopped together with the Mp2RSS replacement plan it explored, see ADR-061.

Date: 2026-08-13

Supersedes: ADR-033 for newly generated evidence. Version 1 and version 2 artifacts remain frozen historical observations.

Superseded by: ADR-035 for newly generated evidence. Version 3 artifacts remain frozen historical observations.

## Context

The first two real unauthenticated canary artifacts proved that request failures are not equivalent to an empty shelf, but review of the complete evidence surface found that version 2 still cannot support its intended audit and integration decisions. It counts an adapter call as a sent request before dispatch is known, collapses CDP, HTTP, WeRead API, and response-shape failures, stores several array-derived counts that can drift, separates candidate identity evidence from the candidates it describes, and lets the CLI hard-code replacement readiness rather than project it from the artifact.

The browser boundary also discarded distinctions that can change operator action. An HTTP failure could be converted into an empty successful shelf, and a malformed target shelf entry could be filtered out and reported as absent, encouraging an unnecessary shelf mutation.

Versions 1 and 2 already have immutable real instances. Repairing either shape in place would make one schema version describe different contracts.

## Decision

All newly generated evidence uses schema version 3 and the artifact kind `weread_article_discovery_canary_evidence`. Versions 1 and 2 are never rewritten or renamed. Consumers dispatch explicitly on `schemaVersion`, identify a legitimate historical absence as not recorded by that version, reject a malformed known version, and fail closed on an unknown future version.

Version 3 has one final `validateAndBuildEvidence` authority through which every success and failure path passes. It validates required account identity, closed state enums, attempt times, request-plan limits, request-ledger topology, shelf/discovery relationships, candidate uniqueness, candidate observation states, and the aggregate state before an artifact can be returned or written.

`requestCounts` is replaced by a bounded `requestPlan` and an immutable `requestLedger`. The plan names each operation, HTTP method or public navigation, origin-relative endpoint template, browser credential mode, and authorized attempt limit. It records session authentication as `unassessed` unless the canary actually measures it. The ledger records one entry per attempted operation and never calls an attempt a sent request.

The request-state matrix is closed:

- `dispatchEvidence=response_observed` permits only `success`, `http_failed`, `weread_api_failed`, or `response_invalid`.
- `dispatchEvidence=page_observed` permits only `success` or `response_invalid`.
- `dispatchEvidence=unknown` permits only `client_failure`.
- A WeRead fetch with an observed response carries a safe integer HTTP status. `http_failed` requires a non-2xx status; the other observed-response outcomes require 2xx.
- `wereadApiErrCodeObservation=observed|absent_in_response` is legal only for `weread_api_failed`; an observed code must be a safe integer. Other outcomes use `not_applicable` and carry no code.
- No ledger row represents an operation that was not attempted. Plan limits and ledger cardinality provide the unattempted boundary.

The shelf parser preserves HTTP status and never defaults a missing `books` field to an empty array. A target WeRead book ID whose entry is malformed becomes `present_but_unusable` with a closed rejection reason. `absent` is legal only after a structurally valid successful shelf response proves that the target ID is missing.

The candidate array is the only home for discovered articles and identity-observation state. Each candidate uses self-describing WeRead/WeChat names, one-based ordinal names with returned-page or publish-group scope, and a `publishedAtSource` enum distinguishing article and publish-group timestamps. Each candidate embeds an identity observation whose state is `not_checked_by_canary_budget`, `verified`, `unverified`, or `identity_mismatch`. A candidate beyond the canary budget explicitly remains the responsibility of `future_production_integration` and must be verified before candidate ingestion. Invalid short-link tokens become omissions rather than candidates.

Version 3 removes `candidateCount`, `omittedEntryCount`, `verification`, the separate `identityEvidence` array, and reconstructed `observedIdentityUrl`. The CLI derives counts from candidates and omissions, and reconstructs links only in a presentation that needs them.

The overall state is explicitly scoped to the returned-page canary result, using names such as `returned_page_candidate_identities_fully_verified` and `returned_page_identity_mismatch_observed`. It cannot be interpreted as historical coverage or replacement readiness. A required `replacementAssessment` remains `not_validated` with reason `mp2rss_shadow_comparison_not_performed` until a separate shadow comparison supplies evidence; the CLI projects this field rather than inventing the result.

Side-effect claims are an implementation contract, not an external-state observation. Version 3 records a `sideEffectAssessment` with `basis=implementation_contract`, `externalObservationState=not_measured`, and a closed set of operations declared not performed. The CLI preserves that qualification.

The CLI consumption contract is fixed:

- Candidate and identity lines derive only from `discovery.state` and candidate observation states. If discovery was not attempted, the line says `NOT_MEASURED` rather than zero.
- Returned-page coverage derives only from `discovery.coverageState` and omissions.
- Request attempts group request-ledger rows by operation, and dispatch evidence is shown separately; neither is labelled as sent.
- Shelf-request status derives only from the unique shelf ledger row. Shelf-entry status is read only after an applicable shelf outcome.
- Replacement readiness derives only from `replacementAssessment`.

The artifact payload is the authority for kind, version, and attempt time. Output filenames are not contract fields. New operational filenames do not encode a schema version or date; existing version 1 and version 2 filenames remain unchanged.

## Alternatives rejected

- Extend version 2 in place. Real version 2 evidence already exists, so this would make one version number ambiguous.
- Add only the missing diagnostic fields while retaining counts and a separate identity-evidence array. This would preserve multiple writable homes and the current split success/failure builders.
- Treat all adapter failures as one conservative failure. It fails closed for replacement, but it cannot support request-budget audit or correct operator action and can mistake HTTP failure for an absent shelf entry.
- Build a generic request-observability framework. Version 3 instead records only the bounded operations and distinctions required by this canary's audit and integration decisions.

## Scope and verification boundary

This decision applies only to the default-off, local, single-account, read-only WeRead canary and its JSON/CLI evidence. It does not write the v6 WeChat discovery ledger, change the production pipeline, mutate a WeRead shelf, or switch production away from Mp2RSS.

Verification must include negative paths through the actual browser adapter and runner, not fake runner return values alone: HTTP failure, WeRead API failure with and without an integer code, invalid response shape, CDP/client failure with unknown dispatch, malformed target shelf entry, invalid article token, and a candidate skipped by the observation budget. It must also prove valid version 1 and version 2 rendering, rejection of damaged and unknown-version artifacts, writer-produced positive and failure CLI summaries, immutable old artifacts, and the closed request/state matrix.

A real logged-in positive artifact is still required before the canary can validate the discovery route. No offline test or schema review can substitute for that platform observation.

## Consequences

Version 3 is a larger local refactor than adding fields to version 2, but it reduces the number of authorities and makes request risk, response failure, candidate verification, and replacement readiness independently auditable. The remaining live uncertainty is explicit and cannot be mistaken for an empty result, a sent request, or a validated replacement.
