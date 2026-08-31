# Stage WeChat whitespace fallback after an empty indexed result

- Status: accepted
- Date: 2026-09-01
- Refines: ADR-20260831-30ad, ADR-20260831-8b7c
- Supersedes: only ADR-20260831-30ad's single-stage execution detail for long item-field terms; its field scope, required-term semantics, aliases, and ranking remain in force

## Context

The first implementation made every long query term test whitespace-insensitive `LIKE` against item title, author, and body in the same SQL expression as trigram FTS. This closes both directions of a whitespace mismatch, such as stored `Claude Code` versus query `ClaudeCode`, but it also makes the indexed candidate path pay for a normalized substring scan on every query.

A read-only comparison used the current worktree code and `/Users/lindong/research/ai-radar/data/radar.db` through SQLite `mode=ro`. The snapshot contained 83,783 items and 3,328 saved WeChat interpretations. Each query ran eight times; the first reading is reported separately and the median uses the final six readings.

| Query | Always-normalized result | Always-normalized warm median | Strict indexed result | Strict warm median |
|---|---:|---:|---:|---:|
| `即梦 Seedance 2.5 实测` | 4, intended article first | 681 ms | 4 | 65 ms |
| `Seedance2.0 分镜 Skill` | 7 | 343 ms | 4 | 58 ms |
| `分享ClaudeCode` | 1 | 350 ms | 0 | 57 ms |
| `不存在的唯一串xyz987` | 0 | 350 ms | 0 | 56 ms |

The positive target, known compact-whitespace positive, and true negative make the instrument distinguish recall loss from a successful fast path. These are local relative readings from one database snapshot, not public latency claims.

Adding compact-normalized fields to `items_fts` would remove this execution tradeoff, but it is not a local web-route change. Migration 003, its byte-equivalent trigger copies, manifest `FTS_FIELDS`, server rebuild verification, and verifier identity all bind the current FTS schema. The storage and rebuild cost of extra compact body fields has not been measured.

## Decision

Use two stages for non-empty `/wechat` searches:

1. The strict stage keeps every parsed term required. Long item title/body/author terms and their controlled aliases use the existing trigram FTS; short terms use whitespace-insensitive escaped `LIKE`. Interpretation abstract, tags, and full summary continue to use whitespace-insensitive escaped `LIKE` because they are not in the shared item FTS.
2. Only when the strict stage returns zero rows, run the same query again with whitespace-insensitive escaped `LIKE` enabled for long item title/body/author terms. Ranking remains raw author, raw title term count, alias-only title term count, then deterministic recency.

This is an empty-result relaxation, not soft-AND: fallback still requires every query term. It does not change shared timeline or curated search, the FTS schema, the manifest, or production synchronization.

## Rejected alternatives

- Always run normalized `LIKE` for long item fields: it gives the broadest compact-whitespace recall, but the current snapshot shows that every query—including a true negative—pays the full scan.
- Add compact-normalized fields to `items_fts` now: this can provide indexed complete recall, but its index size, rebuild time, and synchronization-verifier cost are unmeasured, while its schema blast radius is already known to cross deployment contracts.
- Rely only on CamelCase or script-boundary tokenization: it cannot cover lowercase concatenation or arbitrary whitespace removal and would turn a general normalization promise into a casing heuristic.

## Residual boundary and discovery point

If the strict stage returns a weaker nonzero result set, this decision does not run the normalized item-field fallback. A more relevant candidate that is discoverable only through compact-whitespace matching can therefore remain hidden. No passive detector currently observes that failure, and the presence of some results is not evidence that search recall is healthy.

Reopen this decision on the first reproducible instance of any of the following:

- a user reports a missing target and an always-normalized comparison on the same database and query adds that target or ranks it higher while the strict result is nonzero;
- a regression fixture demonstrates the same shape;
- an independent observation of fallback frequency or latency shows that the current workload assumption no longer holds.

At that point compare strict, two-stage, and always-normalized behavior on the same database and query. If the first or second trigger is confirmed, the safe temporary reversal is the existing always-normalized query implementation; it is a code-only change with no data migration. Then measure compact FTS field size, rebuild time, and verifier impact before deciding whether to change the schema.

## Verification

Focused tests cover a strict positive that does not invoke fallback, a strict-zero compact-whitespace positive that does invoke fallback, and a strict-zero true negative that remains empty. Existing tests continue to cover required-term intersection, aliases, short terms, simplified/traditional variants, wildcard escaping, and author/title ranking.

## Decision review

Independent review required two repairs before approval: explicitly supersede only the earlier execution detail, and define how silent recall loss is discovered and reversed. The revised decision passed follow-up with the unresolved `strict>0` compact-only case retained as an explicit product tradeoff rather than an implied guarantee.
