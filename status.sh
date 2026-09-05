#!/usr/bin/env bash
# Read-only service status panel. Never modifies state.
# Usage: ./status.sh              # all services
#        ./status.sh <service>    # serve | tunnel | pipeline | alert | performance-probe | orbstack | cost-report

set -uo pipefail  # no -e: status must report failures, not abort on them
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
# shellcheck source=deploy/lib/services.sh
source "$SCRIPT_DIR/deploy/lib/services.sh"

resolve_services "$@" || exit 1

status_launchd_service() {
  local slug="$1"
  local label pid launchd_status destination plist_path
  local file_present=0
  local file_owned=0
  label="$(service_label "$slug")"
  destination="$(service_launch_agent_path "$slug")" || {
    printf "%-9s | status unavailable (unsafe HOME)\n" "$slug"
    return 1
  }
  plist_path="$REPO_ROOT/deploy/launchd/$(service_plist_name "$slug")"
  if [[ -e "$destination" || -L "$destination" ]]; then
    file_present=1
    if launch_agent_file_owned "$slug" "$destination" \
      || legacy_launch_agent_symlink_owned "$slug" "$destination"; then
      file_owned=1
    fi
  fi

  printf "%-9s | " "$slug"
  if [[ "$file_present" -eq 0 ]]; then
    printf "not installed     "
  elif is_launchd_loaded "$label" "$destination" "$plist_path"; then
    if [[ "$file_owned" -eq 1 ]]; then
      if [[ "$LAUNCHD_LOADED_PATH_KIND" == "generated" ]]; then
        printf "loaded ✓ migration pending"
      else
        pid="$(launchd_pid "$label")"
        if [[ -n "${pid:-}" && "$pid" != "-" ]]; then
          printf "loaded ✓ pid=%-6s" "$pid"
        else
          printf "loaded ✓ (no pid)   "
        fi
      fi
    else
      printf "foreign job/file ⚠ "
    fi
  else
    launchd_status=$?
    case "$launchd_status" in
      1)
        if [[ "$file_owned" -eq 1 ]]; then
          printf "installed, not loaded"
        elif [[ "$file_present" -eq 1 ]]; then
          printf "foreign file ⚠    "
        else
          printf "not installed     "
        fi
        ;;
      3) printf "foreign job ⚠     " ;;
      *) printf "status unavailable" ;;
    esac
  fi

  # Service-specific extras
  case "$slug" in
    serve)  printf " | log logs/serve-access.log" ;;
    tunnel) printf " | log /tmp/ai-radar-tunnel.err" ;;
    alert)  printf " | log logs/alert-check.log" ;;
    performance-probe) printf " | log logs/performance-probe-launchd.log" ;;
    orbstack) printf " | log /tmp/ai-radar-orbstack.log" ;;
  esac
  echo
}

status_pipeline() {
  printf "%-9s | " "pipeline"
  if crontab -l 2>/dev/null | grep -q "ai-radar/pipeline.sh"; then
    printf "in crontab ✓      "
  else
    printf "not installed     "
  fi

  local latest
  latest="$(find "$SCRIPT_DIR/logs" -maxdepth 1 -name 'pipeline-*.log' 2>/dev/null | sort -r | head -1)"
  if [[ -n "$latest" ]]; then
    printf " | last log: %s" "$(basename "$latest")"
  else
    printf " | last log: none"
  fi
  echo
}

status_cost_report() {
  local count
  count="$(crontab -l 2>/dev/null | grep -c '# ai-radar-cost-report$' || true)"
  printf "%-17s | " "cost-report"
  if [[ "$count" -eq 1 ]]; then
    printf "in crontab ✓ Monday 09:17 | log logs/cost-report-cron.log"
  elif [[ "$count" -eq 0 ]]; then
    printf "not installed"
  else
    printf "duplicate entries ⚠ count=%s" "$count"
  fi
  echo
}

for slug in "${SELECTED_SERVICES[@]}"; do
  case "$slug" in
    serve|tunnel|alert|performance-probe|orbstack) status_launchd_service "$slug" ;;
    pipeline)          status_pipeline ;;
    cost-report)       status_cost_report ;;
  esac
done
