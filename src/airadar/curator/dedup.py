from __future__ import annotations

from .score import ScoredCandidate


def deduplicate_candidates(candidates: list[ScoredCandidate]) -> list[ScoredCandidate]:
    seen_hashes: set[str] = set()
    seen_urls: set[str] = set()
    kept: list[ScoredCandidate] = []
    for candidate in sorted(candidates, key=lambda c: (-c.weighted_score, c.published_at, c.item_id)):
        normalized_url = candidate.url.strip().lower()
        if candidate.content_hash in seen_hashes or normalized_url in seen_urls:
            continue
        seen_hashes.add(candidate.content_hash)
        seen_urls.add(normalized_url)
        kept.append(candidate)
    return kept
