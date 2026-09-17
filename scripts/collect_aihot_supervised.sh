#!/usr/bin/env bash
# Runtime code checkout is explicit; no moving another session's worktree HEAD.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
: "${AIHOT_CAPTURE_WORKTREE:?set an approved clean runtime checkout}"
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:/usr/bin:/bin:$PATH"
export PYTHONPATH="$REPO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export UV_PROJECT_ENVIRONMENT="$REPO_ROOT/.venv"
mkdir -p "$REPO_ROOT/logs/collection-supervisor"
exec >>"$REPO_ROOT/logs/collection-supervisor/aihot.log" 2>&1
exec "$REPO_ROOT/.venv/bin/python" "$SCRIPT_DIR/collection_supervisor.py" \
  --state-dir "$REPO_ROOT/data/collection-state" \
  --aihot-root "$AIHOT_CAPTURE_WORKTREE/benchmarks/aihot" \
  run --kind aihot --budget 2400 --attempts 3 --delay 15 -- \
  /bin/bash "$REPO_ROOT/scripts/capture_aihot_daily.sh"
