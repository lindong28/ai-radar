#!/usr/bin/env bash
# ai-radar pipeline orchestrator.
# Runs all stages sequentially and continues after individual stage failures.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# /usr/sbin is not on cron's PATH, and the domain-selector status helper
# (system-config bin/agent-proxy-wait-launchd-listener) shells out to bare
# `lsof`, which lives there. Without it that helper dies with FileNotFoundError,
# check-proxy-status reports degraded and exits 1, and egress-preflight then
# fails closed -- every managed external stage is skipped for the whole round.
# Symptom is a healthy manual run and a failing cron run (2026-09-05, 4+ rounds).
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$HOME/.cargo/bin:/usr/sbin:$PATH"

# cron runs this in a non-interactive shell, so nothing from the interactive
# rc is present. Local deployment settings live in a gitignored .env.
if [[ -f "$SCRIPT_DIR/.env" ]]; then
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/.env"
fi

LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/pipeline-$(date +%Y%m%d-%H%M%S).log"
LOCK_FILE="$SCRIPT_DIR/.pipeline.flock"
ACTIVITY_FILE="$SCRIPT_DIR/.pipeline.activity"

log() {
  echo "[$(date +%Y-%m-%dT%H:%M:%S)] $*" | tee -a "$LOG_FILE"
}

mark_activity() {
  local generation=""
  generation="$(python3 -c 'import uuid; print(uuid.uuid4())')" || return 1
  local activity_tmp=""
  activity_tmp="$(mktemp "$SCRIPT_DIR/.pipeline.activity.XXXXXX")" || return 1
  if ! printf '%s\n' "$generation" >"$activity_tmp"; then
    rm -f "$activity_tmp"
    return 1
  fi
  if ! mv "$activity_tmp" "$ACTIVITY_FILE"; then
    rm -f "$activity_tmp"
    return 1
  fi
}

# Mutual exclusion is a kernel-held BSD flock on fd 9 (ADR-052). The lock lives
# on the open file description, which every child stage inherits: it is released
# only when the LAST process holding it exits. That is deliberate — the mutex
# protects "the process tree that writes radar.db", so a SIGKILLed orchestrator
# whose enrich stage is still writing must keep the next cron round out.
# Observers (journey monitor, A6) probe the same file with a shared lock; their
# microsecond hold can make one exclusive attempt fail spuriously, hence the
# short retry loop. A real concurrent pipeline keeps failing for the whole
# window, so the SKIP judgment is unaffected.
acquire_lock() {
  if ! exec 9>"$LOCK_FILE"; then
    log "=== pipeline FAIL: cannot open lock file $LOCK_FILE ==="
    exit 1
  fi
  local attempt
  for attempt in 1 2 3 4 5; do
    if python3 -c 'import fcntl; fcntl.flock(9, fcntl.LOCK_EX | fcntl.LOCK_NB)' 2>/dev/null; then
      if ! mark_activity; then
        log "=== pipeline FAIL: cannot persist activity marker ==="
        exit 1
      fi
      return 0
    fi
    log "=== pipeline lock busy (attempt $attempt/5) ==="
    sleep 0.2
  done
  log "=== pipeline SKIP: already running ==="
  exit 0
}

acquire_lock

FAILED=0

log "=== egress preflight START ==="
if ./run.sh egress-preflight >>"$LOG_FILE" 2>&1; then
  log "=== egress preflight OK ==="
else
  code=$?
  log "=== egress preflight FAIL (exit $code) ==="
  exit 1
fi

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
# Bounded batch per run (same reasoning as interpret below): switching the
# enrich ruleset turns every prefilter-passed item in the window into a new
# candidate at once (2026-09-02: 4295 items, ~6s each, lock held 5h+ and every
# later round skipped). Steady-state volume is 10-20 items per cycle; the
# backlog drains across rounds instead of starving fetch/curate.
run_stage enrich --since 24h --limit 40
run_stage curate
# Bounded batch per run: an unbounded interpret over a large error backlog can
# hold the pipeline lock for hours, starving fetch in later rounds.
# Steady-state volume is a handful of items per cycle.
run_stage interpret --limit 30

find "$LOG_DIR" -name 'pipeline-*.log' -mtime +7 -delete

log "=== PIPELINE DONE (failed=$FAILED) ==="
exit "$FAILED"
