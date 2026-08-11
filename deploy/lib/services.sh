# shellcheck shell=bash
# Service registry + helpers. Source from install.sh / uninstall.sh / status.sh.
# No side effects on source.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

ALL_SERVICES=(serve tunnel pipeline alert performance-probe cost-report)
LLM_PROVIDER_ENV_KEYS=(DEEPSEEK_API_KEY ARK_API_KEY OPENAI_API_KEY GLM_API_KEY)
LLM_PROVIDER_ENV_KEYS_TEXT="DEEPSEEK_API_KEY, ARK_API_KEY, OPENAI_API_KEY, GLM_API_KEY"
SERVICE_DEPENDENCY_SKIP_REASON=""
LAUNCHD_QUERY_ERROR=""
LAUNCHD_LOADED_PATH=""
LAUNCHD_LOADED_PATH_KIND=""
LAUNCH_AGENT_OWNER_MARKER="ai-radar:managed-launch-agent:v1"

service_label() {
  case "$1" in
    serve)    echo "live.aiplanet.ai-radar.serve" ;;
    tunnel)   echo "live.aiplanet.ai-radar.tunnel" ;;
    alert)    echo "live.aiplanet.ai-radar.alert" ;;
    performance-probe) echo "live.aiplanet.ai-radar.performance-probe" ;;
    pipeline|cost-report) echo "" ;;
    *) return 1 ;;
  esac
}

service_plist_name() {
  case "$1" in
    serve)  echo "ai-radar-serve.plist" ;;
    tunnel) echo "ai-radar-tunnel.plist" ;;
    alert)  echo "ai-radar-alert.plist" ;;
    performance-probe) echo "ai-radar-performance-probe.plist" ;;
    pipeline|cost-report) echo "" ;;
    *) return 1 ;;
  esac
}

service_launch_agent_path() {
  local label canonical_home requested_dir canonical_dir
  validate_user_home || return 1
  canonical_home="$(canonicalize_user_path "$HOME")" || return 1
  requested_dir="$canonical_home/Library/LaunchAgents"
  canonical_dir="$(canonicalize_user_path "$requested_dir")" || return 1
  if [[ "$canonical_dir" != "$requested_dir" ]]; then
    echo "✗ LaunchAgents path escapes canonical HOME: $canonical_dir" >&2
    return 1
  fi
  label="$(service_label "$1")"
  [[ -n "$label" ]] || return 1
  printf "%s/%s.plist\n" "$canonical_dir" "$label"
}

normalize_absolute_path() {
  local path="$1"
  [[ "$path" == /* ]] || return 1
  awk -v path="$path" '
    BEGIN {
      depth = 0
      count = split(path, parts, "/")
      for (part_index = 1; part_index <= count; part_index++) {
        if (parts[part_index] == "" || parts[part_index] == ".") {
          continue
        }
        if (parts[part_index] == "..") {
          if (depth > 0) {
            depth--
          }
          continue
        }
        stack[++depth] = parts[part_index]
      }
      printf "/"
      for (part_index = 1; part_index <= depth; part_index++) {
        separator = part_index == 1 ? "" : "/"
        printf "%s%s", separator, stack[part_index]
      }
      printf "\n"
    }
  '
}

canonicalize_user_path() {
  local requested="$1"
  local normalized existing suffix component resolved
  normalized="$(normalize_absolute_path "$requested")" || return 1
  existing="$normalized"
  suffix=""
  while [[ "$existing" != "/" && ! -e "$existing" && ! -L "$existing" ]]; do
    component="$(basename "$existing")"
    suffix="/$component$suffix"
    existing="$(dirname "$existing")"
  done
  resolved="$(realpath "$existing" 2>/dev/null)" || return 1
  normalize_absolute_path "$resolved$suffix"
}

validate_user_home() {
  local canonical_home user_name owner_uid
  if [[ -z "${HOME:-}" || "$HOME" != /* ]]; then
    echo "✗ HOME must be a non-root absolute path for user LaunchAgents" >&2
    return 1
  fi
  canonical_home="$(canonicalize_user_path "$HOME")" || {
    echo "✗ HOME cannot be canonicalized safely" >&2
    return 1
  }
  case "$canonical_home" in
    /Users/*) user_name="${canonical_home#/Users/}" ;;
    /home/*) user_name="${canonical_home#/home/}" ;;
    *)
      echo "✗ HOME is not an allowlisted user home: $canonical_home" >&2
      return 1
      ;;
  esac
  if [[ -z "$user_name" || "$user_name" == */* ]]; then
    echo "✗ HOME is not an allowlisted user home: $canonical_home" >&2
    return 1
  fi
  if [[ ! -d "$canonical_home" ]]; then
    echo "✗ HOME is not an existing user home: $canonical_home" >&2
    return 1
  fi
  if [[ "$(uname -s)" == "Darwin" ]]; then
    owner_uid="$(stat -f '%u' "$canonical_home" 2>/dev/null)" || return 1
  else
    owner_uid="$(stat -c '%u' "$canonical_home" 2>/dev/null)" || return 1
  fi
  if [[ "$owner_uid" != "$UID" ]]; then
    echo "✗ HOME is not owned by the current user: $canonical_home" >&2
    return 1
  fi
}

