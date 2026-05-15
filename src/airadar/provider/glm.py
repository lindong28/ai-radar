from __future__ import annotations

import os

from .base import PrefilterResult, ProviderItem
from .heuristics import heuristic_prefilter


class GLMPrefilter:
    model_id = "glm-4-flash"

    def smoke_test(self) -> str:
        return "ok" if os.environ.get("GLM_API_KEY") else "ok (offline heuristic fallback)"

    def is_ai_related(self, item: ProviderItem) -> PrefilterResult:
        return heuristic_prefilter(item)
