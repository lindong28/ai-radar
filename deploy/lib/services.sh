# shellcheck shell=bash
# Service registry + helpers. Source from install.sh / uninstall.sh / status.sh.
# No side effects on source.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

ALL_SERVICES=(serve tunnel pipeline wewe)

service_label() {
  case "$1" in
    serve)    echo "live.aiplanet.ai-radar.serve" ;;
    tunnel)   echo "live.aiplanet.ai-radar.tunnel" ;;
    wewe)     echo "live.aiplanet.ai-radar.wewe" ;;
    pipeline) echo "" ;;
    *) return 1 ;;
  esac
}

service_plist_name() {
  case "$1" in
    serve)  echo "ai-radar-serve.plist" ;;
    tunnel) echo "ai-radar-tunnel.plist" ;;
    wewe)   echo "ai-radar-wewe.plist" ;;
    pipeline) echo "" ;;
    *) return 1 ;;
  esac
}

service_desc() {
  case "$1" in
    serve)    echo "FastAPI web server on :8000" ;;
    tunnel)   echo "Cloudflare tunnel to aiplanet.live" ;;
    wewe)     echo "WeWe RSS bridge on :4000 (WeChat ingestion)" ;;
    pipeline) echo "Incremental fetch/score/enrich/curate (cron, 15 min)" ;;
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

# Generate deploy/launchd/<name>.plist from <name>.plist.example, replacing
# the placeholder path with REPO_ROOT. Idempotent: skips if local plist exists.
ensure_plist() {
  local name="$1"
  local example="$REPO_ROOT/deploy/launchd/${name}.example"
  local target="$REPO_ROOT/deploy/launchd/${name}"
  [[ -f "$example" ]] || { echo "✗ template missing: $example" >&2; return 1; }
  [[ -f "$target" ]] && return 0
  sed "s|/path/to/ai-radar|$REPO_ROOT|g" "$example" > "$target"
  echo "  generated $target from .example"
}

is_launchd_loaded() {
  launchctl print "gui/$UID/$1" >/dev/null 2>&1
}

launchd_pid() {
  launchctl list 2>/dev/null | awk -v lbl="$1" '$3==lbl {print $1; exit}'
}

# Make sure Docker daemon is up. Returns 0 if reachable, 1 otherwise. Tries
# to start OrbStack if it's installed and not running. Used by wewe install.
ensure_docker_daemon() {
  docker info >/dev/null 2>&1 && return 0
  if command -v orbctl >/dev/null 2>&1; then
    echo "  Docker daemon not reachable; starting OrbStack..."
    open -a OrbStack 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8; do
      sleep 2
      docker info >/dev/null 2>&1 && return 0
    done
  fi
  echo "✗ Docker daemon still unreachable. Start your Docker provider (OrbStack / Docker Desktop) first." >&2
  return 1
}
