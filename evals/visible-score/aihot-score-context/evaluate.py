"""Validate or evaluate frozen archive-context score questions."""
from evals._shared.score_eval import main
from evals._shared.assets import SCORE_CONTEXT

if __name__ == "__main__":
    raise SystemExit(main(benchmark=SCORE_CONTEXT))
