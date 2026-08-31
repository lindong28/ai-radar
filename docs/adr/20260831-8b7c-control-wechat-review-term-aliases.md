# Control WeChat review-term aliases without weakening term intersection

- Status: accepted
- Date: 2026-08-31
- Refines: ADR-20260831-30ad

## Context

ADR-20260831-30ad requires every query term to match, but the reported query `即梦 Seedance 2.5 实测` still misses its intended article under strict lexical matching. The article uses `狂测` in its title and `评测` in interpreted text rather than the literal term `实测`. Replacing `实测` with either of those terms recovers the target. The broader word `体验` is not the same controlled review-term concept and would expand recall beyond the demonstrated mismatch.

The first implementation also constructed a title-ranking expression without placing it in the final `ORDER BY`. Title relevance therefore was not an existing behavior and must be implemented and verified as part of this refinement.

## Decision

Treat `实测`, `评测`, `测评`, and `狂测` as one controlled query-time alias group. Every raw query term remains required, but a term in this group may be satisfied by any member of the group across title, body, abstract, tags, or summary. Do not include `体验` and do not add generic fuzzy expansion or soft-AND behavior.

Keep author priority bound only to raw query terms. An alias match in an author name must not create an author-first result. Within that constraint, order matching rows by raw-term title-match count, then alias-only title-match count, then publication time, fetch time, and item id. This makes an exact remembered title term outrank an alias-only title hit without weakening the existing explicit-author preference.

## Rejected alternatives

- Add `体验` to the alias group: it is semantically broader than the demonstrated review-term mismatch.
- Allow missing query terms with soft-AND: this admits unrelated documents and changes a local vocabulary mismatch into a global precision loss.
- Let aliases trigger author priority: an author containing `评测` could outrank a stronger raw-title result for a query containing `实测`.
- Claim title ranking already exists: the current final `ORDER BY` does not consume the previously constructed expression.

## Verification

Tests must prove that raw author matches still outrank non-author matches, alias-only author names do not enter the author-first bucket, raw title matches outrank alias-only title matches, and the original query returns the intended article first against the current database snapshot. Multi-term intersection, short CJK terms, simplified/traditional variants, and escaped wildcard behavior remain required.

## Decision review

An independent review first rejected the false claim that title ranking was already wired into the final query. After the current state was corrected and `体验` was removed, the decision passed with one precision repair: aliases are excluded from author-priority matching. This ADR includes that repair.
