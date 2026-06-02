#!/usr/bin/env bash
# Install ai-radar services. Idempotent.
# Usage: ./install.sh              # all services
#        ./install.sh <service>    # serve | tunnel | pipeline | wewe | alert

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
# shellcheck source=deploy/lib/services.sh
source "$SCRIPT_DIR/deploy/lib/services.sh"

resolve_services "$@"

install_launchd_service() {
  local slug="$1"
  local label plist_name plist_path
  label="$(service_label "$slug")"
  plist_name="$(service_plist_name "$slug")"
  plist_path="$REPO_ROOT/deploy/launchd/$plist_name"

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
  if crontab -l 2>/dev/null | grep -q "ai-radar/pipeline.sh"; then
    echo "✓ pipeline: already in crontab"
    return 0
  fi
  local entry
  entry="$(cat "$REPO_ROOT/deploy/cron/ai-radar-pipeline")"
  (crontab -l 2>/dev/null; echo ""; echo "$entry") | crontab -
  echo "✓ pipeline: installed in user crontab (every 15 min)"
}

# wewe prerequisite: Docker daemon must be reachable.
needs_docker=0
for slug in "${SELECTED_SERVICES[@]}"; do
  [[ "$slug" == "wewe" ]] && needs_docker=1
done
if [[ "$needs_docker" -eq 1 ]]; then
  ensure_docker_daemon || exit 1
fi

for slug in "${SELECTED_SERVICES[@]}"; do
  case "$slug" in
    serve|tunnel|wewe|alert) install_launchd_service "$slug" ;;
    pipeline)          install_pipeline ;;
  esac
done

cat <<EOF

Verify with: ./status.sh
Logs:        logs/serve-access.log, logs/alert-check.log, /tmp/ai-radar-{tunnel,wewe}.{log,err}, logs/pipeline-*.log
EOF
