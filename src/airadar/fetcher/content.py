from __future__ import annotations

import re

import trafilatura


def clean_content(raw: str | None, fallback: str = "") -> str:
    text = raw or ""
    extracted = trafilatura.extract(text, include_comments=False, include_tables=False)
    if extracted:
        return _normalize_text(extracted)
    without_tags = re.sub(r"<[^>]+>", " ", text)
    cleaned = _normalize_text(without_tags)
    return cleaned or fallback


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
