# ADR-036: Preserve public-page observation outcomes in the WeRead canary

Status: Accepted; deprecated — the WeRead canary line stopped together with the Mp2RSS replacement plan it explored, see ADR-061

Date: 2026-08-13

Supersedes: ADR-035 for newly generated evidence. Versions 1 through 4 remain frozen historical observations.

Superseded by: ADR-037 for newly generated evidence.

## Context

The version 4 adapter correctly separated WeRead HTTP, API, and response-shape failures, but its public WeChat identity path threw ordinary errors for target creation, CDP evaluation, boundary decoding, observed captcha, and identity timeout. The runner then represented every case as unknown dispatch. An observed captcha could therefore be shown as a Chrome/CDP failure, while an incomplete observed page could prevent any evidence artifact from being written. The validator also treated ledger order as authoritative without enforcing it and accepted contradictory returned-page coordinates.

## Decision

All newly generated evidence uses schema version 5. The public-page adapter returns an observation and request evidence together. Target creation, CDP evaluation, boundary decoding, observed captcha, identity timeout, and observed identity-shape failures have distinct closed failure points. Captcha, timeout, and incomplete identity fields use `page_observed`; only failures that do not establish a page observation use unknown dispatch.

The runner propagates adapter evidence instead of reclassifying it. The final validator requires ledger stages to remain ordered as shelf, article list, then public identity; requires candidate and omission coordinates to be mutually consistent; and closes reader failure state to its corresponding failure point. The CLI shows operation-specific article-list evidence, names the exact production mutations that the implementation declares absent, and provides a first action for the observed failure layer.

The existing source digest remains a capture-time implementation fingerprint. Schema version 5 does not introduce a source archive, Git requirement, or additional production integration because those would raise the local canary's auditability tier beyond the task's existing requirements.

## Consequences

A consumer can distinguish a page it actually observed from a client boundary it could not observe, so retry and captcha handling no longer use the same instruction. Existing version 4 artifacts remain readable and immutable. A logged-in positive version 5 artifact is still required before the WeRead route can be considered feasible, and a separate Mp2RSS shadow comparison remains required before any production cutover.
