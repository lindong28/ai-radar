from __future__ import annotations

from .categories import matches_category


def compute(items: list[dict[str, object]]) -> dict[str, int]:
    return {
        "events": len(items),
        "first_party": sum(
            (item.get("source_kind") or "feed") != "x" and item.get("tier") == "T1"
            for item in items
        ),
        "new_models": sum(matches_category(item, "ai-models") for item in items),
        "sources": len({str(item.get("source_id")) for item in items if item.get("source_id")}),
    }
