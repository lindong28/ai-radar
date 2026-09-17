from __future__ import annotations

import re
from threading import RLock

import trafilatura

# trafilatura reuses its module-level HTML_PARSER across calls. Serialize parsing
# and tree copying after the collector's libxml2 invalid-free crash (2026-09-16);
# shared-parser causality remains unproven. Network requests remain concurrent.
_EXTRACTION_LOCK = RLock()


def clean_content(raw: str | None, fallback: str = "") -> str:
    text = raw or ""
    with _EXTRACTION_LOCK:
        extracted = trafilatura.extract(text, include_comments=False, include_tables=False)
    if extracted:
        return _normalize_text(extracted)
    without_tags = re.sub(r"<[^>]+>", " ", text)
    cleaned = _normalize_text(without_tags)
    return cleaned or fallback


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
