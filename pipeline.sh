#!/usr/bin/env bash
# ai-radar pipeline orchestrator.
# Runs all stages sequentially and continues after individual stage failures.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

# cron runs this in a non-interactive shell, so nothing from the interactive
# rc is present. Local deployment settings live in a gitignored .env.
if [[ -f "$SCRIPT_DIR/.env" ]]; then
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/.env"
fi

# Resolve the egress proxy at run time: agent-proxy picks an ephemeral port per
# tunnel, so pinning one here would break silently on the next reconnect.
PROXY_STATUS="not configured"
if [[ -n "${AI_RADAR_PROXY_FILE:-}" ]]; then
  if [[ -r "$AI_RADAR_PROXY_FILE" ]]; then
    proxy_url="$(tr -d '[:space:]' <"$AI_RADAR_PROXY_FILE")"
    if [[ -n "$proxy_url" ]]; then
      export HTTP_PROXY="$proxy_url" HTTPS_PROXY="$proxy_url"
      export http_proxy="$proxy_url" https_proxy="$proxy_url"
      export NO_PROXY="${AI_RADAR_NO_PROXY:-localhost,127.0.0.1,::1}"
      export no_proxy="$NO_PROXY"
      PROXY_STATUS="$proxy_url"
    else
      PROXY_STATUS="FAILED: $AI_RADAR_PROXY_FILE is empty"
    fi
  else
    PROXY_STATUS="FAILED: cannot read $AI_RADAR_PROXY_FILE"
  fi
fi

LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/pipeline-$(date +%Y%m%d-%H%M%S).log"
LOCK_DIR="$SCRIPT_DIR/.pipeline.lock"
ACTIVITY_FILE="$SCRIPT_DIR/.pipeline.activity"
LOCK_OWNER_TOKEN=""
LOCK_BOOT_ID=""
LOCK_PROCESS_START=""
LOCK_CANDIDATE_ROOT=""
LOCK_CANDIDATE_DIR=""
LOCK_ACQUIRE_GRACE_SECONDS=30

log() {
  echo "[$(date +%Y-%m-%dT%H:%M:%S)] $*" | tee -a "$LOG_FILE"
}

mark_activity() {
  local activity_tmp=""
  activity_tmp="$(mktemp "$SCRIPT_DIR/.pipeline.activity.XXXXXX")" || return 1
  if ! printf '%s\n' "$LOCK_OWNER_TOKEN" >"$activity_tmp"; then
    rm -f "$activity_tmp"
    return 1
  fi
  if ! mv "$activity_tmp" "$ACTIVITY_FILE"; then
    rm -f "$activity_tmp"
    return 1
  fi
}

owner_value() {
  local key="$1"
  awk -F= -v key="$key" '$1 == key { sub(/^[^=]*=/, ""); print; exit }' "$LOCK_DIR/owner" 2>/dev/null
}

current_boot_id() {
  if [[ -r /proc/sys/kernel/random/boot_id ]]; then
    cat /proc/sys/kernel/random/boot_id
  elif [[ -x /usr/sbin/sysctl ]]; then
    /usr/sbin/sysctl -n kern.boottime 2>/dev/null
  else
    return 1
  fi
}

process_start_identity() {
  /bin/ps -p "$1" -o lstart= 2>/dev/null | awk '{$1=$1; print}'
}

owner_is_live() {
  [[ -f "$LOCK_DIR/owner" ]] || return 1
  local owner_pid=""
  local owner_boot_id=""
  local owner_process_start=""
  owner_pid="$(owner_value pid)"
  owner_boot_id="$(owner_value boot_id)"
  owner_process_start="$(owner_value process_start)"
  [[ "$owner_pid" =~ ^[1-9][0-9]*$ ]] || return 1
  [[ -n "$owner_boot_id" && "$owner_boot_id" == "$(current_boot_id 2>/dev/null)" ]] || return 1
  [[ -n "$owner_process_start" && "$owner_process_start" == "$(process_start_identity "$owner_pid")" ]] || return 1
  kill -0 "$owner_pid" 2>/dev/null
}

