#!/usr/bin/env bash
# Cron entry point for sync-db-to-server.sh. Meant to be wrapped by
# run-or-alert in the crontab so any non-zero exit here pages once (with
# dedup) and a later success re-arms the alert. run-or-alert's dedup identity
# includes the exit code, so each failure class below uses a distinct code —
# a streak that changes cause (agent lost -> replica stale) re-alerts instead
# of being swallowed by the previous firing.
#
#   exit 2  no usable ssh-agent / cannot derive the sync key fingerprint
#   exit 3  sync-db-to-server.sh itself failed
#   exit 4  upload fine, but the server's accepted-snapshot receipt is stale
#   exit 5  upload fine, but the receipt has been unreadable for several
#           consecutive runs -- staleness detection is effectively blind
#
# Two problems this wrapper owns, both invisible to the sync script itself:
#
#   ssh-agent discovery   cron runs a non-interactive shell with no
#                         SSH_AUTH_SOCK, and the sync key is passphrase-
#                         protected: authentication only works through the
#                         logged-in user's ssh-agent. macOS launchd exposes
#                         that agent at /var/run/com.apple.launchd.*/Listeners,
#                         so probe each candidate socket for one that holds a
#                         key ssh would offer this host (by fingerprint, any
#                         of the host's resolved IdentityFiles). This fails
#                         after a reboot until the user logs in again; the
#                         failure exits non-zero so run-or-alert reports it,
#                         and the next scheduled run recovers.
#
#   apply-side freshness  sync-db-to-server.sh hands off to the server's
#                         apply step with --no-block and exits 0, so a server
#                         that keeps rejecting snapshots would look healthy
#                         from here. Before syncing, check the age of the
#                         server's accepted-snapshot receipt (computed on the
#                         server's own clock, so host clock skew is
#                         irrelevant): a confirmed age beyond two sync
#                         intervals plus slack means the previous cycle never
#                         got accepted, and this run must say so even if its
#                         own upload succeeds. An UNREADABLE receipt is not
#                         evidence of staleness: one hiccup only logs (a truly
#                         unreachable server also fails the sync itself, which
#                         does alert), but consecutively unreadable runs mean
#                         staleness detection is blind and exit 5 says so.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
LOG_FILE="$REPO_ROOT/logs/sync-cron.log"
mkdir -p "$REPO_ROOT/logs"

SERVER="${AI_RADAR_SYNC_SERVER:-tencent-webserver-china}"
REMOTE_RECEIPT="${AI_RADAR_SYNC_REMOTE_RECEIPT:-ai-radar/data/accepted-snapshot.json}"
# Cron cadence is 5h; two missed cycles plus an hour of slack. Detection of a
# rejecting server therefore lags: the age crosses this threshold and is then
# noticed at the next cron sample -- worst case threshold + one cadence
# (~16h), documented in ADR-013.
FRESHNESS_MAX_AGE_MIN="${AI_RADAR_SYNC_FRESHNESS_MAX_AGE_MIN:-660}"
SSH_OPTS="${AI_RADAR_SYNC_SSH_OPTS:--o ProxyCommand=none} -o BatchMode=yes"
LOG_MAX_BYTES="${AI_RADAR_SYNC_LOG_MAX_BYTES:-5242880}"

# Keep the log bounded: trim to the newest quarter once it exceeds the cap.
# Skip while a sync holds its lock -- rotating the file under a writer that
# still has the old inode open would silently lose its remaining output.
SYNC_LOCK_DIR="${AI_RADAR_SYNC_LOCK:-$REPO_ROOT/data/.sync.lock}"
if [[ -f "$LOG_FILE" && ! -d "$SYNC_LOCK_DIR" ]] \
   && (( $(stat -f %z "$LOG_FILE" 2>/dev/null || echo 0) > LOG_MAX_BYTES )); then
  tail -c $(( LOG_MAX_BYTES / 4 )) "$LOG_FILE" > "$LOG_FILE.trim" && mv "$LOG_FILE.trim" "$LOG_FILE"
fi

