#!/usr/bin/env bash
# Install the AI Radar server-side services (Linux/systemd).
#
# The macOS side keeps its own launchd layer in install.sh; the two hosts have
# different jobs (Mac runs the pipeline, server only serves) and sharing one
# abstraction across launchd and systemd would put the Mac's live pipeline at
# risk for no gain here.
#
# Idempotent: safe to re-run after a code push.
#
# Timers are installed but NOT enabled. Enabling them starts an authority that
# rewrites production state on a schedule; that is a separate, deliberate step
# (see the plan's P3), not a side effect of installing files.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

SERVICES=(serve db-apply alert)

CONFIG_DIR=/etc/ai-radar
SYSTEMD_DIR=/etc/systemd/system
SERVE_PORTS=(8000 8001)

AI_RADAR_USER="${AI_RADAR_USER:-$(id -un)}"
AI_RADAR_HOME="${AI_RADAR_HOME:-$REPO_ROOT}"
AI_RADAR_LOG_DIR="${AI_RADAR_LOG_DIR:-$AI_RADAR_HOME/logs}"
AI_RADAR_LOCAL_BIN="${AI_RADAR_LOCAL_BIN:-$HOME/.local/bin}"
AI_RADAR_UV="${AI_RADAR_UV:-$(command -v uv || echo "$HOME/.local/bin/uv")}"

log() { printf '%s\n' "$*"; }
fail() { printf '✗ %s\n' "$*" >&2; exit 1; }

require_linux_systemd() {
  [[ "$(uname -s)" == "Linux" ]] || fail "this installer targets Linux; the Mac uses ./install.sh"
  command -v systemctl >/dev/null || fail "systemctl not found"
}

preflight() {
  [[ -x "$AI_RADAR_UV" ]] || fail "uv not found at '$AI_RADAR_UV' (set AI_RADAR_UV)"
  [[ -d "$AI_RADAR_HOME/src/airadar" ]] || fail "AI_RADAR_HOME '$AI_RADAR_HOME' does not look like the repo"
  id "$AI_RADAR_USER" >/dev/null 2>&1 || fail "user '$AI_RADAR_USER' does not exist"
  sudo -n true 2>/dev/null || fail "passwordless sudo is required to write $SYSTEMD_DIR"
}

# Substitution happens here rather than in the unit files because systemd
# expands neither User= nor the ExecStart executable path, and EnvironmentFile
# values are read without any ${VAR} expansion. Keeping the tokens in tracked
# templates preserves reproducibility; only the substitution is host-local.
#
# Python string replacement, not sed: a path containing '|' or '&' would break
# a sed expression or expand to the matched text. Rendered to a temp file and
# installed atomically -- streaming through `sudo tee` opens (truncates) the
# destination before the pipeline can fail, which can leave a live unit empty.
render_unit() {
  local src="$1" dest="$2"
  local tmp; tmp="$(mktemp)"
  AI_RADAR_USER="$AI_RADAR_USER" AI_RADAR_HOME="$AI_RADAR_HOME" \
  AI_RADAR_LOG_DIR="$AI_RADAR_LOG_DIR" AI_RADAR_LOCAL_BIN="$AI_RADAR_LOCAL_BIN" \
  AI_RADAR_UV="$AI_RADAR_UV" \
  python3 - "$src" "$tmp" <<'PY'
import os, re, sys
src, dest = sys.argv[1], sys.argv[2]
text = open(src, encoding="utf-8").read()
for token in ("AI_RADAR_USER", "AI_RADAR_HOME", "AI_RADAR_LOG_DIR",
              "AI_RADAR_LOCAL_BIN", "AI_RADAR_UV"):
    text = text.replace(f"@{token}@", os.environ[token])
leftover = re.findall(r"@[A-Z_]+@", text)
if leftover:
    sys.exit(f"unsubstituted placeholders: {leftover}")
open(dest, "w", encoding="utf-8").write(text)
PY
  # Same-directory temp + rename, not `install` straight onto the target:
  # install copies into the destination, so an interruption mid-copy leaves a
  # truncated live unit. A rename within one filesystem cannot be observed
  # half-done.
  sudo cp "$tmp" "$dest.tmp.$$"
  sudo chmod 0644 "$dest.tmp.$$"
  sudo mv -f "$dest.tmp.$$" "$dest"
  rm -f "$tmp"
}

