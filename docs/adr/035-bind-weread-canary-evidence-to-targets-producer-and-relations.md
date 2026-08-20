# ADR-035: Bind WeRead canary evidence to request targets, producer source, and verified relations

Status: Accepted; superseded by ADR-036 for newly generated evidence; deprecated — the WeRead canary line stopped together with the Mp2RSS replacement plan it explored, see ADR-061

Date: 2026-08-13

Supersedes: ADR-034 for newly generated evidence. Versions 1 through 3 remain frozen historical observations.

## Context

The first real version 3 artifact correctly distinguished an observed HTTP response and WeRead API failure from an empty shelf, but independent schema and CLI review found that a copied artifact still could not answer several audit questions without source inspection. Relative WeRead paths did not state their origin, public-navigation ledger rows did not identify the candidate URL they attempted, and an implementation-contract side-effect statement was not bound to the source that produced it.

The version 3 validator also trusted identity-match booleans instead of recomputing them from configured and observed values, accepted article-list and public-page attempts while the reader context was not ready, and did not require identity observation time to fall inside the canary attempt. These gaps could turn contradictory data into a fully verified result. Version 3 already has an immutable real artifact, so the contract cannot be repaired in place.

## Decision

All newly generated evidence uses schema version 4. Versions 1 through 3 remain byte-frozen and are consumed only through their exact version-specific validators; missing required fields, added fields, and unknown future versions fail closed.

Version 4 removes the fixed `mode` label and per-operation `attemptOrdinal`, leaving `artifactKind`, the account shape, the side-effect assessment, and ledger array order as their single authorities. HTTP request-plan entries include `targetOrigin=https://weread.qq.com`, and transport is named by `actionKind=http_get|browser_navigation`. Every public-navigation ledger row contains the exact `candidateWechatArticleShortUrl` it attempted, which the final validator must match to the corresponding embedded candidate observation.

Every artifact includes `producer.implementation` and a SHA-256 digest over the canary source files used by that execution. This binds the immutable runtime observation and its implementation-contract side-effect declaration to one exact source snapshot, including when the source is not committed.

Client and response failures carry a closed `failurePoint` produced at the nearest adapter boundary. Reader open and probe failures carry a separate reader-context failure point. The CLI reports unknown dispatch as possibly sent, names the affected operation, and prohibits immediate retry until the Chrome/CDP boundary is verified. WeRead API failures report that the response was observed and direct the operator to verify the platform error meaning and browser authentication state before retrying.

The final validator recomputes account-name and WeChat `biz` matches from the configured and observed values, revalidates numeric `mid` and `idx`, requires every observation time to fall inside the canary attempt, and closes the reader/request topology: article-list attempts require a usable shelf entry and ready reader context; public identity attempts additionally require a successful article-list response and completed discovery. Duplicate target shelf entries are ambiguous and become `present_but_unusable` rather than selecting the first entry.

Overall failure states name the unavailable contents rather than claiming the request itself was not evaluated. Candidate summary output separates identity attempts, pages actually observed, unknown dispatches, verified identities, and budget-skipped candidates. Discovery that was attempted but produced an invalid or non-evaluable list is not described as “not attempted.” Legacy non-observations render as `NOT_MEASURED`, never a synthetic zero.

## Consequences

The evidence contract remains deliberately local, single-account, default-off, read-only, and bounded. No schema version validates Mp2RSS replacement readiness by itself; `replacementAssessment` remains `not_validated` until a separate same-account, same-window shadow comparison is completed. A real logged-in positive version 4 artifact remains required before the WeRead route can be considered viable.

The stricter provenance and relationship checks add fields to version 4 but remove two redundant fields and prevent future producers or consumers from silently accepting contradictory evidence. The source digest proves which implementation produced an artifact; it does not prove that no external side effect occurred, so external observation remains explicitly `not_measured`.
