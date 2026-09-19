"""Read-only validation entry for independent question datasets; no inference."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .assets import OBJECT_BENCHMARKS, load_dataset


def main(*, target: str, argv=None) -> int:
    parser = argparse.ArgumentParser(description="校验独立题库身份与文件完整性；不执行模型或计算成绩。")
    parser.add_argument("command", choices=["validate"])
    parser.add_argument("--dataset", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        manifest, cases = load_dataset(args.dataset.expanduser(), target)
        if manifest["benchmark"] != OBJECT_BENCHMARKS[target]:
            raise ValueError("请使用此入口对应的独立 benchmark，历史题库使用历史入口")
    except (OSError, ValueError, KeyError) as exc:
        print(f"题库校验未通过：{exc}；未运行模型评测。", file=sys.stderr)
        return 1
    print(f"题库校验通过：{target}/{manifest['benchmark']}/{manifest['version']}，{len(cases)} 题。")
    print("仅验证题库身份与完整性；未运行推理、判官或评分。")
    return 0
