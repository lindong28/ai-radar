#!/usr/bin/env bash
# One line per service, plus what is actually being served.
#
# The two release facts are reported separately and neither overwrites the
# other: a failed sync attempt must not replace the timestamp of the snapshot
# currently being served, or the display would answer "when did we last try?"
# while looking like it answers "what is live right now?".
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

SERVICES=(serve db-apply alert)

CONFIG_DIR=/etc/ai-radar
AI_RADAR_HOME="${AI_RADAR_HOME:-$REPO_ROOT}"
SERVE_PORTS=(8000 8001)

line() { printf '%-22s %s\n' "$1" "$2"; }

unit_state() {
  local unit="$1"
  if ! systemctl list-unit-files "$unit" >/dev/null 2>&1; then
    echo "not installed"; return
  fi
  local active enabled pid
  # is-enabled prints its state ("disabled", "static", ...) AND exits non-zero
  # for anything not enabled. `cmd || echo -` would therefore emit two lines
  # for every disabled unit -- and freshly installed timers are disabled by
  # design here -- breaking the one-line-per-service contract this display
  # exists to keep.
  active="$(systemctl is-active "$unit" 2>/dev/null || true)"
  enabled="$(systemctl is-enabled "$unit" 2>/dev/null || true)"
  pid="$(systemctl show -p MainPID --value "$unit" 2>/dev/null || true)"
  if [[ "$pid" != "0" && -n "$pid" ]]; then
    echo "${active:-unknown} (${enabled:-unknown}) pid=$pid"
  else
    echo "${active:-unknown} (${enabled:-unknown})"
  fi
}

echo "== services =="
for port in "${SERVE_PORTS[@]}"; do
  line "serve@$port" "$(unit_state "ai-radar-serve@$port.service")"
done
for name in db-apply alert; do
  line "$name" "$(unit_state "ai-radar-$name.service")"
  line "$name.timer" "$(unit_state "ai-radar-$name.timer")"
done

echo
echo "== active release =="
active_conf="$AI_RADAR_HOME/data/nginx/ai-radar-active-upstream.conf"
if [[ -r "$active_conf" ]]; then
  line "nginx upstream" "$(grep -oE '127\.0\.0\.1:[0-9]+' "$active_conf" | head -1 || echo unknown)"
else
  line "nginx upstream" "not configured"
fi

sha_file="$AI_RADAR_HOME/.deployed-sha"
line "deployed sha" "$([[ -r $sha_file ]] && cat "$sha_file" || echo unknown)"

# The snapshot currently being served. Deliberately sourced from the accepted
# receipt, not from the newest file on disk: a snapshot that arrived but failed
# verification is present without ever having been served.
receipt="$AI_RADAR_HOME/data/accepted-snapshot.json"
if [[ -r "$receipt" ]]; then
  line "serving snapshot" "$(python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));print(d.get("snapshot_id","?"), d.get("completed_at","?"))' "$receipt" 2>/dev/null || echo "unreadable")"
else
  line "serving snapshot" "no accepted snapshot yet"
fi

# Reported as its own line so a degraded sync is visible without displacing the
# fact above.
last_attempt="$(systemctl show -p ExecMainExitTimestamp --value ai-radar-db-apply.service 2>/dev/null || true)"
last_result="$(systemctl show -p Result --value ai-radar-db-apply.service 2>/dev/null || true)"
if [[ -n "$last_attempt" ]]; then
  if [[ "$last_result" == "success" ]]; then
    line "last sync attempt" "$last_attempt (ok)"
  else
    line "last sync attempt" "$last_attempt (DEGRADED: ${last_result:-unknown})"
  fi
fi

echo
echo "== config =="
for f in "$CONFIG_DIR/server.env" "$CONFIG_DIR/slots"/*.env; do
  [[ -e "$f" ]] || continue
  # Key names only. These files hold deployment identity; echoing values into a
  # terminal or a log is not something that can be undone.
  # grep -c prints its count even when it is 0 (exiting 1), so `|| echo 0`
  # would print a second number; `|| true` keeps the count singular.
  line "$(basename "$f")" "$(grep -cE '^[A-Z_]+=' "$f" 2>/dev/null || true) keys"
done
