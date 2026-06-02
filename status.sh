#!/usr/bin/env bash
# Read-only service status panel. Never modifies state.
# Usage: ./status.sh              # all services
#        ./status.sh <service>    # serve | tunnel | pipeline | wewe | alert

set -uo pipefail  # no -e: status must report failures, not abort on them
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
# shellcheck source=deploy/lib/services.sh
source "$SCRIPT_DIR/deploy/lib/services.sh"

resolve_services "$@" || exit 1

status_launchd_service() {
  local slug="$1"
  local label pid
  label="$(service_label "$slug")"

  printf "%-9s | " "$slug"
  if is_launchd_loaded "$label"; then
    pid="$(launchd_pid "$label")"
    if [[ -n "${pid:-}" && "$pid" != "-" ]]; then
      printf "loaded ✓ pid=%-6s" "$pid"
    else
      printf "loaded ✓ (no pid)   "
    fi
  else
    printf "not installed     "
  fi

  # Service-specific extras
  case "$slug" in
    wewe)
      printf " | "
      local cstatus
      cstatus="$(docker ps --filter "name=ai-radar-wewe-rss" --format "{{.Status}}" 2>/dev/null | head -1)"
      if [[ -n "$cstatus" ]] && [[ "$cstatus" == Up* ]]; then
        printf "container ✓ %s" "$cstatus"
      elif command -v docker >/dev/null 2>&1 && ! docker info >/dev/null 2>&1; then
        printf "docker daemon down"
      else
        printf "container ✗"
      fi
      printf " | log /tmp/ai-radar-wewe.err"
      ;;
    serve)  printf " | log logs/serve-access.log" ;;
    tunnel) printf " | log /tmp/ai-radar-tunnel.err" ;;
    alert)  printf " | log logs/alert-check.log" ;;
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

for slug in "${SELECTED_SERVICES[@]}"; do
  case "$slug" in
    serve|tunnel|wewe|alert) status_launchd_service "$slug" ;;
    pipeline)          status_pipeline ;;
  esac
done
