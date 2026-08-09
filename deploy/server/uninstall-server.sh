#!/usr/bin/env bash
# Remove the AI Radar server services.
#
# Leaves /etc/ai-radar and the database alone: those are state, not units, and
# an uninstall that silently discarded the replica would turn a routine
# teardown into a re-sync of several GB.
set -euo pipefail

SERVICES=(serve db-apply alert)

SYSTEMD_DIR=/etc/systemd/system
CONFIG_DIR=/etc/ai-radar
SERVE_PORTS=(8000 8001)

log() { printf '%s\n' "$*"; }
fail() { printf '✗ %s\n' "$*" >&2; exit 1; }

command -v systemctl >/dev/null || fail "systemctl not found"
sudo -n true 2>/dev/null || fail "passwordless sudo is required"

for port in "${SERVE_PORTS[@]}"; do
  unit="ai-radar-serve@$port.service"
  sudo systemctl disable --now "$unit" 2>/dev/null || true
  log "removed $unit"
done
sudo rm -f "$SYSTEMD_DIR/ai-radar-serve@.service"

for name in db-apply alert; do
  sudo systemctl disable --now "ai-radar-$name.timer" 2>/dev/null || true
  sudo systemctl disable --now "ai-radar-$name.service" 2>/dev/null || true
  sudo rm -f "$SYSTEMD_DIR/ai-radar-$name.service" "$SYSTEMD_DIR/ai-radar-$name.timer"
  log "removed ai-radar-$name"
done

sudo systemctl daemon-reload
log "units for: ${SERVICES[*]} removed"
log "kept: $CONFIG_DIR and the database (state, not units)"
