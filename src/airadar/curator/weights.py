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
