# Hybrid WeChat search and explicit KB archive import

- Status: accepted
- Date: 2026-08-31
- Refines: ADR-007, ADR-059
- Supersedes: the field-scope and data-scale assumptions in `docs/plans/20260607-wechat-interpretation-search/plan.md`; the rest of that archived plan remains historical context

## Context

`/wechat` currently removes whitespace from the whole query and applies one `LIKE` pattern to title, author, abstract, and tags. This works for a remembered contiguous phrase, but fails when a user remembers several terms that occur in different fields. The real query `即梦 Seedance 2.5 实测` returned no result even though the target article is present: its terms are split between the title and body. A second requested article exists in the ai-assistant knowledge base but is absent from AI Radar, so search changes alone cannot recover it.

The original 2026-06 search plan deliberately searched only card fields and chose `LIKE` at a scale of 202 saved interpretations. The current request is based on a demonstrated miss and asks for a long-term search solution; AI Radar already maintains an FTS5 trigram index over item titles and bodies. SQLite documents trigram substring matching, its inability to match full-text tokens shorter than three Unicode characters, and `bm25()` relevance ordering in the [official FTS5 documentation](https://www.sqlite.org/fts5.html).

The KB boundary is also explicit: ai-assistant owns `index.json`, vector alignment, URL identity, and filesystem paths. External consumers must use its stable CLI rather than import private loaders or parse those stores directly. AI Radar remains the only store read by the public web application.

## Decision

### Search

Parse a non-empty query into whitespace- and punctuation-delimited terms. Every term is required, while each term may match any searchable field. Terms of at least three Unicode characters use the existing trigram FTS index for title, body, and author; shorter terms and interpretation-only fields use escaped, whitespace-insensitive `LIKE`. Simplified/traditional variants remain supported.

Ranking preserves the accepted user-visible invariant: an author hit strictly outranks every non-author hit. Within each author bucket, exact title and title-term matches outrank abstract, tag, summary, and body-only matches; publication, fetch, and item identifiers remain deterministic recency tie-breakers.

This expands the old card-only field scope because the reported false negative cannot be fixed while excluding the body. Term intersection limits the noise introduced by the wider field set. No embedding model or remote service is added to the request path.

### KB catalog and manual import

ai-assistant exposes a versioned, read-only JSONL article catalog from the summary-agent CLI. It is the authority for KB slug, canonical URL, index metadata, referenced files, and vector integrity. AI Radar adds `./run.sh admin wechat-kb import` with `--dry-run`, bounded `--limit`, `--assistant-root`, `--user`, and `--db-path` options. The command imports only catalog entries whose URL is a WeChat article, whose vector and referenced files validate, and for which no AI Radar item already exists. Existing items without an interpretation are reported separately and are not mutated by this command.

Imported rows use the reserved source id `wx_ai_assistant_kb_archive`, with `kind='wechat'`, `enabled=0`, and `url='internal://ai-assistant-kb'`. One module owns the reserved id and two predicates:

- WeChat visibility and cross-source identity include enabled WeChat sources plus this reserved source.
- Public source surfaces exclude the reserved source.

Keeping the row disabled prevents fetch and A7 scheduling. Explicit predicates make archive articles visible on `/wechat`, allow a later enabled feed item to deduplicate against them, and keep the internal row out of public source APIs and counts. Source reload may leave the unconfigured row disabled but must not erase these semantics.

Every imported item records `origin`, `import_run_id`, `kb_slug`, and the upstream canonical URL in `extra_json`. The run id is provenance for the import receipt and postcheck; it is not a deletion capability. A failed import or failed postcheck rolls back its still-open transaction. A successfully committed run has no batch-deletion command.

This distinction is required by the existing cross-source identity rule. Once an archive row exists, a later live-feed fetch of the same article is deliberately deduplicated against it. Deleting the archive row afterwards can therefore delete the only stored and visible identity even though a live source observed the article. A temporary-SQLite reproduction covered that exact sequence—archive insert, later live-feed upsert suppressed as a duplicate, then archive deletion—and demonstrated the loss. This evidence is deliberately scoped to that sequence; it is not a claim that every deletion or every deduplication path loses data.

If an operator later needs to undo a committed batch, that need is the discovery point for a separate reviewed data-repair operation. Such a repair must first establish whether each archive row is still the sole stored identity or has a safe replacement; a future promotion/claim ledger could make that ownership explicit. Neither mechanism is introduced by this import feature.

Before commit, the import transaction checks that the run's provenance count, item count, saved/synced interpretation count, WeChat-visible count, and FTS item count agree, and that no imported source is public. A mismatch rolls the whole run back and returns nonzero. Human output reports the run id, imported/already-present/skipped/remaining counts, the postcheck result, and whether changes were made.

## Rejected alternatives

- Keep whole-query `LIKE`, or tokenize only the existing four fields: both preserve the demonstrated miss.
- Search every field with pure `LIKE`: this gives every long body field an unindexed substring scan. The existing trigram index already supports the required title/body/author substring candidate set, while escaped `LIKE` remains necessary for short terms and interpretation-only fields.
- Add vector search: the use case is lexical recall of product names, versions, and topic terms; a new model, index-consistency path, and opaque ranking are unnecessary.
- Scan raw Markdown headers: this bypasses the summary-agent's ownership of URL identity, index status, vector alignment, and stored path resolution.
- Read ai-assistant private stores directly: this violates its CLI-only boundary and couples AI Radar to a private layout.
- Attribute imported items to Mp2RSS or Wechat2RSS, or create an enabled source: both create false provenance and incorrect fetch, alert, and public-source behavior.
- Exclude the archive source from cross-source deduplication: a later feed observation would create a second card for the same article.
- Offer run-scoped deletion without an ownership ledger: the reproduced archive-to-live sequence can delete the only stored identity.
- Add a promotion/claim ledger now: it would introduce a new state machine and schema beyond the requested discovery-and-import command. It should be evaluated only if committed-batch repair becomes an actual operational need.

## Scope and verification

This decision covers `/wechat` query semantics, the internal archive identity, the manual import command, and the minimal ai-assistant catalog CLI. It does not change the KB save decision, embeddings, automatic discovery, scheduled pipeline stages, production deployment, or production data.

Required focused verification covers multi-term cross-field matches, short CJK terms, simplified/traditional variants, wildcard escaping, author-first ordering, archive list/detail visibility, public-source exclusion, fetch/A7 exclusion, later-feed deduplication, source reload behavior, import idempotency, and transaction rollback on a failed postcheck. The first implementation run must use a temporary database and include positive and negative catalog instances.

Known unverified boundaries at decision time are the current real catalog's status distribution, total eligible import count and runtime, same-article/different-URL cases outside the existing title/author/time identity, public end-to-end latency, production deployment, and production import. These claims require later direct observations and are not implied by implementation tests.

## Decision review

An independent adversarial review initially required three repairs: prove every archive-source consumer boundary, preserve author-first ordering, and define run-scoped provenance plus a post-import oracle. A later review reproduced the unsafe interaction between live-feed deduplication and success-batch deletion. This accepted decision therefore explicitly supersedes the earlier review packet's unshipped deletion promise: failures retain transactional rollback, while committed runs retain provenance but expose no rollback command. The next discovery point is an operator's first concrete need to undo a committed batch; the remedy is a separately reviewed data repair or ownership-ledger design, not blind run-id deletion.
