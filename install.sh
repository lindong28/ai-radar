#!/usr/bin/env bash
# Install ai-radar services. Idempotent.
# Usage: ./install.sh              # all services
#        ./install.sh <service>    # serve | tunnel | pipeline | alert | performance-probe | orbstack | cost-report

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
# shellcheck source=deploy/lib/services.sh
source "$SCRIPT_DIR/deploy/lib/services.sh"

resolve_services "$@"

is_install_dry_run() {
  [[ "${AI_RADAR_INSTALL_DRY_RUN:-0}" == "1" ]]
}

restore_previous_launch_agent_file() {
  local slug="$1"
  local destination="$2"
  local previous_kind="$3"
  local backup_file="$4"
  local legacy_link_target="$5"
  local generated_plist="$6"
  case "$previous_kind" in
    regular)
      place_launch_agent_file "$slug" "$backup_file" "$destination"
      ;;
    legacy-symlink)
      replace_file_from_snapshot "$backup_file" "$generated_plist" \
        && restore_legacy_launch_agent_symlink "$slug" "$legacy_link_target" "$destination"
      ;;
    absent)
      remove_owned_launch_agent_file "$slug" "$destination"
      ;;
    *)
      echo "✗ $slug: unknown previous LaunchAgent state: $previous_kind" >&2
      return 1
      ;;
  esac
}

