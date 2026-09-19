"""Observed-membership consumer; same inference, distinct label contract."""
from evals._shared.prefilter_eval import main

if __name__ == "__main__":
    raise SystemExit(main(benchmark="aihot-observed-membership"))
