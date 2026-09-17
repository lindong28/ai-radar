"""Canonical visible-score/aihot-visible-score entry; shared implementation stays single-source."""
from evals._shared.cli import main

if __name__ == "__main__":
    raise SystemExit(main(entry_target="visible-score"))
