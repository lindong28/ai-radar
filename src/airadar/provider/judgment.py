"""Validate explanations at the model-response boundary, before projection."""
from __future__ import annotations


class JudgmentFormatError(ValueError):
    """Keep the invalid response available for per-item failure recording."""

    def __init__(self, message: str, payload: object):
        super().__init__(message)
        self.payload = payload


def require_reason_first(payload: dict, decision: str) -> str:
    """Inspect parser insertion order; never reorder or manufacture a rationale."""
    if not isinstance(payload, dict) or not isinstance(payload.get("reason"), str) or not payload["reason"].strip():
        raise JudgmentFormatError("model judgment requires a nonempty reason", payload)
    if next(iter(payload)) != "reason" or decision not in payload:
        raise JudgmentFormatError("model judgment must emit reason before its decision", payload)
    return payload["reason"]