launch_agent_file_owned() {
  local slug="$1"
  local path="$2"
  local label
  [[ -f "$path" && ! -L "$path" ]] || return 1
  label="$(service_label "$slug")" || return 1
  grep -Fq "<!-- $LAUNCH_AGENT_OWNER_MARKER -->" "$path" \
    && grep -Fq "<key>Label</key><string>$label</string>" "$path"
}

legacy_launch_agent_symlink_owned() {
  local slug="$1"
  local destination="$2"
  local source destination_target source_target
  [[ -L "$destination" ]] || return 1
  source="$REPO_ROOT/deploy/launchd/$(service_plist_name "$slug")"
  destination_target="$(realpath "$destination" 2>/dev/null)" || return 1
  source_target="$(realpath "$source" 2>/dev/null)" || return 1
  [[ "$destination_target" == "$source_target" ]] || return 1
  # Pre-U16 installs linked to this exact repo-generated file before the
  # ownership marker existed. The exact source path is the ownership proof.
  [[ -f "$source" && ! -L "$source" ]]
}

launch_agent_destination_owned() {
  local slug="$1"
  local destination="$2"
  launch_agent_file_owned "$slug" "$destination" \
    || legacy_launch_agent_symlink_owned "$slug" "$destination"
}

validate_launch_agent_destination() {
  local slug="$1"
  local destination="$2"
  if [[ ! -e "$destination" && ! -L "$destination" ]]; then
    return 0
  fi
  if launch_agent_destination_owned "$slug" "$destination"; then
    return 0
  fi
  echo "✗ $slug: LaunchAgent file is not owned by ai-radar: $destination" >&2
  return 1
}

place_launch_agent_file() {
  local slug="$1"
  local source="$2"
  local destination="$3"
  local destination_dir destination_name
  destination_dir="$(dirname "$destination")"
  destination_name="$(basename "$destination")"
  mkdir -p "$destination_dir" || return 1
  # `cd` pins the verified directory object for all basename-relative
  # mutations below. A malicious same-user swap before this cd/pwd check is
  # outside this manual deployment tool's threat model (Phase 3 limitation).
  (
    local actual_dir temp_name
    cd "$destination_dir" || return 1
    actual_dir="$(pwd -P)" || return 1
    if [[ "$actual_dir" != "$destination_dir" ]]; then
      echo "✗ $slug: LaunchAgents directory changed before placement: $actual_dir" >&2
      return 1
    fi
    validate_launch_agent_destination "$slug" "$destination_name" || return 1
    if [[ -f "$destination_name" && ! -L "$destination_name" ]] \
      && cmp -s "$source" "$destination_name"; then
      echo "  $slug: LaunchAgent file already current"
      return 0
    fi
    temp_name="$(mktemp ".ai-radar-launch-agent.XXXXXX")" || return 1
    if ! cp "$source" "$temp_name" || ! chmod 0600 "$temp_name"; then
      rm -f "$temp_name"
      return 1
    fi
    validate_launch_agent_destination "$slug" "$destination_name" || {
      rm -f "$temp_name"
      return 1
    }
    if ! mv -f "$temp_name" "$destination_name"; then
      rm -f "$temp_name"
      return 1
    fi
    echo "  placed $destination"
  )
}

remove_owned_launch_agent_file() {
  local slug="$1"
  local destination="$2"
  local destination_dir destination_name
  destination_dir="$(dirname "$destination")"
  destination_name="$(basename "$destination")"
  if [[ ! -d "$destination_dir" ]]; then
    echo "  $slug: no LaunchAgent file to remove"
    return 0
  fi
  (
    local actual_dir
    cd "$destination_dir" || return 1
    actual_dir="$(pwd -P)" || return 1
    if [[ "$actual_dir" != "$destination_dir" ]]; then
      echo "✗ $slug: LaunchAgents directory changed before removal: $actual_dir" >&2
      return 1
    fi
    if [[ ! -e "$destination_name" && ! -L "$destination_name" ]]; then
      echo "  $slug: no LaunchAgent file to remove"
      return 0
    fi
    validate_launch_agent_destination "$slug" "$destination_name" || return 1
    launch_agent_file_owned "$slug" "$destination_name" \
      || legacy_launch_agent_symlink_owned "$slug" "$destination_name" \
      || {
      echo "✗ $slug: LaunchAgent ownership changed before removal" >&2
      return 1
    }
    rm "$destination_name" || return 1
    echo "✓ $slug: removed LaunchAgent file"
  )
}

