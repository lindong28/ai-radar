from __future__ import annotations

from pathlib import Path

import pytest
from production_shape import ProductionShape, build_production_shape


@pytest.fixture(scope="session")
def production_shape(tmp_path_factory: pytest.TempPathFactory) -> ProductionShape:
    root = tmp_path_factory.mktemp("production-shape")
    return build_production_shape(root / "radar.db", root / "expected-manifest.json")


@pytest.fixture
def isolated_production_shape(
    production_shape: ProductionShape,
    tmp_path: Path,
) -> ProductionShape:
    return production_shape.clone(tmp_path / "radar.db")
