#!/usr/bin/env bash
# Stable entry point for the ai-radar workflow.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

export PYTHONPATH="$SCRIPT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
if [[ "${1:-}" == "performance-probe" ]]; then
  exec uv run python -m airadar.performance.journey_monitor \
    --external-watchdog \
    --timeout-seconds 960 \
    --kill-after-seconds 5 \
    -- python -m airadar.cli "$@"
fi
exec uv run python -m airadar.cli "$@"
