#!/usr/bin/env bash
# Server-side health checks. Run on a timer; the server is the only production
# entry point once traffic cuts over, so an unnoticed failure here is an
# unnoticed outage.
#
# Four rules, each answering a different question:
#   serve      - is a process listening at all?
#   healthz    - does it answer correctly? (a process can be up and broken)
#   disk       - will the next database sync have room to land?
#   freshness  - is the replica still being updated? (sync can stop silently
#                while every other check stays green)
#   deploy     - did the last code push fail? (post-receive cannot fail the
#                git push itself, so its failure marker is paged on here)
#
# Delivery goes through im-notify, the same path the Mac side already uses.
# Alerts are deduplicated by key so a persistent fault does not page on every
# tick; a key rearms once its check passes again.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

AI_RADAR_HOME="${AI_RADAR_HOME:-$REPO_ROOT}"
STATE_DIR="${AI_RADAR_ALERT_STATE_DIR:-$AI_RADAR_HOME/data/alert-state}"
ACTIVE_UPSTREAM_CONF="${AI_RADAR_ACTIVE_UPSTREAM_CONF:-$AI_RADAR_HOME/data/nginx/ai-radar-active-upstream.conf}"
RECEIPT="${AI_RADAR_SNAPSHOT_RECEIPT:-$AI_RADAR_HOME/data/accepted-snapshot.json}"

DISK_MIN_FREE_GB="${AI_RADAR_DISK_MIN_FREE_GB:-8}"
# Two full sync intervals plus slack: one missed run is a hiccup, two in a row
# means the chain is actually broken.
FRESHNESS_MAX_AGE_MIN="${AI_RADAR_FRESHNESS_MAX_AGE_MIN:-150}"

DRY_RUN="${AI_RADAR_ALERT_DRY_RUN:-0}"

mkdir -p "$STATE_DIR"

notify() {
  local key="$1" severity="$2" message="$3"
  local marker="$STATE_DIR/$key.firing"

  if [[ -f "$marker" ]]; then
    return 0  # already delivered; rearms when the check passes
  fi

  printf '[alert] %s %s: %s\n' "$severity" "$key" "$message"

  # The marker means "this firing was DELIVERED", so it is written only after
  # a successful send. Writing it first turned any single delivery failure --
  # a dry run, im-notify missing, one transient webhook error -- into permanent
  # silence: every later tick saw the marker and returned, the fault was never
  # reported, and on recovery the operator could even receive a "recovered"
  # message for an alert that was never delivered in the first place.
  if [[ "$DRY_RUN" == "1" ]]; then
    return 0  # exercise the decision logic, leave no delivery state behind
  fi
  if ! command -v im-notify >/dev/null; then
    # Deliberately loud, and deliberately no marker: retry on every tick.
    # A health checker that cannot deliver is not a health checker, and
    # silence here would look identical to "all clear".
    printf '[alert] DELIVERY FAILED (im-notify not on PATH): %s\n' "$key" >&2
    return 1
  fi
  local sent=1
  if [[ "$severity" == "page" ]]; then
    im-notify --alert "ai-radar server: $message" && sent=0
  else
    im-notify "ai-radar server: $message" && sent=0
  fi
  if (( sent == 0 )); then
    : > "$marker"
  else
    printf '[alert] DELIVERY FAILED (im-notify returned non-zero): %s\n' "$key" >&2
    return 1
  fi
}

resolve() {
  local key="$1"
  local marker="$STATE_DIR/$key.firing"
  if [[ -f "$marker" ]]; then
    rm -f "$marker"
    printf '[alert] resolved %s\n' "$key"
    if [[ "$DRY_RUN" != "1" ]] && command -v im-notify >/dev/null; then
      im-notify "ai-radar server: recovered — $key" || true
    fi
  fi
}

active_port() {
  # Same fail-closed shape as the apply script's copy: grep exits 2 on a
  # missing file, and under `set -e` that kills the checker before the
  # "no active upstream" alert branch can ever run -- the one state this
  # check most needs to report would be the one state it never reports.
  [[ -r "$ACTIVE_UPSTREAM_CONF" ]] || { echo ""; return 0; }
  grep -oE '127\.0\.0\.1:[0-9]+' "$ACTIVE_UPSTREAM_CONF" 2>/dev/null | head -1 | cut -d: -f2 || true
}

check_serve() {
  local port; port="$(active_port)"
  if [[ -z "$port" ]]; then
    notify serve page "no active upstream configured ($ACTIVE_UPSTREAM_CONF)"; return
  fi
  if systemctl is-active --quiet "ai-radar-serve@$port.service"; then
    resolve serve
  else
    notify serve page "serve@$port is not active"
  fi
}

check_healthz() {
  local port; port="$(active_port)"
  [[ -n "$port" ]] || return 0  # already covered by check_serve
  if curl -sf -m 10 "http://127.0.0.1:$port/api/v1/healthz" >/dev/null 2>&1; then
    resolve healthz
  else
    notify healthz page "healthz did not return success on port $port"
  fi
}

check_disk() {
  local free_gb
  free_gb="$(df -BG --output=avail "$AI_RADAR_HOME" 2>/dev/null | tail -1 | tr -dc '0-9')"
  if [[ -z "$free_gb" ]]; then
    notify disk notice "could not determine free space for $AI_RADAR_HOME"; return
  fi
  if (( free_gb < DISK_MIN_FREE_GB )); then
    notify disk page "only ${free_gb}G free; a database sync needs headroom"
  else
    resolve disk
  fi
}

check_sync_freshness() {
  if [[ ! -r "$RECEIPT" ]]; then
    notify sync notice "no accepted snapshot receipt yet ($RECEIPT)"; return
  fi
  local age_min
  age_min=$(( ( $(date +%s) - $(stat -c %Y "$RECEIPT") ) / 60 ))
  if (( age_min > FRESHNESS_MAX_AGE_MIN )); then
    notify sync page "replica has not been updated for ${age_min}m (limit ${FRESHNESS_MAX_AGE_MIN}m)"
  else
    resolve sync
  fi
}

check_deploy_failed() {
  local marker="${AI_RADAR_DEPLOY_FAILED_MARKER:-$AI_RADAR_HOME/data/.deploy-failed}"
  local journal="${AI_RADAR_CODE_JOURNAL:-$AI_RADAR_HOME/data/code-deploy-journal.json}"
  if [[ -f "$marker" ]]; then
    notify deploy page "last code deploy failed: $(head -c 160 "$marker")"
    return
  fi
  # A deploy killed (SIGKILL/power loss) writes no marker; the journal is the
  # only trace. ANY non-idle state left older than a normal deploy means an
  # interrupted deploy no push has reconciled -- page regardless of which phase
  # (promoting/activating/serving), since each leaves a real inconsistency
  # (half-updated tree, un-recorded serving version, pending receipt). Normal
  # deploys pass through all of them in seconds.
  if [[ -f "$journal" ]] \
     && grep -qE '"state": *"(promoting|activating|serving)"' "$journal" 2>/dev/null; then
    local age_min state
    age_min=$(( ( $(date +%s) - $(stat -c %Y "$journal") ) / 60 ))
    state="$(grep -oE '"state": *"[a-z]+"' "$journal" | grep -oE '[a-z]+"$' | tr -d '"')"
    if (( age_min > 15 )); then
      notify deploy page "code deploy stuck in '${state}' for ${age_min}m (interrupted mid-deploy)"
      return
    fi
  fi
  resolve deploy
}

check_serve
check_healthz
check_disk
check_sync_freshness
check_deploy_failed