ensure_config() {
  sudo mkdir -p "$CONFIG_DIR/slots"
  mkdir -p "$AI_RADAR_LOG_DIR"

  if [[ ! -f "$CONFIG_DIR/server.env" ]]; then
    # Created empty rather than pre-filled: the values are deployment secrets
    # and identity, and a placeholder that looks configured is worse than an
    # obviously missing file.
    sudo tee "$CONFIG_DIR/server.env" >/dev/null <<'EOF'
# AI Radar server configuration. Values here are read by every service.
# AI_RADAR_SITE_DOMAIN=news.aiplanet.live
# AI_RADAR_ICP_BEIAN=
EOF
    log "created $CONFIG_DIR/server.env (fill in before starting serve)"
  fi

  for port in "${SERVE_PORTS[@]}"; do
    local slot="$CONFIG_DIR/slots/$port.env"
    if [[ ! -f "$slot" ]]; then
      sudo tee "$slot" >/dev/null <<EOF
# Release descriptor for the slot listening on $port.
# Each slot points at its own database file so a candidate can be verified
# while the other slot keeps serving the active one.
AI_RADAR_DB=$AI_RADAR_HOME/data/radar-$port.db
EOF
      log "created $slot"
    fi
  done
}

install_units() {
  render_unit "$REPO_ROOT/deploy/systemd/ai-radar-serve@.service" "$SYSTEMD_DIR/ai-radar-serve@.service"
  for name in db-apply alert; do
    render_unit "$REPO_ROOT/deploy/systemd/ai-radar-$name.service" "$SYSTEMD_DIR/ai-radar-$name.service"
    sudo cp "$REPO_ROOT/deploy/systemd/ai-radar-$name.timer" "$SYSTEMD_DIR/ai-radar-$name.timer.tmp.$$"
    sudo chmod 0644 "$SYSTEMD_DIR/ai-radar-$name.timer.tmp.$$"
    sudo mv -f "$SYSTEMD_DIR/ai-radar-$name.timer.tmp.$$" "$SYSTEMD_DIR/ai-radar-$name.timer"
  done
  sudo systemctl daemon-reload
  log "units installed: ${SERVICES[*]}"
  log "timers installed but NOT enabled; enable them explicitly when the schedule is decided"
}

ensure_active_upstream() {
  # Which slot is live. The REAL file lives under data/ where the app user can
  # replace it atomically without root; /etc/nginx/conf.d gets a root-installed
  # SYMLINK to it (nginx follows symlinks on include). Writing /etc directly
  # from the apply step would need root there -- and the apply runs as the app
  # user, so its first switch would die on PermissionError after journalling
  # `switching`, wedging the state machine at the same point every round.
  local real="$AI_RADAR_HOME/data/nginx/ai-radar-active-upstream.conf"
  local link=/etc/nginx/conf.d/ai-radar-active-upstream.conf
  mkdir -p "$(dirname "$real")"
  if [[ ! -f "$real" ]]; then
    printf 'upstream ai_radar_active { server 127.0.0.1:%s; }\n' "${SERVE_PORTS[0]}" > "$real"
    log "created $real (initial active slot: ${SERVE_PORTS[0]})"
  fi
  if [[ "$(readlink "$link" 2>/dev/null)" != "$real" ]]; then
    sudo ln -sfn "$real" "$link"
    log "linked $link -> $real"
  fi
  # nginx's root-run master must be able to traverse into the data dir.
  chmod o+x "$AI_RADAR_HOME" "$AI_RADAR_HOME/data" "$(dirname "$real")" 2>/dev/null || true
}

main() {
  require_linux_systemd
  preflight
  ensure_config
  install_units
  ensure_active_upstream
  log "done. next: fill $CONFIG_DIR/server.env, then start a serve slot:"
  log "  sudo systemctl enable --now ai-radar-serve@${SERVE_PORTS[0]}"
}

main "$@"
