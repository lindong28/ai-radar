#!/usr/bin/env bash
# Independent of both collection locks and all AI processing.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:/usr/bin:/bin:$PATH"
export PYTHONPATH="$REPO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
: "${AIHOT_CAPTURE_WORKTREE:?set an approved clean runtime checkout}"
AIHOT_REFERENCE_PATH="$(git config -f "$AIHOT_CAPTURE_WORKTREE/.gitmodules" --get submodule.benchmarks/aihot.path)"
mkdir -p "$REPO_ROOT/logs/collection-supervisor"
exec >>"$REPO_ROOT/logs/collection-supervisor/health.log" 2>&1
exec "$REPO_ROOT/.venv/bin/python" "$SCRIPT_DIR/collection_supervisor.py" \
  --state-dir "$REPO_ROOT/data/collection-state" \
  --aihot-root "$AIHOT_CAPTURE_WORKTREE/$AIHOT_REFERENCE_PATH" health \
  --radar-root "$REPO_ROOT/data/raw-capture" \
  --sources-path "$REPO_ROOT/data/sources.toml" --notify