snapshot_owned_launch_agent_file() {
  local slug="$1"
  local destination="$2"
  local snapshot="$3"
  local destination_dir destination_name
  destination_dir="$(dirname "$destination")"
  destination_name="$(basename "$destination")"
  (
    local actual_dir
    cd "$destination_dir" || return 1
    actual_dir="$(pwd -P)" || return 1
    if [[ "$actual_dir" != "$destination_dir" ]]; then
      echo "✗ $slug: LaunchAgents directory changed before snapshot: $actual_dir" >&2
      return 1
    fi
    validate_launch_agent_destination "$slug" "$destination_name" || return 1
    cp "$destination_name" "$snapshot" && chmod 0600 "$snapshot"
  )
}

replace_file_from_snapshot() {
  local source="$1"
  local destination="$2"
  local destination_dir destination_name
  destination_dir="$(dirname "$destination")"
  destination_name="$(basename "$destination")"
  (
    local actual_dir temp_name
    cd "$destination_dir" || return 1
    actual_dir="$(pwd -P)" || return 1
    if [[ "$actual_dir" != "$destination_dir" ]]; then
      echo "✗ file directory changed before restore: $actual_dir" >&2
      return 1
    fi
    temp_name="$(mktemp ".ai-radar-file-restore.XXXXXX")" || return 1
    if ! cp "$source" "$temp_name" || ! chmod 0600 "$temp_name"; then
      rm -f "$temp_name"
      return 1
    fi
    if ! mv -f "$temp_name" "$destination_name"; then
      rm -f "$temp_name"
      return 1
    fi
  )
}

restore_legacy_launch_agent_symlink() {
  local slug="$1"
  local link_target="$2"
  local destination="$3"
  local destination_dir destination_name
  destination_dir="$(dirname "$destination")"
  destination_name="$(basename "$destination")"
  (
    local actual_dir temp_name
    cd "$destination_dir" || return 1
    actual_dir="$(pwd -P)" || return 1
    if [[ "$actual_dir" != "$destination_dir" ]]; then
      echo "✗ $slug: LaunchAgents directory changed before symlink restore: $actual_dir" >&2
      return 1
    fi
    validate_launch_agent_destination "$slug" "$destination_name" || return 1
    temp_name=".ai-radar-launch-agent-symlink.$$.$RANDOM"
    if ! ln -s "$link_target" "$temp_name"; then
      return 1
    fi
    if ! mv -f "$temp_name" "$destination_name"; then
      rm -f "$temp_name"
      return 1
    fi
  )
}

service_desc() {
  case "$1" in
    serve)    echo "FastAPI web server on :8000" ;;
    tunnel)   echo "Cloudflare tunnel to your public domain" ;;
    alert)    echo "Monitoring alert check (launchd, 5 min)" ;;
    performance-probe) echo "Idle-gated performance probe (launchd, 5 min)" ;;
    pipeline) echo "Incremental fetch/score/enrich/curate (cron, 15 min)" ;;
    cost-report) echo "Weekly LLM cost report (cron, Monday 09:17)" ;;
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
    performance-probe)
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
    cost-report)
      if runtime_env_value FEISHU_GENERAL_NOTIFICATION_WEBHOOK >/dev/null; then
        return 1
      fi
      echo "missing FEISHU_GENERAL_NOTIFICATION_WEBHOOK"
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
    alert|cost-report)
      if [[ ! -t 0 ]]; then
        SERVICE_DEPENDENCY_SKIP_REASON="$reason; stdin is not a TTY"
        echo "⚠ $slug: $SERVICE_DEPENDENCY_SKIP_REASON; skipping." >&2
        return 1
      fi
      echo "⚠ $slug: $reason." >&2
      if [[ "$slug" == "alert" ]]; then
        pending_keys=(FEISHU_GENERAL_ALERT_WEBHOOK FEISHU_GENERAL_NOTIFICATION_WEBHOOK)
      else
        pending_keys=(FEISHU_GENERAL_NOTIFICATION_WEBHOOK)
      fi
      local -a required_keys=("${pending_keys[@]}")
      pending_keys=()
      for key in "${required_keys[@]}"; do
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
# the placeholder path with REPO_ROOT. Regenerate on every install so the
# ownership marker and alert environment cannot drift from the tracked template.
ensure_plist() {
  local name="$1"
  local example="$REPO_ROOT/deploy/launchd/${name}.example"
  local target="$REPO_ROOT/deploy/launchd/${name}"
  local target_dir target_name
  [[ -f "$example" ]] || { echo "✗ template missing: $example" >&2; return 1; }
  local environment_xml
  environment_xml=""
  if [[ "$name" == "ai-radar-alert.plist" ]]; then
    environment_xml="$(alert_environment_xml)" || return 1
  fi
  target_dir="$(dirname "$target")"
  target_name="$(basename "$target")"
  (
    local temp_name
    cd "$target_dir" || return 1
    temp_name="$(mktemp ".${name}.XXXXXX")" || return 1
    if ! {
      while IFS= read -r line || [[ -n "$line" ]]; do
        if [[ "$line" == "  <!-- __AI_RADAR_ALERT_ENVIRONMENT__ -->" ]]; then
          if [[ -n "$environment_xml" ]]; then
            printf "%s\n" "$environment_xml"
          fi
          continue
        fi
        printf "%s\n" "$line"
      done < <(sed "s|/path/to/ai-radar|$REPO_ROOT|g" "$example")
    } > "$temp_name"; then
      rm -f "$temp_name"
      return 1
    fi
    if ! chmod 0600 "$temp_name" || ! mv -f "$temp_name" "$target_name"; then
      rm -f "$temp_name"
      return 1
    fi
  ) || return 1
  echo "  generated $target from .example"
}

