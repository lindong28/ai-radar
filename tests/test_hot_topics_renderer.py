"""把 CSR 渲染器的 node 测试拉进常规 pytest 套件。

仓里已有的 `.mjs` 测试没有任何执行入口（`test.sh` 只跑 pytest），所以一个只放在
那边的测试等于没有。这个 shim 让 `renderHotTopics` 的判别力跟着 `uv run pytest`
一起生效。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

TEST_FILE = Path(__file__).parent / "js" / "hot_topics_renderer.test.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_hot_topics_csr_renderer_matches_the_ssr_partial() -> None:
    result = subprocess.run(
        ["node", "--test", str(TEST_FILE)],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
        check=False,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