owner_token_matches() {
  [[ -n "$LOCK_OWNER_TOKEN" && "$(owner_value token)" == "$LOCK_OWNER_TOKEN" ]]
}

cleanup_lock() {
  if [[ -z "$LOCK_OWNER_TOKEN" || ! -f "$LOCK_DIR/owner" ]]; then
    return
  fi
  if owner_token_matches; then
    rm -rf "$LOCK_DIR"
  fi
}

cleanup_lock_candidate() {
  if [[ -n "$LOCK_CANDIDATE_ROOT" && -d "$LOCK_CANDIDATE_ROOT" ]]; then
    rm -rf "$LOCK_CANDIDATE_ROOT"
  fi
  LOCK_CANDIDATE_ROOT=""
  LOCK_CANDIDATE_DIR=""
}

prepare_lock_candidate() {
  LOCK_CANDIDATE_ROOT="$(mktemp -d "$SCRIPT_DIR/.pipeline.lock.acquire.XXXXXX")" || return 1
  LOCK_CANDIDATE_DIR="$LOCK_CANDIDATE_ROOT/.pipeline.lock"
  if ! mkdir "$LOCK_CANDIDATE_DIR"; then
    cleanup_lock_candidate
    return 1
  fi
  LOCK_OWNER_TOKEN="${LOCK_CANDIDATE_ROOT##*.pipeline.lock.acquire.}:$$"
  LOCK_BOOT_ID="$(current_boot_id)" || return 1
  LOCK_PROCESS_START="$(process_start_identity "$$")"
  if [[ -z "$LOCK_PROCESS_START" ]]; then
    cleanup_lock_candidate
    return 1
  fi
  if ! printf 'token=%s\ngeneration=%s\npid=%s\nboot_id=%s\nprocess_start=%s\n' \
    "$LOCK_OWNER_TOKEN" "$LOCK_OWNER_TOKEN" "$$" "$LOCK_BOOT_ID" "$LOCK_PROCESS_START" \
    >"$LOCK_CANDIDATE_DIR/owner"; then
    cleanup_lock_candidate
    return 1
  fi
  if ! printf '%s\n' "$$" >"$LOCK_CANDIDATE_DIR/pid"; then
    cleanup_lock_candidate
    return 1
  fi
}

publish_lock_candidate() {
  # The source basename is `.pipeline.lock` and the destination is its parent,
  # so this publishes an already-complete owner dir into the canonical path.
  # Safety does NOT rely on `mv -n` being an atomic no-replace CAS (macOS mv is
  # access()+rename(), not atomic): it rests on the invariant that a published
  # canonical is always a NON-EMPTY dir (owner written before publish). A racing
  # publisher's rename onto that non-empty dir fails with ENOTEMPTY, and the
  # `-e "$LOCK_DIR"` / leftover-candidate checks below identify it as the loser.
  # Result: the canonical path is either absent or already holds a full owner —
  # never observable mkdir'd-but-ownerless.
  if ! mv -n "$LOCK_CANDIDATE_DIR" "$SCRIPT_DIR/" 2>/dev/null; then
    if [[ -e "$LOCK_DIR" ]]; then
      cleanup_lock_candidate
      return 1
    fi
    cleanup_lock_candidate
    return 2
  fi
  if [[ -d "$LOCK_CANDIDATE_DIR" ]]; then
    cleanup_lock_candidate
    return 1
  fi
  LOCK_CANDIDATE_DIR=""
  rmdir "$LOCK_CANDIDATE_ROOT" 2>/dev/null || true
  LOCK_CANDIDATE_ROOT=""
  if ! owner_token_matches; then
    return 2
  fi
  if ! mark_activity; then
    cleanup_lock
    log "=== pipeline FAIL: cannot persist activity marker ==="
    return 2
  fi
  trap cleanup_lock EXIT
}

try_acquire_lock() {
  if ! prepare_lock_candidate; then
    cleanup_lock_candidate
    return 2
  fi
  publish_lock_candidate
}

