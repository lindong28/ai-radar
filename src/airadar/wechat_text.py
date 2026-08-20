from __future__ import annotations

import re
import unicodedata

_LITERAL_NEWLINE_RE = re.compile(r"(?:\\r\\n|\\n|\\r)+")
_REAL_NEWLINE_RE = re.compile(r"[\r\n]+")


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
    cross-source identity has to come from account plus title.

    Fold only what the two renderers were measured to disagree on. Over 126
    articles both carried, 125 titles were byte-identical and the one exception
    differed by a single U+00A0 where the other had a space — zero punctuation
    differences. NFKC settles that case and the width variants; whitespace
    collapse and casefold cost nothing. Stripping punctuation on top of that
    was folding noise nobody generates, and it merges titles that are genuinely
    different: ``报告：1.0！`` and ``报告10`` both flatten to ``报告10``, and a
    merge here silently drops an article for good.
    """
    return unicodedata.normalize("NFKC", normalize_wechat_title(value)).casefold()


def has_wechat_title_artifacts(value: object) -> bool:
    raw = str(value or "").strip()
    return raw != normalize_wechat_title(raw)
