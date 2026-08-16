# ADR-033: Version WeRead canary shelf-request evidence

Status: Accepted

Date: 2026-08-13

## Context

The first real unauthenticated WeRead canary produced a version 1 local evidence bundle with `overallState=shelf_request_failed`, but the runner discarded the platform error code already distinguished by the shelf-response parser. The artifact could prove that the request failed rather than returning an empty shelf, yet it could not preserve whether the response supplied a safe integer diagnostic code.

That version 1 artifact is already an immutable observation. Adding fields while retaining `schemaVersion=1` would make the same version number describe different shapes and leave consumers unable to determine which contract produced an artifact.

## Decision

All newly generated WeRead canary evidence uses `schemaVersion=2`. Version 2 adds a required `shelfRequest` object whose `state` is `success` or `failed`. A conditional integer `platformErrorCode` is present only when the actual shelf response supplies one.

The CLI displays the shelf-request state and the conditional platform error code separately from the overall canary result. It does not interpret an error code as authentication, rate limiting, or any other cause without separate evidence.

The existing version 1 artifact remains unchanged. No migration rewrites it, and no version 1 reader may assume the version 2 shape.

## Alternatives rejected

- Add the field to version 1 in place. This would make a released schema version ambiguous.
- Keep only `overallState=shelf_request_failed`. This preserves failure but discards an observed diagnostic distinction at the runner boundary.
- Put the code into a free-form error message. This would mix a machine-observed integer with human interpretation and make exact consumption brittle.

## Scope and verification boundary

This decision applies only to the default-off, local, single-account, read-only WeRead canary evidence artifact and its CLI summary. It does not change the production pipeline, the v6 WeChat discovery ledger, Mp2RSS authority, request budgets, or WeRead shelf contents.

Verification must prove that a real or simulated platform shelf failure with an integer code produces a version 2 artifact containing that exact integer, while a failure without such a code omits the field rather than inventing a sentinel. It must also prove that a successful empty shelf remains distinct from a failed request and that the existing version 1 artifact is not modified.

## Consequences

New evidence preserves the diagnostic fact available at the platform boundary without claiming a cause that has not been verified. Consumers must branch on `schemaVersion`; future additions that change the artifact shape require another explicit version rather than silently extending version 2.
