#!/usr/bin/env bash
# ai-radar pipeline orchestrator.
# Runs all stages sequentially and continues after individual stage failures.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/pipeline-$(date +%Y%m%d-%H%M%S).log"
LOCK_DIR="$SCRIPT_DIR/.pipeline.lock"

log() {
  echo "[$(date +%Y-%m-%dT%H:%M:%S)] $*" | tee -a "$LOG_FILE"
}

acquire_lock() {
  if mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "$$" >"$LOCK_DIR/pid"
    trap 'rm -rf "$LOCK_DIR"' EXIT
    return 0
  fi

  local existing_pid=""
  if [[ -f "$LOCK_DIR/pid" ]]; then
    existing_pid="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
  fi
  if [[ -n "$existing_pid" ]] && kill -0 "$existing_pid" 2>/dev/null; then
    log "=== pipeline SKIP: already running pid=$existing_pid ==="
    exit 0
  fi

  rm -rf "$LOCK_DIR"
  mkdir "$LOCK_DIR"
  echo "$$" >"$LOCK_DIR/pid"
  trap 'rm -rf "$LOCK_DIR"' EXIT
}

acquire_lock

FAILED=0

run_stage() {
  local stage=$1
  shift

  log "=== $stage START ==="
  if ./run.sh "$stage" "$@" >>"$LOG_FILE" 2>&1; then
    log "=== $stage OK ==="
  else
    local code=$?
    log "=== $stage FAIL (exit $code) ==="
    FAILED=1
  fi
}

run_stage fetch
run_stage prefilter --since 24h
run_stage score --since 24h
run_stage enrich --since 24h
run_stage curate

find "$LOG_DIR" -name 'pipeline-*.log' -mtime +7 -delete

log "=== PIPELINE DONE (failed=$FAILED) ==="
exit "$FAILED"
