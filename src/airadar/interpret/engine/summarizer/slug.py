from __future__ import annotations

import re
import unicodedata

_CJK_RE = r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"


def slugify_title(title: str, *, max_length: int = 80) -> str:
    """Create a filesystem-safe slug from an article title.

    Rules mirror the summarize-article skill: remove punctuation, convert English
    spaces to underscores, remove spaces between Chinese text, and truncate long
    titles without leaving dangling separators.
    """
    normalized = unicodedata.normalize("NFKC", title).strip()
    cleaned_chars: list[str] = []
    for char in normalized:
        category = unicodedata.category(char)
        if category[0] in {"P", "S"}:
            cleaned_chars.append(" ")
        else:
            cleaned_chars.append(char)

    cleaned = re.sub(r"\s+", " ", "".join(cleaned_chars)).strip()
    slug = cleaned.replace(" ", "_")
    slug = re.sub(rf"(?<=[{_CJK_RE}])_(?=[{_CJK_RE}])", "", slug)
    slug = re.sub(r"_+", "_", slug).strip("_")
    if not slug:
        return "article"

    if len(slug) <= max_length:
        return slug

    truncated = slug[:max_length].rstrip("_")
    if "_" in truncated and not re.match(rf".*[{_CJK_RE}]$", truncated):
        truncated = truncated.rsplit("_", 1)[0] or truncated
    return truncated.rstrip("_") or "article"

