# shellcheck shell=bash
# Service registry + helpers. Source from install.sh / uninstall.sh / status.sh.
# No side effects on source.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

ALL_SERVICES=(serve tunnel pipeline alert)
LLM_PROVIDER_ENV_KEYS=(DEEPSEEK_API_KEY ARK_API_KEY OPENAI_API_KEY GLM_API_KEY)
LLM_PROVIDER_ENV_KEYS_TEXT="DEEPSEEK_API_KEY, ARK_API_KEY, OPENAI_API_KEY, GLM_API_KEY"
SERVICE_DEPENDENCY_SKIP_REASON=""

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
    tunnel)   echo "Cloudflare tunnel to your public domain" ;;
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

append_runtime_env_value() {
  local key="$1"
  local value="$2"
  local env_file="$REPO_ROOT/.env"
  touch "$env_file"
  printf "\n%s=%s\n" "$key" "$value" >> "$env_file"
}

llm_provider_key_present() {
  local key
  for key in "${LLM_PROVIDER_ENV_KEYS[@]}"; do
    if runtime_env_value "$key" >/dev/null; then
      return 0
    fi
  done
  return 1
}

alert_webhook_missing_keys() {
  local key index
  local -a missing=()
  for key in FEISHU_GENERAL_ALERT_WEBHOOK FEISHU_GENERAL_NOTIFICATION_WEBHOOK; do
    if ! runtime_env_value "$key" >/dev/null; then
      missing+=("$key")
    fi
  done
  if [[ ${#missing[@]} -eq 0 ]]; then
    return 1
  fi
  printf "%s" "${missing[0]}"
  for ((index = 1; index < ${#missing[@]}; index++)); do
    printf ", %s" "${missing[$index]}"
  done
  printf "\n"
}

service_dependency_missing_reason() {
  local slug="$1"
  case "$slug" in
    serve)
      return 1
      ;;
    pipeline)
      if llm_provider_key_present; then
        return 1
      fi
      echo "missing one of $LLM_PROVIDER_ENV_KEYS_TEXT"
      return 0
      ;;
    alert)
      local missing_webhooks
      if ! missing_webhooks="$(alert_webhook_missing_keys)"; then
        return 1
      fi
      echo "missing $missing_webhooks"
      return 0
      ;;
    tunnel)
      if [[ -f "$REPO_ROOT/deploy/cloudflared/config.yml" ]]; then
        return 1
      fi
      echo "missing deploy/cloudflared/config.yml"
      return 0
      ;;
    *)
      validate_service "$slug" >/dev/null
      ;;
  esac
}

ensure_install_dependency() {
  local slug="$1"
  local reason key value index
  local -a pending_keys=()
  local -a pending_values=()
  SERVICE_DEPENDENCY_SKIP_REASON=""

  if ! reason="$(service_dependency_missing_reason "$slug")"; then
    return 0
  fi

  if [[ "$slug" == "tunnel" ]]; then
    SERVICE_DEPENDENCY_SKIP_REASON="$reason; create it from deploy/cloudflared/config.yml.example and re-run ./install.sh tunnel"
    echo "⚠ $slug: $SERVICE_DEPENDENCY_SKIP_REASON; skipping." >&2
    return 1
  fi

  case "$slug" in
    pipeline) key="DEEPSEEK_API_KEY" ;;
    alert)
      if [[ ! -t 0 ]]; then
        SERVICE_DEPENDENCY_SKIP_REASON="$reason; stdin is not a TTY"
        echo "⚠ $slug: $SERVICE_DEPENDENCY_SKIP_REASON; skipping." >&2
        return 1
      fi
      echo "⚠ $slug: $reason." >&2
      for key in FEISHU_GENERAL_ALERT_WEBHOOK FEISHU_GENERAL_NOTIFICATION_WEBHOOK; do
        runtime_env_value "$key" >/dev/null && continue
        read -r -p "Enter $key to save to ./.env and install $slug, or press Enter to skip: " value
        if [[ -z "$value" ]]; then
          SERVICE_DEPENDENCY_SKIP_REASON="$reason; user skipped prompt"
          echo "⚠ $slug: $SERVICE_DEPENDENCY_SKIP_REASON; skipping." >&2
          return 1
        fi
        pending_keys+=("$key")
        pending_values+=("$value")
      done
      for ((index = 0; index < ${#pending_keys[@]}; index++)); do
        key="${pending_keys[$index]}"
        value="${pending_values[$index]}"
        append_runtime_env_value "$key" "$value"
        export "$key=$value"
        echo "✓ $slug: saved $key to ./.env"
      done
      return 0
      ;;
    *)
      SERVICE_DEPENDENCY_SKIP_REASON="$reason"
      echo "⚠ $slug: $SERVICE_DEPENDENCY_SKIP_REASON; skipping." >&2
      return 1
      ;;
  esac

  if [[ ! -t 0 ]]; then
    SERVICE_DEPENDENCY_SKIP_REASON="$reason; stdin is not a TTY"
    echo "⚠ $slug: $SERVICE_DEPENDENCY_SKIP_REASON; skipping." >&2
    return 1
  fi

  echo "⚠ $slug: $reason." >&2
  read -r -p "Enter $key to save to ./.env and install $slug, or press Enter to skip: " value
  if [[ -z "$value" ]]; then
    SERVICE_DEPENDENCY_SKIP_REASON="$reason; user skipped prompt"
    echo "⚠ $slug: $SERVICE_DEPENDENCY_SKIP_REASON; skipping." >&2
    return 1
  fi

  append_runtime_env_value "$key" "$value"
  export "$key=$value"
  echo "✓ $slug: saved $key to ./.env"
  return 0
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
  local alert_webhook notification_webhook db_path missing_webhooks
  if missing_webhooks="$(alert_webhook_missing_keys)"; then
    echo "✗ alert: missing $missing_webhooks in process env, .env, or ~/.claude/.env; refusing partial launchd configuration." >&2
    return 1
  fi
  alert_webhook="$(runtime_env_value FEISHU_GENERAL_ALERT_WEBHOOK)"
  notification_webhook="$(runtime_env_value FEISHU_GENERAL_NOTIFICATION_WEBHOOK)"
  db_path="$(runtime_env_value AI_RADAR_DB || true)"
  cat <<EOF
  <key>EnvironmentVariables</key>
  <dict>
$(alert_environment_entry_xml FEISHU_GENERAL_ALERT_WEBHOOK "$alert_webhook")
$(alert_environment_entry_xml FEISHU_GENERAL_NOTIFICATION_WEBHOOK "$notification_webhook")
$(alert_environment_entry_xml AI_RADAR_DB "$db_path")
  </dict>
EOF
}

# Generate deploy/launchd/<name>.plist from <name>.plist.example, replacing
# the placeholder path with REPO_ROOT. Idempotent except alert, whose local
# plist is regenerated so both Feishu webhook changes are picked up.
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
    environment_xml="$(alert_environment_xml)" || return 1
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

reload_alert_launchd_service() {
  local label="$1"
  # launchd snapshots EnvironmentVariables at bootstrap. Alert installation
  # therefore must replace an already-loaded job after regenerating its plist,
  # otherwise a newly required notification webhook never reaches live state.
  launchctl bootout "gui/$UID/$label"
}

launchd_pid() {
  launchctl list 2>/dev/null | awk -v lbl="$1" '$3==lbl {print $1; exit}'
}
