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


# ── 下面两条持住 2026-09-12 review-gate 报出的 B1 与 A1 ────────────────────
# 它们防的失效**都不以报错的形态出现**：B1 是格式错时静默换语义或静默 no-op，
# A1 是系数打不中时静默 no-op——两者与「机制生效了但这一类不响应」在读数上完全同形。


@pytest.mark.parametrize("bad", ["09-05", "2026-9-5", "2026/09/05", "yesterday", ""])
def test_pause_from_rejects_malformed_dates(bad: str) -> None:
    """B1：`--pause-from` 是字符串比较，格式错会静默把它变成 --exclude-source 或 no-op。

    实测（修之前）：`--pause-from 09-05` 的输出与 `--exclude-source` **逐字节相同**，
    因为 `"2026-08-31" < "09-05"` 恒为假、整源被丢。
    """
    with pytest.raises(SystemExit) as excinfo:
        _sim._require_pause_from_format(bad)
    assert "YYYY-MM-DD" in str(excinfo.value)


@pytest.mark.parametrize("ok", ["2026-08-31", "2026-09-05", "1999-01-01"])
def test_pause_from_accepts_iso_dates(ok: str) -> None:
    """阴性对照：一个恒抛的校验会让这几条全红。"""
    _sim._require_pause_from_format(ok)


def test_pause_from_none_is_allowed() -> None:
    """不给就取窗口第一天，不该报错。"""
    _sim._require_pause_from_format(None)


B2O = {"tip": "tutorial"}


def test_unreachable_multiplier_is_refused() -> None:
    """A1：`factor` 改读 `mech_label` 后，旧守卫（校验 `primary_category`）罩不住这条路。

    实测（修之前）：`--enrich-stamp zzz-nope --multiplier model=3.0` 退出码 0，
    输出与不带系数时逐字节相同——系数是完整 no-op，而守卫放行。
    """
    with pytest.raises(SystemExit) as excinfo:
        _sim._require_multipliers_reachable({"model": 2.169}, set(), B2O)
    assert "打不中" in str(excinfo.value)


def test_reachable_multiplier_is_allowed() -> None:
    """阴性对照：恒抛的守卫会让这条红。`tip` bucket 映射到我方的 `tutorial`。"""
    _sim._require_multipliers_reachable({"model": 2.169, "tutorial": 0.885},
                                        {"model", "tip"}, B2O)


def test_unit_multiplier_need_not_be_reachable() -> None:
    """系数为 1.0 时不改变任何东西，打不中也不该拦。"""
    _sim._require_multipliers_reachable({"paper": 1.0}, {"model"}, B2O)
