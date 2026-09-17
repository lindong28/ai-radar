#!/usr/bin/env bash
# Independently scheduled collection, with its own lock and no AI stages.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$SCRIPT_DIR/logs/collection-supervisor"
exec >>"$SCRIPT_DIR/logs/collection-supervisor/radar.log" 2>&1
exec "$SCRIPT_DIR/.venv/bin/python" "$SCRIPT_DIR/scripts/collection_supervisor.py" \
  --state-dir "$SCRIPT_DIR/data/collection-state" run --kind radar \
  --budget 840 --attempts 3 --delay 15 -- "$SCRIPT_DIR/pipeline.sh" --collect-only
