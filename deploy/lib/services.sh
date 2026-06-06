# shellcheck shell=bash
# Service registry + helpers. Source from install.sh / uninstall.sh / status.sh.
# No side effects on source.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

ALL_SERVICES=(serve tunnel pipeline alert)

service_label() {
  case "$1" in
    serve)    echo "live.aiplanet.ai-radar.serve" ;;
    tunnel)   echo "live.aiplanet.ai-radar.tunnel" ;;
    alert)    echo "live.aiplanet.ai-radar.alert" ;;
    pipeline) echo "" ;;
    *) return 1 ;;
  esac
}

service_plist_name() {
  case "$1" in
    serve)  echo "ai-radar-serve.plist" ;;
    tunnel) echo "ai-radar-tunnel.plist" ;;
    alert)  echo "ai-radar-alert.plist" ;;
    pipeline) echo "" ;;
    *) return 1 ;;
  esac
}

service_desc() {
  case "$1" in
    serve)    echo "FastAPI web server on :8000" ;;
    tunnel)   echo "Cloudflare tunnel to aiplanet.live" ;;
    alert)    echo "Monitoring alert check (launchd, 5 min)" ;;
    pipeline) echo "Incremental fetch/score/enrich/curate (cron, 15 min)" ;;
    *) return 1 ;;
  esac
}

validate_service() {
  local s="$1"
  for valid in "${ALL_SERVICES[@]}"; do
    [[ "$s" == "$valid" ]] && return 0
  done
  echo "Error: unknown service '$s'. Valid: ${ALL_SERVICES[*]}" >&2
  return 1
}

# Parse CLI args → fills SELECTED_SERVICES (global). No args = all services.
resolve_services() {
  if [[ $# -eq 0 ]]; then
    SELECTED_SERVICES=("${ALL_SERVICES[@]}")
  else
    SELECTED_SERVICES=()
    for s in "$@"; do
      validate_service "$s" || return 1
      SELECTED_SERVICES+=("$s")
    done
  fi
}

runtime_env_value() {
  local key="$1"
  local value="${!key:-}"
  if [[ -n "$value" ]]; then
    printf "%s" "$value"
    return 0
  fi

  local env_file line raw
  for env_file in "$REPO_ROOT/.env" "$HOME/.claude/.env"; do
    [[ -f "$env_file" ]] || continue
    line="$(grep -E "^[[:space:]]*(export[[:space:]]+)?${key}=" "$env_file" 2>/dev/null | tail -n 1 || true)"
    [[ -n "$line" ]] || continue
    raw="${line#*=}"
    raw="${raw%$'\r'}"
    raw="${raw%\"}"
    raw="${raw#\"}"
    raw="${raw%\'}"
    raw="${raw#\'}"
    if [[ -n "$raw" ]]; then
      printf "%s" "$raw"
      return 0
    fi
  done

  return 1
}

xml_escape() {
  local value="$1"
  value="${value//&/&amp;}"
  value="${value//</&lt;}"
  value="${value//>/&gt;}"
  value="${value//\"/&quot;}"
  value="${value//\'/&apos;}"
  printf "%s" "$value"
}

alert_environment_entry_xml() {
  local key="$1"
  local value="$2"
  local escaped
  [[ -n "$value" ]] || return 0
  escaped="$(xml_escape "$value")"
  printf "    <key>%s</key><string>%s</string>\n" "$key" "$escaped"
}

alert_environment_xml() {
  local webhook db_path
  webhook="$(runtime_env_value AI_RADAR_FEISHU_WEBHOOK || true)"
  if [[ -z "$webhook" ]]; then
    echo "⚠ alert: AI_RADAR_FEISHU_WEBHOOK not found in process env, .env, or ~/.claude/.env; launchd alert will dry-run only." >&2
    return 0
  fi
  db_path="$(runtime_env_value AI_RADAR_DB || true)"
  cat <<EOF
  <key>EnvironmentVariables</key>
  <dict>
$(alert_environment_entry_xml AI_RADAR_FEISHU_WEBHOOK "$webhook")
$(alert_environment_entry_xml AI_RADAR_DB "$db_path")
  </dict>
EOF
}

# Generate deploy/launchd/<name>.plist from <name>.plist.example, replacing
# the placeholder path with REPO_ROOT. Idempotent except alert, whose local
# plist is regenerated so AI_RADAR_FEISHU_WEBHOOK changes are picked up.
ensure_plist() {
  local name="$1"
  local example="$REPO_ROOT/deploy/launchd/${name}.example"
  local target="$REPO_ROOT/deploy/launchd/${name}"
  [[ -f "$example" ]] || { echo "✗ template missing: $example" >&2; return 1; }
  if [[ "$name" != "ai-radar-alert.plist" && -f "$target" ]]; then
    return 0
  fi
  local environment_xml
  environment_xml=""
  if [[ "$name" == "ai-radar-alert.plist" ]]; then
    environment_xml="$(alert_environment_xml)"
  fi
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ "$line" == "  <!-- __AI_RADAR_ALERT_ENVIRONMENT__ -->" ]]; then
      if [[ -n "$environment_xml" ]]; then
        printf "%s\n" "$environment_xml"
      fi
      continue
    fi
    printf "%s\n" "$line"
  done < <(sed "s|/path/to/ai-radar|$REPO_ROOT|g" "$example") > "$target"
  echo "  generated $target from .example"
}

is_launchd_loaded() {
  launchctl print "gui/$UID/$1" >/dev/null 2>&1
}

launchd_pid() {
  launchctl list 2>/dev/null | awk -v lbl="$1" '$3==lbl {print $1; exit}'
}
