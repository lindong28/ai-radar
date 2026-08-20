from __future__ import annotations

import re
import unicodedata

_LITERAL_NEWLINE_RE = re.compile(r"(?:\\r\\n|\\n|\\r)+")
_REAL_NEWLINE_RE = re.compile(r"[\r\n]+")
# Formatting noise that differs between feed renderers serving the same article.
_IDENTITY_NOISE_RE = re.compile(r"[\s　|｜\-–—_,，.。!！?？:：;；'\"“”‘’()（）\[\]【】]+")


def normalize_wechat_title(value: object) -> str:
    text = str(value or "")
    text = _LITERAL_NEWLINE_RE.sub(" ", text)
    text = _REAL_NEWLINE_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def wechat_slug_seed(value: object) -> str:
    text = normalize_wechat_title(value).lower()
    text = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "-", text)
    text = text.strip("-")
    return text or "wechat-article"


def wechat_identity_title(value: object) -> str:
    """Fold a title down to what two feed renderers agree on for the same article.

    Mp2RSS and Wechat2RSS serve the same article under URLs with no shared
    substring (short ``/s/<token>`` versus long ``?__biz&mid&idx&sn``), so a
    cross-source identity has to come from account plus title. The two
    renderers differ only in punctuation and full/half-width forms, which this
    strips; it deliberately does not stem or truncate, because two genuinely
    different articles from one account routinely share a topic.
    """
    folded = unicodedata.normalize("NFKC", normalize_wechat_title(value)).casefold()
    return _IDENTITY_NOISE_RE.sub("", folded)


def has_wechat_title_artifacts(value: object) -> bool:
    raw = str(value or "").strip()
    return raw != normalize_wechat_title(raw)
