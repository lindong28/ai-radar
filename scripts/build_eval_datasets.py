#!/usr/bin/env python3
"""Offline, independently eligible AIHOT question sets (no model calls)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals._shared.object_datasets import main  # noqa: E402

if __name__ == "__main__":
    main()
