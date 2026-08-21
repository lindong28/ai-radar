# ADR-038: Observe WeRead dynamic header presence without replay

Status: Accepted; deprecated — the WeRead canary line stopped together with the Mp2RSS replacement plan it explored, see ADR-061

Date: 2026-08-13

Supersedes: ADR-037 for newly generated evidence. Versions 1 through 6 remain frozen historical observations.

## Context

An anonymous page-context request to `/web/mp/articles` returns a WeRead API failure, while current external implementations disagree about whether the endpoint is retired or instead requires browser-generated `x-wrpa-0` and `x-wr-ticket` headers. Replaying captured header values would provide a stronger endpoint test, but it would also add a credential-handling and credential-rotation risk that the version 6 canary does not cover. Avoiding Network observation altogether would leave the two explanations indistinguishable.

## Decision

All newly generated evidence uses schema version 7. The canary passively observes only the existing, bounded `weread_article_list` request that it already makes from the dedicated headed Chrome. It neither adds a request nor replays a captured request. Raw request header values must not enter the evidence bundle, human summary, errors, or logs.

Version 7 records a dynamic-header observation beside the article-list attempt. Its states distinguish whether Network observation was armed, whether the exact WeRead origin, article-list path, and configured `bookId` request was matched, whether redirect-leg attribution was ambiguous, and whether `Network.requestWillBeSentExtraInfo` was observed for that request. Only an ExtraInfo event that can be attributed to the single matching request leg may produce case-insensitive presence booleans for the `x-wrpa-0` and `x-wr-ticket` header names. A missing event and a redirect-chain attribution ambiguity are both distinct from an observed event whose header set lacks either name.

The evidence scope is one local account and one read-only feasibility attempt. Absence of either header describes only the observed page-context request and does not prove that the endpoint is retired. The canary continues to make no shelf change, production candidate write, scheduler change, or `wx_mp2rss` change. Versions 1 through 6 remain readable through their frozen consumer paths.

## Consequences

The next visible logged-in run can distinguish listener failure, no matching request, multiple matching requests, ambiguous redirect-leg attribution, no ExtraInfo event, and observed header-name absence without persisting a credential value. The implementation needs a version 7 writer, validator, consumer projection, and adversarial tests showing that synthetic secret values never survive JSON serialization or human formatting.

Subsequent observation on 2026-08-14 produced the first real logged-in version 7 artifact from a user-confirmed visible Chrome window. The shelf request received an observed HTTP 200 success, but the configured book ID was absent from the shelf, so the canary stopped with `blocked_no_shelf_entry`; it did not attempt the article-list request and recorded dynamic-header observation as `not_attempted`. This closes the live shelf-boundary gap without establishing real Chrome event delivery for the article-list request, header presence, endpoint success, pagination, account coverage, or replacement readiness. Adding the target to the external shelf remains a separately authorized mutation rather than part of this read-only decision.