log() { printf '[%s] %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*" >>"$LOG_FILE"; }

fail() {
  local code=$1; shift
  log "FAIL($code): $*"
  printf 'ai-radar db-sync: %s\n' "$*" >&2
  exit "$code"
}

resolve_target_key_shas() {
  if [[ -n "${AI_RADAR_SYNC_KEY_SHA:-}" ]]; then
    printf '%s\n' "$AI_RADAR_SYNC_KEY_SHA"
    return 0
  fi
  # Neutral default: fingerprints of every identity ssh itself would offer
  # this host, from the resolved client config. ssh tries them in turn, so
  # accept a match on any of them.
  local identity
  while IFS= read -r identity; do
    identity="${identity/#\~/$HOME}"
    [[ -f "$identity.pub" ]] || continue
    ssh-keygen -lf "$identity.pub" 2>/dev/null | awk '{print $2}' || true
  done < <(ssh -G "$SERVER" 2>/dev/null | awk '/^identityfile /{print $2}')
  return 0
}

TARGET_KEY_SHAS="$(resolve_target_key_shas)"
[[ -n "$TARGET_KEY_SHAS" ]] \
  || fail 2 "cannot determine any sync key fingerprint (set AI_RADAR_SYNC_KEY_SHA or configure an IdentityFile with a .pub for $SERVER)"

agent_has_target_key() {
  local listing
  listing="$(SSH_AUTH_SOCK="$1" ssh-add -l 2>/dev/null)" || return 1
  grep -qF -f <(printf '%s\n' "$TARGET_KEY_SHAS") <<<"$listing"
}

if [[ -z "${SSH_AUTH_SOCK:-}" ]] || ! agent_has_target_key "$SSH_AUTH_SOCK"; then
  found=""
  for sock in /var/run/com.apple.launchd.*/Listeners; do
    [[ -S "$sock" ]] || continue
    if agent_has_target_key "$sock"; then
      found="$sock"
      break
    fi
  done
  [[ -n "$found" ]] || fail 2 "no ssh-agent holding a sync key for $SERVER; log in to reload the agent"
  export SSH_AUTH_SOCK="$found"
fi

# Freshness pre-check. Age is computed remotely against the server's clock.
# One unreadable receipt is not evidence of staleness and must not page; but
# consecutively unreadable receipts mean staleness detection is blind, which
# deserves its own alert (exit 5) rather than an indefinite quiet NOTE.
UNKNOWN_STRIKES_FILE="$REPO_ROOT/logs/.sync-receipt-unknown-strikes"
UNKNOWN_STRIKES_MAX="${AI_RADAR_SYNC_UNKNOWN_STRIKES_MAX:-3}"
stale_reason=""
unknown_reason=""
age_min="unknown"
if remote_age_sec="$(ssh $SSH_OPTS "$SERVER" "echo \$(( \$(date +%s) - \$(stat -c %Y '$REMOTE_RECEIPT') ))" 2>/dev/null)" \
   && [[ "$remote_age_sec" =~ ^[0-9]+$ ]]; then
  rm -f "$UNKNOWN_STRIKES_FILE"
  age_min=$(( remote_age_sec / 60 ))
  if (( age_min > FRESHNESS_MAX_AGE_MIN )); then
    stale_reason="the serving replica last accepted a snapshot ${age_min}min ago (max ${FRESHNESS_MAX_AGE_MIN}min): news.aiplanet.live is serving stale data"
  fi
else
  strikes=$(( $(cat "$UNKNOWN_STRIKES_FILE" 2>/dev/null || echo 0) + 1 ))
  printf '%s\n' "$strikes" > "$UNKNOWN_STRIKES_FILE"
  log "NOTE: could not read receipt age from $SERVER:$REMOTE_RECEIPT (strike $strikes/$UNKNOWN_STRIKES_MAX); continuing"
  if (( strikes >= UNKNOWN_STRIKES_MAX )); then
    unknown_reason="the receipt at $SERVER:$REMOTE_RECEIPT has been unreadable for $strikes consecutive runs: staleness detection is blind"
  fi
fi
[[ -n "$stale_reason" ]] && log "STALE: $stale_reason"

log "sync start (receipt age ${age_min}min)"
if "$SCRIPT_DIR/sync-db-to-server.sh" >>"$LOG_FILE" 2>&1; then
  log "sync OK"
else
  code=$?
  fail 3 "db sync to $SERVER failed (exit $code)${stale_reason:+ and $stale_reason}; the site keeps serving the last accepted snapshot and will go stale if this persists. First step: run $SCRIPT_DIR/sync-db-cron.sh by hand and read $LOG_FILE"
fi

if [[ -n "$stale_reason" ]]; then
  fail 4 "upload succeeded but $stale_reason. First step: ssh $SERVER sudo journalctl -u ai-radar-db-apply -n 50"
fi
if [[ -n "$unknown_reason" ]]; then
  fail 5 "upload succeeded but $unknown_reason. First step: ssh $SERVER ls -l '$REMOTE_RECEIPT'"
fi
