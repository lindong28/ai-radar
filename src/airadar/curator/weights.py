from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

DIMENSIONS = ("relevance", "density", "recency", "authority", "engineering")


@dataclass(frozen=True)
class Weights:
    relevance: float
    density: float
    recency: float
    authority: float
    engineering: float

    @classmethod
    def default(cls) -> Weights:
        """Curation ranking. Unchanged: see AIHOT_FIT_WEIGHTS for why it was left alone."""
        return cls(relevance=0.10, density=0.40, recency=0.30, authority=0.10, engineering=0.10)

    def as_dict(self) -> dict[str, float]:
        return asdict(self)

    def validate(self) -> None:
        values = self.as_dict()
        if any(value < 0 for value in values.values()):
            raise ValueError("weights must be non-negative")
        if sum(values.values()) <= 0:
            raise ValueError("weights total must be greater than zero")


DEFAULT_WEIGHTS = Weights.default()

# AIHOT scores an item and selects an item with two different functions, and one weighting cannot
# serve both: fitted against its 0-100 score the best weights load density and drop the tier
# multiplier, fitted against what it selected they load authority and keep the multiplier, and the
# two are close to orthogonal. This vector is the first of those, for measuring how closely our
# scoring tracks AIHOT's -- it is not used for ranking.
#
# Fitted on 2741 paired items, half fit and half held out, repeated over five seeds: density 0.5
# and authority 0.2 in every one, engineering 0.0 in every one, relevance and recency trading
# 0.0-0.3 between them; held-out Spearman 0.519 to 0.558 against 0.449 for the ranking weights
# with the tier multiplier applied. Values below are the per-dimension medians.
#
# The selection side was fitted the same way and is deliberately absent. Its best weights did not
# survive a change of seed -- 0.821 on the fitting half against 0.771 held out, where the ranking
# weights already give 0.773 -- because 78 positives cannot support a search over 126 candidate
# vectors. Ranking stays as it is until there are more selected items to fit against.
AIHOT_FIT_WEIGHTS = Weights(relevance=0.10, density=0.50, recency=0.20, authority=0.20, engineering=0.0)
AIHOT_FIT_USES_TIER_MULTIPLIER = False


def weights_from_mapping(data: dict[str, Any]) -> Weights:
    missing = [dimension for dimension in DIMENSIONS if dimension not in data]
    if missing:
        raise ValueError(f"missing weight dimensions: {', '.join(missing)}")
    weights = Weights(**{dimension: float(data[dimension]) for dimension in DIMENSIONS})
    weights.validate()
    return weights


def load_weights(path: Path) -> Weights:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("weights file must be a JSON object")
    return weights_from_mapping(data)
