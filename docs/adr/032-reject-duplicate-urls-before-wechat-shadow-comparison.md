# ADR-032: Reject duplicate URLs before WeChat shadow comparison

Status: Accepted

Date: 2026-08-13

Clarifies: ADR-026 and ADR-030 candidate snapshot semantics.

## Context

The article parser formerly deduplicated a backend response by URL before the immutable candidate snapshot was written. Window comparison uses the number of snapshot candidates and the requested page size to decide whether a full response may have been truncated before reaching the comparison window. A five-slot response containing one duplicate therefore became a four-item snapshot and could be mistaken for a non-truncated page, allowing insufficient evidence to produce a coverage conclusion.

No successful live article-list response has yet established whether duplicate URLs are a legitimate platform behavior. Persisting both raw slot count and a distinct URL snapshot would require a new versioned field, provenance for older attempts, another database migration, and new reader semantics before the external behavior is known.

## Decision

An `appmsgpublish` response containing the same normalized article URL more than once is invalid for shadow evidence. Parsing fails explicitly, the probe records `response_invalid`, and no candidate snapshot is written. Duplicate URLs across different attempts remain valid historical observations; the rejection applies only within one backend response.

The distinct URL snapshot remains the only candidate authority. Raw response-slot count will not become a persisted contract unless a future live response proves that duplicates are legitimate and useful enough to justify separate provenance.

## Alternatives rejected

- Add schema v7 immediately with raw returned-slot count beside the distinct snapshot. This can preserve more responses, but it expands migrations and consumer contracts without live evidence that the new fact is needed or stable.
- Treat every response with fewer distinct URLs than the requested page size as truncated. That would make ordinary short pages permanently incomparable.
- Persist duplicate candidate rows. This conflicts with the URL-set snapshot and makes counts depend on transport duplication rather than article identity.

## Scope and verification boundary

This decision applies only to exact normalized URL duplication within one `appmsgpublish` response. It does not define whether WeChat may legitimately return duplicates, does not affect `searchbiz`, and does not affect the same URL appearing in later attempts.

The implementation must prove that a duplicate response becomes `response_invalid`, that no candidate snapshot survives, and that the former full-page comparison shape cannot produce a coverage result. A future authorized live duplicate response is explicit evidence to revisit this decision rather than silently relaxing it.

## Consequences

Comparison cannot confuse a deduplicated transport response with a genuinely short page. The conservative cost is one failed probe if duplicates are legitimate; that failure is visible and recoverable, while a false coverage conclusion would be silent and could justify an unsafe Mp2RSS cutover.
