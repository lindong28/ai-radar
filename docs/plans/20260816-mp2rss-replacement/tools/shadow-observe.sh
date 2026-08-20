#!/usr/bin/env bash
# Shadow-comparison sampler for the Mp2RSS replacement evaluation.
# Non-interactive shell: PATH and proxy come from the repo's gitignored .env,
# exactly as pipeline.sh resolves them.
set -uo pipefail
REPO="/Users/lindong/research/ai-radar"
cd "$REPO" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
[[ -f "$REPO/.env" ]] && source "$REPO/.env"
if [[ -n "${AI_RADAR_PROXY_FILE:-}" && -r "$AI_RADAR_PROXY_FILE" ]]; then
  p="$(tr -d '[:space:]' <"$AI_RADAR_PROXY_FILE")"
  [[ -n "$p" ]] && export HTTP_PROXY="$p" HTTPS_PROXY="$p" http_proxy="$p" https_proxy="$p" \
    NO_PROXY="${AI_RADAR_NO_PROXY:-localhost,127.0.0.1,::1}" no_proxy="${AI_RADAR_NO_PROXY:-localhost,127.0.0.1,::1}"
fi
LOG="$REPO/plans/20260816-mp2rss-replacement/evidence/shadow-observe.log"
echo "[$(date -Iseconds)] observe start" >>"$LOG"
uv run python "$REPO/plans/20260816-mp2rss-replacement/tools/shadow_compare.py" observe >>"$LOG" 2>&1
echo "[$(date -Iseconds)] observe exit=$?" >>"$LOG"