lock_age_seconds() {
  local modified=""
  if modified="$(stat -f %m "$LOCK_DIR" 2>/dev/null)"; then
    :
  elif modified="$(stat -c %Y "$LOCK_DIR" 2>/dev/null)"; then
    :
  else
    return 1
  fi
  printf '%s\n' "$(( $(date +%s) - modified ))"
}

lock_generation_identity() {
  local identity=""
  if identity="$(stat -f '%d-%i-%c' "$LOCK_DIR" 2>/dev/null)"; then
    :
  elif identity="$(stat -c '%d-%i-%Z' "$LOCK_DIR" 2>/dev/null)"; then
    :
  else
    return 1
  fi
  printf '%s' "$identity" | tr -c 'A-Za-z0-9._-' '_'
}

acquire_lock() {
  try_acquire_lock
  local acquire_status=$?
  if [[ "$acquire_status" -eq 0 ]]; then
    return 0
  fi
  if [[ "$acquire_status" -eq 2 ]]; then
    log "=== pipeline FAIL: cannot persist lock ownership ==="
    exit 1
  fi

  local stale_generation=""
  stale_generation="$(lock_generation_identity || true)"
  if [[ -z "$stale_generation" ]]; then
    log "=== pipeline SKIP: lock generation changed before stale judgment ==="
    exit 0
  fi

  local existing_pid=""
  existing_pid="$(owner_value pid)"
  if owner_is_live; then
    log "=== pipeline SKIP: already running pid=$existing_pid ==="
    exit 0
  fi

  local lock_age=""
  lock_age="$(lock_age_seconds || true)"
  if [[ -z "$lock_age" || ( "$lock_age" -ge 0 && "$lock_age" -lt "$LOCK_ACQUIRE_GRACE_SECONDS" ) ]]; then
    log "=== pipeline SKIP: lock acquisition in progress ==="
    exit 0
  fi

  local current_generation=""
  current_generation="$(lock_generation_identity || true)"
  if [[ -z "$current_generation" || "$current_generation" != "$stale_generation" ]]; then
    log "=== pipeline SKIP: lock generation changed during stale recovery ==="
    exit 0
  fi
  if owner_is_live; then
    log "=== pipeline SKIP: lock owner became live during stale recovery ==="
    exit 0
  fi

  local stale_root="$SCRIPT_DIR/.pipeline.lock.reclaim.$stale_generation"
  mkdir -p "$stale_root"
  # The target is permanently bound to the generation observed before the stale
  # judgment. A winner that replaces it leaves this no-replace tombstone behind,
  # so a delayed reclaimer cannot move the successor generation.
  if ! mv -n "$LOCK_DIR" "$stale_root/" 2>/dev/null; then
    log "=== pipeline SKIP: lock owner changed during stale recovery ==="
    exit 0
  fi
  if [[ -e "$LOCK_DIR" ]]; then
    log "=== pipeline SKIP: another reclaimer won stale recovery ==="
    exit 0
  fi
  try_acquire_lock
  acquire_status=$?
  if [[ "$acquire_status" -eq 1 ]]; then
    log "=== pipeline SKIP: another owner acquired during stale recovery ==="
    exit 0
  fi
  if [[ "$acquire_status" -eq 2 ]]; then
    log "=== pipeline FAIL: cannot persist lock ownership ==="
    exit 1
  fi
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

log "=== egress proxy: $PROXY_STATUS ==="

run_stage fetch
run_stage prefilter --since 24h
run_stage score --since 24h
run_stage enrich --since 24h
run_stage curate
# Bounded batch per run: an unbounded interpret over a large error backlog can
# hold the pipeline lock for hours, starving fetch and inviting stale-lock
# reclaim races. Steady-state volume is a handful of items per cycle.
run_stage interpret --limit 30

find "$LOG_DIR" -name 'pipeline-*.log' -mtime +7 -delete

log "=== PIPELINE DONE (failed=$FAILED) ==="
exit "$FAILED"
