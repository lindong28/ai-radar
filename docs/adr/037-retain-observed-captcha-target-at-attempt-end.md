# ADR-037: Retain an observed captcha target at canary attempt end

Status: Accepted; superseded by ADR-038 for newly generated evidence. Versions 1 through 6 remain frozen historical observations. Deprecated — the WeRead canary line stopped together with the Mp2RSS replacement plan it explored, see ADR-061.

Date: 2026-08-13

Supersedes: ADR-036 for newly generated evidence. Versions 1 through 5 remain frozen historical observations.

## Context

Schema version 5 correctly classifies a public WeChat captcha as an observed page, but the adapter closes that page before the CLI asks the operator to handle it. The instruction is therefore not actionable. The same schema also permits some contradictory failure-point, omission, and reader-state relationships, while the CLI can hide a known identity-mismatch dimension or authorize a retry when another request has unknown dispatch.

## Decision

All newly generated evidence uses schema version 6. A public identity ledger row records the target state at canary attempt end as one of `not_created`, `closed_before_attempt_end`, `retained_at_attempt_end`, or `close_not_confirmed_at_attempt_end`. Only an observed captcha may be deliberately retained. `close_not_confirmed_at_attempt_end` means that the canary attempted to close an already-created target but CDP did not confirm the close; consumers must not interpret it as proof that the tab still exists. These fields describe capture-time facts and do not claim that a target will remain visible or operable after the attempt.

The adapter does not close the one target on which it observed a captcha. The CLI conditionally asks the operator to inspect that retained target in the dedicated visible Chrome session. If it is absent or unusable, or after the operator finishes, abandons the attempt, or decides not to rerun, the operator closes the tab. A later bounded canary creates its own target and does not reuse the retained tab. Live visibility, operability, captcha completion, and post-captcha identity fields remain unverified until a user confirms them.

The final validator closes failure points by operation, dispatch evidence, and outcome; requires each public failure point to match the candidate observation; rejects contradictory omission fields and unchecked usable shelf entries; and records observation timestamps after the adapter returns. Unknown dispatch takes precedence over any retry instruction. The CLI projects public failure layers, exact identity-mismatch dimensions, and human-readable omission and shelf-entry reasons.

## Consequences

The canary no longer destroys the only observed captcha page before a possible human handoff, while its evidence avoids promising that the page will persist. Captcha handling may leave one dedicated Chrome tab for the operator to close. Existing version 5 and older artifacts remain readable and immutable. A visible, logged-in version 6 run is still required to establish whether the retained target is actionable and whether the WeRead route is feasible; an Mp2RSS shadow comparison remains required before production integration or cutover.