# Known limitation: these single-operator deployment commands intentionally do
# not serialize a truly concurrent install + uninstall. Adding a custom mutex
# would reintroduce the crash/liveness failure class removed by U16.
install_launchd_service() {
  local slug="$1"
  local label plist_name plist_path destination
  local launchd_status=0
  local had_previous=0
  local was_loaded=0
  local loaded_path_kind=""
  local backup_file=""
  local previous_kind="absent"
  local legacy_link_target=""
  label="$(service_label "$slug")"
  plist_name="$(service_plist_name "$slug")"
  plist_path="$REPO_ROOT/deploy/launchd/$plist_name"

  if is_install_dry_run; then
    echo "dry-run: $slug: would install launchd service + owned LaunchAgent file (label=$label)"
    return 0
  fi

  validate_user_home || return 1
  destination="$(service_launch_agent_path "$slug")" || return 1
  mkdir -p "$REPO_ROOT/logs" || return 1
  validate_launch_agent_destination "$slug" "$destination" || return 1
  if legacy_launch_agent_symlink_owned "$slug" "$destination"; then
    had_previous=1
    previous_kind="legacy-symlink"
    legacy_link_target="$(readlink "$destination")" || return 1
  elif launch_agent_file_owned "$slug" "$destination"; then
    had_previous=1
    previous_kind="regular"
  fi

  # An owned destination entry is the local authority for claiming a loaded
  # label. launchctl reports only the resolved source path, so a generated path
  # without this entry cannot prove that ai-radar installed the job.
  if [[ "$had_previous" -ne 1 ]]; then
    if is_launchd_loaded "$label"; then
      echo "✗ $slug: label is occupied without an owned LaunchAgent destination; refusing to evict it" >&2
      return 1
    else
      launchd_status=$?
      if [[ "$launchd_status" -ne 1 ]]; then
        echo "✗ $slug: launchd query failed: $LAUNCHD_QUERY_ERROR" >&2
        return 1
      fi
    fi
  else
    # Identity and the rollback snapshot must describe the pre-install disk
    # state. In particular, a legacy destination symlink exposes any early
    # ensure_plist rewrite immediately to the loaded job's source path.
    if is_launchd_loaded "$label" "$destination" "$plist_path"; then
      launchd_status=0
      was_loaded=1
      loaded_path_kind="$LAUNCHD_LOADED_PATH_KIND"
    else
      launchd_status=$?
      case "$launchd_status" in
        1) ;;
        3)
          echo "✗ $slug: foreign launchd job uses this label: $LAUNCHD_QUERY_ERROR" >&2
          return 1
          ;;
        *)
          echo "✗ $slug: launchd query failed: $LAUNCHD_QUERY_ERROR" >&2
          return 1
          ;;
      esac
    fi
  fi

  if [[ "$had_previous" -eq 1 ]]; then
    backup_file="$(mktemp "${TMPDIR:-/tmp}/ai-radar-launch-agent-backup.XXXXXX")" || return 1
    chmod 0600 "$backup_file" || {
      rm -f "$backup_file"
      return 1
    }
    if ! snapshot_owned_launch_agent_file "$slug" "$destination" "$backup_file"; then
      rm -f "$backup_file"
      return 1
    fi
  fi

  if ! ensure_plist "$plist_name"; then
    [[ -z "$backup_file" ]] || rm -f "$backup_file"
    return 1
  fi

  if ! place_launch_agent_file "$slug" "$plist_path" "$destination"; then
    if ! restore_previous_launch_agent_file \
      "$slug" "$destination" "$previous_kind" "$backup_file" \
      "$legacy_link_target" "$plist_path"; then
      echo "✗ $slug: placement failed and previous LaunchAgent state could not be restored" >&2
    fi
    [[ -z "$backup_file" ]] || rm -f "$backup_file"
    echo "✗ $slug: failed to place owned LaunchAgent file" >&2
    return 1
  fi

  if [[ "$was_loaded" -eq 1 ]]; then
    if [[ "$loaded_path_kind" == "generated" ]]; then
      echo "  $slug: completing interrupted migration from generated plist to destination"
    else
      # launchd exposes the plist path but not a digest of its in-memory
      # snapshot. Re-bootstrap even when the path is current so a previous
      # SIGKILL after atomic placement cannot be mistaken for a no-op install.
      echo "  $slug: reloading loaded job to reconcile its in-memory snapshot"
    fi
    if ! launchctl bootout "gui/$UID/$label"; then
      if ! restore_previous_launch_agent_file \
        "$slug" "$destination" "$previous_kind" "$backup_file" \
        "$legacy_link_target" "$plist_path"; then
        echo "✗ $slug: launchctl bootout failed and previous LaunchAgent file could not be restored" >&2
        [[ -z "$backup_file" ]] || rm -f "$backup_file"
        return 1
      fi
      [[ -z "$backup_file" ]] || rm -f "$backup_file"
      echo "✗ $slug: launchctl bootout failed while reloading (label=$label)" >&2
      return 1
    fi
  fi

  if ! launchctl bootstrap "gui/$UID" "$destination"; then
    if is_launchd_loaded "$label" "$destination" "$plist_path"; then
      if ! launchctl bootout "gui/$UID/$label"; then
        echo "✗ $slug: failed bootstrap left a job loaded, and rollback bootout failed; keeping matching LaunchAgent file" >&2
        [[ -z "$backup_file" ]] || rm -f "$backup_file"
        return 1
      fi
    else
      launchd_status=$?
      if [[ "$launchd_status" -ne 1 ]]; then
        echo "✗ $slug: bootstrap failed and launchd rollback state is ambiguous: $LAUNCHD_QUERY_ERROR" >&2
        [[ -z "$backup_file" ]] || rm -f "$backup_file"
        return 1
      fi
    fi
    if ! restore_previous_launch_agent_file \
      "$slug" "$destination" "$previous_kind" "$backup_file" \
      "$legacy_link_target" "$plist_path"; then
      echo "✗ $slug: new bootstrap failed and previous LaunchAgent file could not be restored" >&2
      [[ -z "$backup_file" ]] || rm -f "$backup_file"
      return 1
    fi
    if [[ "$was_loaded" -eq 1 ]]; then
      if ! launchctl bootstrap "gui/$UID" "$destination"; then
        echo "✗ $slug: new bootstrap failed; previous launchd job could not be restored" >&2
        [[ -z "$backup_file" ]] || rm -f "$backup_file"
        return 1
      fi
      if ! launchctl enable "gui/$UID/$label"; then
        echo "✗ $slug: restored job bootstrap succeeded, but launchctl enable failed" >&2
        [[ -z "$backup_file" ]] || rm -f "$backup_file"
        return 1
      fi
      if ! launchctl kickstart -k "gui/$UID/$label"; then
        echo "✗ $slug: restored job bootstrap succeeded, but launchctl kickstart failed" >&2
        [[ -z "$backup_file" ]] || rm -f "$backup_file"
        return 1
      fi
      echo "✗ $slug: new bootstrap failed; restored previous launchd job" >&2
    else
      echo "✗ $slug: launchctl bootstrap failed; restored previous LaunchAgent file state" >&2
    fi
    [[ -z "$backup_file" ]] || rm -f "$backup_file"
    return 1
  fi

  if ! launchctl enable "gui/$UID/$label"; then
    [[ -z "$backup_file" ]] || rm -f "$backup_file"
    echo "✗ $slug: launchctl enable failed; job remains bootstrapped from $destination" >&2
    return 1
  fi
  if ! launchctl kickstart -k "gui/$UID/$label"; then
    [[ -z "$backup_file" ]] || rm -f "$backup_file"
    echo "✗ $slug: launchctl kickstart failed; job remains bootstrapped from $destination" >&2
    return 1
  fi
  [[ -z "$backup_file" ]] || rm -f "$backup_file"
  echo "✓ $slug: bootstrapped + started (label=$label)"
}

