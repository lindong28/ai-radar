from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Emitted by every scoring run since the first one, so a row without them is a defect.
CORE_DIMENSIONS = ("relevance", "density", "recency", "authority", "engineering")
# Added 2026-09-06. Rows written before it exist in quantity, so it is optional everywhere:
# absent in a weights file, absent on a stored row. See weighted_score for what absent means.
LATER_DIMENSIONS = ("significance",)
DIMENSIONS = CORE_DIMENSIONS + LATER_DIMENSIONS


@dataclass(frozen=True)
class Weights:
    relevance: float
    density: float
    recency: float
    authority: float
    engineering: float
    significance: float = 0.0
    # Whether tier_multiplier applies. False by default because the vector below does not use it
    # and neither does the fit: see DEFAULT_WEIGHTS for the reading that retired it.
    uses_tier_multiplier: bool = False

    @classmethod
    def default(cls) -> Weights:
        """Curation ranking, fitted to AIHOT's own 0-100 score. See DEFAULT_WEIGHTS."""
        return cls(relevance=0.0, density=0.40, recency=0.0, authority=0.10, engineering=0.0, significance=0.50)

    def as_dict(self) -> dict[str, float]:
        """The numeric dimensions only -- uses_tier_multiplier is not one of them."""
        return {dimension: float(getattr(self, dimension)) for dimension in DIMENSIONS}

    def as_record(self) -> dict[str, Any]:
        """Everything needed to reproduce a score, for the archived record of a run.

        as_dict is the weighting; this is the whole function. While the tier multiplier was
        applied unconditionally it was implicit in the code version, so the dimensions alone were
        enough to recompute a past run. Now that it is a switch, a stored row without it cannot
        say whether its scores were multiplied.
        """
        return {**self.as_dict(), "uses_tier_multiplier": self.uses_tier_multiplier}

    def validate(self) -> None:
        values = self.as_dict()
        if any(value < 0 for value in values.values()):
            raise ValueError("weights must be non-negative")
        if sum(values.values()) <= 0:
            raise ValueError("weights total must be greater than zero")


DEFAULT_WEIGHTS = Weights.default()

# Ranking and the fit now use one vector. They were separate while ranking kept its own editorial
# weighting; the standing instruction is to fit AIHOT above all else, and the user chose on
# 2026-09-06 to move ranking onto the fitted vector.
#
# Fitted on the 2741 paired items of FULL3-20260906 against AIHOT's 0-100 score, over the simplex
# of non-negative weights on a 0.1 grid, half fitting and half held out, five seeds. All five
# seeds picked this exact vector; held-out Spearman 0.6331 (sd 0.0118), 0.6286 over the whole set,
# against 0.4502 for the weighting this replaces and 0.5925 for the five-dimension fit before
# `significance` existed. Keeping the tier multiplier on this vector costs 0.093.
#
# Three dimensions carry zero weight, each for a reason outside the scoring function:
#   relevance  -- production gates score and enrich behind the prefilter stage, which is the
#                 AI-relatedness decision; scoring it again adds nothing.
#   recency    -- selection applies a 48-hour freshness window and then keeps only the newest
#                 fresh date, so ordering never compares items across a meaningful age gap.
#   engineering -- redundant given the others in every fit run so far, on both weight vectors.
# They are still emitted and stored: a zero weight is a statement about this vector, not about
# whether the signal is worth measuring, and the next refit gets to disagree.
#
# The tier multiplier is gone rather than re-tuned. It was T1 1.25 / T1.5 1.0 / T2 0.75, and AIHOT
# scores those tiers 44.68 / 39.82 / 51.68 -- our multiplier ordered them backwards. An inverted
# multiplier was not fitted: tier is confounded with content type here and the fit has no way to
# separate them.
#
# The selection side is still deliberately absent. Its best weights did not survive a change of
# seed -- 0.821 on the fitting half against 0.771 held out -- because 78 positives cannot support
# a search over that many candidate vectors.
AIHOT_FIT_WEIGHTS = Weights.default()


def weights_from_mapping(data: dict[str, Any]) -> Weights:
    missing = [dimension for dimension in CORE_DIMENSIONS if dimension not in data]
    if missing:
        raise ValueError(f"missing weight dimensions: {', '.join(missing)}")
    supplied = {dimension: float(data[dimension]) for dimension in DIMENSIONS if dimension in data}
    # Spelled out rather than splatted: the dataclass also carries a bool, and a mapping of
    # floats must not be able to reach it.
    weights = Weights(
        relevance=supplied["relevance"],
        density=supplied["density"],
        recency=supplied["recency"],
        authority=supplied["authority"],
        engineering=supplied["engineering"],
        significance=supplied.get("significance", 0.0),
        # Settable from the file, because otherwise a `--weights` vector could never ask for the
        # multiplier: the field defaults to False and the only other way to set it is to build a
        # Weights in Python. Absent means False, which is what the shipped vector uses.
        uses_tier_multiplier=bool(data.get("uses_tier_multiplier", False)),
    )
    weights.validate()
    return weights


def load_weights(path: Path) -> Weights:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("weights file must be a JSON object")
    return weights_from_mapping(data)
