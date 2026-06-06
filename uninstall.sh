#!/usr/bin/env bash
# Uninstall ai-radar services. Idempotent and tolerant — silent if not installed.
# Keeps source, plist files, logs, and data. Only unregisters from supervisors.
# Usage: ./uninstall.sh              # all services
#        ./uninstall.sh <service>    # serve | tunnel | pipeline | alert

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
# shellcheck source=deploy/lib/services.sh
source "$SCRIPT_DIR/deploy/lib/services.sh"

resolve_services "$@"

uninstall_launchd_service() {
  local slug="$1"
  local label
  label="$(service_label "$slug")"

  if is_launchd_loaded "$label"; then
    launchctl bootout "gui/$UID/$label" 2>/dev/null || true
    echo "✓ $slug: unloaded from launchd (label=$label)"
  else
    echo "  $slug: nothing to remove (not loaded)"
  fi
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

for slug in "${SELECTED_SERVICES[@]}"; do
  case "$slug" in
    serve|tunnel|alert) uninstall_launchd_service "$slug" ;;
    pipeline)          uninstall_pipeline ;;
  esac
done