install_pipeline() {
  if is_install_dry_run; then
    echo "dry-run: pipeline: would install in user crontab (every 15 min)"
    return 0
  fi

  if crontab -l 2>/dev/null | grep -q "ai-radar/pipeline.sh"; then
    echo "✓ pipeline: already in crontab"
    return 0
  fi
  local entry
  entry="$(sed "s|/path/to/ai-radar|$REPO_ROOT|g" "$REPO_ROOT/deploy/cron/ai-radar-pipeline")"
  (crontab -l 2>/dev/null || true; echo ""; echo "$entry") | crontab -
  echo "✓ pipeline: installed in user crontab (every 15 min)"
}

install_cost_report() {
  if is_install_dry_run; then
    echo "dry-run: cost-report: would install in user crontab (Monday 09:17)"
    return 0
  fi
  local entry current filtered
  entry="$(sed -e "s|/path/to/ai-radar|$REPO_ROOT|g" -e "s|/path/to/home|$HOME|g" "$REPO_ROOT/deploy/cron/ai-radar-cost-report")"
  current="$(crontab -l 2>/dev/null || true)"
  filtered="$(printf '%s\n' "$current" | grep -v '^# Send the AI Radar LLM cost report' | grep -v '# ai-radar-cost-report$' || true)"
  printf '%s\n%s\n' "$filtered" "$entry" | crontab -
  echo "✓ cost-report: installed in user crontab (Monday 09:17)"
}

INSTALLED_SERVICES=()
SKIPPED_SERVICE_SUMMARY=()

for slug in "${SELECTED_SERVICES[@]}"; do
  if ! ensure_install_dependency "$slug"; then
    SKIPPED_SERVICE_SUMMARY+=("$slug: $SERVICE_DEPENDENCY_SKIP_REASON")
    continue
  fi

  case "$slug" in
    serve|tunnel|alert|performance-probe|orbstack) install_launchd_service "$slug" ;;
    pipeline)          install_pipeline ;;
    cost-report)       install_cost_report ;;
  esac
  INSTALLED_SERVICES+=("$slug")
done

echo
echo "Install summary:"
if [[ "${#INSTALLED_SERVICES[@]}" -eq 0 ]]; then
  echo "  installed: (none)"
else
  echo "  installed: ${INSTALLED_SERVICES[*]}"
fi
if [[ "${#SKIPPED_SERVICE_SUMMARY[@]}" -eq 0 ]]; then
  echo "  skipped: (none)"
else
  echo "  skipped:"
  for item in "${SKIPPED_SERVICE_SUMMARY[@]}"; do
    echo "    - $item"
  done
fi

cat <<EOF

Verify with: ./status.sh
Logs:        logs/serve-access.log, logs/alert-check.log, logs/cost-report-cron.log, logs/performance-probe-launchd.{log,err.log}, /tmp/ai-radar-tunnel.{log,err}, logs/pipeline-*.log
EOF
