#!/usr/bin/env bash
# Uninstall ai-radar services. Idempotent and tolerant — silent if not installed.
# Keeps source, plist files, logs, and data. Only unregisters from supervisors.
# Usage: ./uninstall.sh              # all services
#        ./uninstall.sh <service>    # serve | tunnel | pipeline | alert | performance-probe | orbstack | cost-report

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
# shellcheck source=deploy/lib/services.sh
source "$SCRIPT_DIR/deploy/lib/services.sh"

resolve_services "$@"

uninstall_launchd_service() {
  local slug="$1"
  local label
  local launchd_status
  local bootout_error
  local destination plist_path
  validate_user_home || return 1
  label="$(service_label "$slug")"
  destination="$(service_launch_agent_path "$slug")" || return 1
  plist_path="$REPO_ROOT/deploy/launchd/$(service_plist_name "$slug")"
  validate_launch_agent_destination "$slug" "$destination" || return 1
  if ! launch_agent_destination_owned "$slug" "$destination"; then
    echo "  $slug: nothing to remove (not installed)"
    return 0
  fi

  if is_launchd_loaded "$label" "$destination" "$plist_path"; then
    if ! bootout_error="$(launchctl bootout "gui/$UID/$label" 2>&1)"; then
      echo "✗ $slug: launchctl bootout failed${bootout_error:+: $bootout_error}" >&2
      return 1
    fi
    if is_launchd_loaded "$label" "$destination" "$plist_path"; then
      echo "✗ $slug: launchd job is still loaded (label=$label)" >&2
      return 1
    else
      launchd_status=$?
      if [[ "$launchd_status" -eq 3 ]]; then
        echo "✗ $slug: a foreign launchd job appeared after bootout: $LAUNCHD_QUERY_ERROR" >&2
        return 1
      fi
      if [[ "$launchd_status" -ne 1 ]]; then
        echo "✗ $slug: launchd query failed after bootout: $LAUNCHD_QUERY_ERROR" >&2
        return 1
      fi
    fi
    echo "✓ $slug: unloaded from launchd (label=$label)"
  else
    launchd_status=$?
    if [[ "$launchd_status" -eq 3 ]]; then
      echo "✗ $slug: foreign launchd job uses this label: $LAUNCHD_QUERY_ERROR" >&2
      return 1
    fi
    if [[ "$launchd_status" -ne 1 ]]; then
      echo "✗ $slug: launchd query failed: $LAUNCHD_QUERY_ERROR" >&2
      return 1
    fi
    echo "  $slug: nothing to remove (not loaded)"
  fi
  remove_owned_launch_agent_file "$slug" "$destination"
}

uninstall_pipeline() {
  if crontab -l 2>/dev/null | grep -q "ai-radar/pipeline.sh"; then
    crontab -l 2>/dev/null \
      | grep -v "^# Run the AI Radar incremental pipeline" \
      | grep -v "ai-radar/pipeline.sh" \
      | crontab -
    echo "✓ pipeline: removed from user crontab"
  else
    echo "  pipeline: nothing to remove (not in crontab)"
  fi
}

uninstall_cost_report() {
  local current filtered
  current="$(crontab -l 2>/dev/null || true)"
  if printf '%s\n' "$current" | grep -q '# ai-radar-cost-report$'; then
    filtered="$(printf '%s\n' "$current" | grep -v '^# Send the AI Radar LLM cost report' | grep -v '# ai-radar-cost-report$' || true)"
    printf '%s\n' "$filtered" | crontab -
    echo "✓ cost-report: removed from user crontab"
  else
    echo "  cost-report: nothing to remove (not in crontab)"
  fi
}

for slug in "${SELECTED_SERVICES[@]}"; do
  case "$slug" in
    serve|tunnel|alert|performance-probe|orbstack) uninstall_launchd_service "$slug" ;;
    pipeline)          uninstall_pipeline ;;
    cost-report)       uninstall_cost_report ;;
  esac
done
