"""持住 `simulate_multirun_archive._refuse_overlay_with_quota` 那道闸。

为什么需要它：那道闸防的失效**不会以失败读数的形态出现**。覆盖层新增的候选拿不到机制标签，
于是整类绕过配额封顶，读数只是偏向「封顶生效了」那一侧——不报错、不异常。
没有测试持住时，删掉它整套测试照样全绿，于是它是一条大家都相信而没人检查的不变式。

这里同时给**阴性对照**：三个参数各自单独出现都不得拒绝。只测阳性的话，
一个恒真的闸（无条件拒绝）也会让阳性通过。
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "_sim_guard", REPO / "scripts" / "eval" / "simulate_multirun_archive.py"
)
assert _SPEC and _SPEC.loader
_sim = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_sim)


def _args(**over: object) -> argparse.Namespace:
    base = {"score_overlay": None, "closed_loop": False, "actuator": "rank"}
    base.update(over)
    return argparse.Namespace(**base)


def test_overlay_with_quota_actuator_is_refused() -> None:
    for actuator in ("quota", "quota+floor"):
        with pytest.raises(SystemExit) as excinfo:
            _sim._refuse_overlay_with_quota(
                _args(score_overlay="/tmp/o.db", closed_loop=True, actuator=actuator)
            )
        assert "绕过封顶" in str(excinfo.value)


@pytest.mark.parametrize(
    "over",
    [
        # 覆盖层单独出现：合法，这是本轮的主用法
        {"score_overlay": "/tmp/o.db"},
        # 闭环 + 封顶但不带覆盖层：合法，这是测封顶本身的用法
        {"closed_loop": True, "actuator": "quota"},
        # 覆盖层 + 闭环但 actuator 是 rank：合法，rank 不读机制标签
        {"score_overlay": "/tmp/o.db", "closed_loop": True, "actuator": "rank"},
        # 覆盖层 + 封顶但没开闭环：`--actuator` 只在闭环下起作用
        {"score_overlay": "/tmp/o.db", "actuator": "quota"},
        # 什么都不给
        {},
    ],
)
def test_other_combinations_are_allowed(over: dict[str, object]) -> None:
    """阴性对照：一个恒真的闸会让这几条全红。"""
    _sim._refuse_overlay_with_quota(_args(**over))