launchd_loaded_path_from_output() {
  local output="$1"
  local path
  path="$(
    printf "%s\n" "$output" \
      | sed -n 's/^[[:space:]]*path[[:space:]]*=[[:space:]]*//p' \
      | head -n 1
  )"
  path="${path#\"}"
  path="${path%\"}"
  [[ -n "$path" ]] || return 1
  printf "%s\n" "$path"
}

launchd_path_matches_destination() {
  local loaded_path="$1"
  local destination="$2"
  local canonical_loaded canonical_destination
  canonical_loaded="$(realpath "$loaded_path" 2>/dev/null)" || return 1
  canonical_destination="$(realpath "$destination" 2>/dev/null)" || return 1
  [[ "$canonical_loaded" == "$canonical_destination" ]]
}

launchd_reported_path_is_canonical() {
  local reported_path="$1"
  local normalized_reported canonical_reported
  normalized_reported="$(normalize_absolute_path "$reported_path")" || return 1
  canonical_reported="$(realpath "$reported_path" 2>/dev/null)" || return 1
  # launchctl reports the resolved plist source. Reject a fake/foreign alias
  # instead of treating "same inode through another path" as ownership.
  [[ "$normalized_reported" == "$canonical_reported" ]]
}

is_launchd_loaded() {
  local label="$1"
  local expected_path="${2:-}"
  local generated_path="${3:-}"
  local output loaded_path
  local status
  LAUNCHD_QUERY_ERROR=""
  LAUNCHD_LOADED_PATH=""
  LAUNCHD_LOADED_PATH_KIND=""
  if output="$(launchctl print "gui/$UID/$label" 2>&1)"; then
    loaded_path="$(launchd_loaded_path_from_output "$output")" || {
      LAUNCHD_QUERY_ERROR="loaded job has no parseable path"
      return 2
    }
    LAUNCHD_LOADED_PATH="$loaded_path"
    if [[ -n "$expected_path" ]]; then
      # Callers may claim a resolved generated path only after proving that an
      # owned destination entry exists. launchctl discards bootstrap alias
      # provenance; with both an exact foreign label/source and an owned
      # destination present, origin is intentionally a documented limitation.
      if ! launchd_reported_path_is_canonical "$loaded_path"; then
        LAUNCHD_QUERY_ERROR="foreign launchd job path alias: $loaded_path"
        return 3
      elif launchd_path_matches_destination "$loaded_path" "$expected_path"; then
        LAUNCHD_LOADED_PATH_KIND="destination"
      elif [[ -n "$generated_path" && -f "$generated_path" && ! -L "$generated_path" ]] \
        && launchd_path_matches_destination "$loaded_path" "$generated_path"; then
        # An interrupted legacy migration can leave launchd running the old
        # repo-generated source after destination has become a regular file.
        # Both paths are project-owned; callers must reconcile this stale path.
        LAUNCHD_LOADED_PATH_KIND="generated"
      else
        LAUNCHD_QUERY_ERROR="foreign launchd job path: $loaded_path"
        return 3
      fi
    fi
    return 0
  else
    status=$?
  fi
  if [[ "$output" == *"Could not find service"* ]]; then
    return 1
  fi
  LAUNCHD_QUERY_ERROR="exit $status${output:+: $output}"
  return 2
}

launchd_pid() {
  launchctl list 2>/dev/null | awk -v lbl="$1" '$3==lbl {print $1; exit}'
}
