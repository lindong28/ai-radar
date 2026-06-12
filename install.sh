#!/usr/bin/env bash
# Install ai-radar services. Idempotent.
# Usage: ./install.sh              # all services
#        ./install.sh <service>    # serve | tunnel | pipeline | alert

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
# shellcheck source=deploy/lib/services.sh
source "$SCRIPT_DIR/deploy/lib/services.sh"

resolve_services "$@"

is_install_dry_run() {
  [[ "${AI_RADAR_INSTALL_DRY_RUN:-0}" == "1" ]]
}

install_launchd_service() {
  local slug="$1"
  local label plist_name plist_path
  label="$(service_label "$slug")"
  plist_name="$(service_plist_name "$slug")"
  plist_path="$REPO_ROOT/deploy/launchd/$plist_name"

  if is_install_dry_run; then
    echo "dry-run: $slug: would install launchd service (label=$label)"
    return 0
  fi

  ensure_plist "$plist_name"

  if is_launchd_loaded "$label"; then
    echo "✓ $slug: already loaded (label=$label)"
    return 0
  fi

  launchctl bootstrap "gui/$UID" "$plist_path"
  launchctl enable "gui/$UID/$label" 2>/dev/null || true
  launchctl kickstart -k "gui/$UID/$label" >/dev/null 2>&1 || true
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

INSTALLED_SERVICES=()
SKIPPED_SERVICE_SUMMARY=()

for slug in "${SELECTED_SERVICES[@]}"; do
  if ! ensure_install_dependency "$slug"; then
    SKIPPED_SERVICE_SUMMARY+=("$slug: $SERVICE_DEPENDENCY_SKIP_REASON")
    continue
  fi

  case "$slug" in
    serve|tunnel|alert) install_launchd_service "$slug" ;;
    pipeline)          install_pipeline ;;
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
Logs:        logs/serve-access.log, logs/alert-check.log, /tmp/ai-radar-tunnel.{log,err}, logs/pipeline-*.log
EOF
