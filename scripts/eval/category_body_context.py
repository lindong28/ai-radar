"""Freeze current article context alongside immutable classification inputs; no LLM calls."""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from airadar.enrich.article_context import prepare_article_context


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=20)
    args = parser.parse_args()
    if not 1 <= args.workers <= 8 or args.timeout <= 0:
        parser.error("workers must be 1..8 and timeout must be positive")
    with args.cases.open() as stream:
        cases = [json.loads(line) for line in stream if line.strip()]
    ids = [case["case_id"] for case in cases]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate case_id")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation refuses to overwrite a frozen context, even if another writer races.
    with args.output.open("x") as stream:
        def prepare(case):
            return {"case_id": case["case_id"], **prepare_article_context(case["input"], timeout=args.timeout)}
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            for row in executor.map(prepare, cases):
                stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
                stream.flush()


if __name__ == "__main__":
    main()
