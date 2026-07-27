from __future__ import annotations

from pathlib import Path

import pytest

from airadar.performance.runner import ProbeInfrastructureError, ProbeRuntime, run_adapter


def test_run_adapter_rejects_unconfigured_public_vantage(tmp_path: Path) -> None:
    runtime = ProbeRuntime(
        origin_url="http://origin.invalid",
        public_url="",
        stage_ledger_root=tmp_path / "ledger",
        db_path=tmp_path / "radar.db",
    )
    environ = {
        "CONTINUOUS_PERFORMANCE_JOURNEY": "homepage.first_card",
        "CONTINUOUS_PERFORMANCE_VANTAGE": "same_host_public",
    }
    with pytest.raises(ProbeInfrastructureError, match="vantage_unconfigured"):
        run_adapter(environ=environ, executable=tmp_path / "adapter", runtime=runtime)
