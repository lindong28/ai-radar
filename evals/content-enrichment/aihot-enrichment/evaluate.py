"""Canonical content-enrichment/aihot-enrichment entry; shared implementation stays single-source."""
from evals._shared.cli import main

if __name__ == "__main__":
    raise SystemExit(main(entry_target="content-enrichment"))
