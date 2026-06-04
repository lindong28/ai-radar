from __future__ import annotations

import re

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


def has_wechat_title_artifacts(value: object) -> bool:
    raw = str(value or "").strip()
    return raw != normalize_wechat_title(raw)
