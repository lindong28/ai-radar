"""Evaluation identity, separate from frozen bytes and production dedup.

Only recognized X post routes share a URL identity. Other URLs retain the
legacy boundary; no generic query stripping or cross-publisher fuzzy matching.
"""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlsplit

from .assets import digest
from .dataset import news_key as legacy_news_key
from .dataset import split_for as legacy_split_for


def x_post_id(url: str) -> str | None:
    parsed = urlsplit(url.strip())
    if parsed.scheme.lower() not in {"http", "https"} or parsed.hostname not in {
        "x.com", "www.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com",
    }:
        return None
    match = re.fullmatch(r"/(?:[A-Za-z0-9_]+/status|i/web/status)/([0-9]+)(?:/(?:photo|video)/[0-9]+)?/?", parsed.path)
    return match[1] if match else None


def identity_url(url: str) -> str:
    post = x_post_id(url)
    return f"https://x.com/i/web/status/{post}" if post else url


def input_url(url: str, source: dict) -> str:
    post = x_post_id(url)
    identity = source.get("derived_aihot_identity", "")
    if post and identity.startswith("x:") and re.fullmatch(r"[A-Za-z0-9_]+", identity[2:]):
        return f"https://x.com/{identity[2:].lower()}/status/{post}"
    return url


def news_key(source: str, url: str) -> str:
    return legacy_news_key(source, identity_url(url))


def split_for(url: str) -> str:
    return legacy_split_for(identity_url(url))


def substantive_title(title: str) -> str:
    # Typography, not a semantic paraphrase detector. Keep numbers/operators.
    # NFC preserves superscripts (10⁶ != 106). Fold full-width ASCII only,
    # rather than compatibility-normalizing every mathematical character.
    value = unicodedata.normalize("NFC", title)
    value = "".join(chr(ord(c) - 0xFEE0) if 0xFF01 <= ord(c) <= 0xFF5E else c for c in value)
    value = value.translate(str.maketrans({
        "“": '"', "”": '"', "‘": '"', "’": "'", "—": "-", "–": "-",
        "，": ",", "：": ":", "；": ";", "。": ".",
    }))
    value = re.sub(r"(?<!\w)['\"]|['\"](?!\w)", "", value)
    value = re.sub(r"[,;:?]+", " ", value)
    # Sentence punctuation is not an operator: retain !=, unary !, and 5!.
    value = re.sub(r"(?<=[^\W\d])!+(?=\s|$)", " ", value)
    value = re.sub(r"\.+\s*$", "", value)
    return " ".join(value.split())


def substantive_hash(raw: dict, target: str) -> str:
    """Compare target-relevant content; do not rewrite captured model text.

    published_at is retained for time-window/rule consumers, but is not a
    content-version veto. HTML and source tags are not these model inputs.
    """
    fields = {"title": substantive_title(raw.get("title") or ""),
              "content_text": raw.get("content_text"),
              "identity": news_key(raw["source_id"], raw["url"])}
    if target != "content-enrichment":
        fields["author"] = raw.get("author")
    return digest(fields)
