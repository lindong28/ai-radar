#!/usr/bin/env bash
# Run ai-radar user-facing browser verify checks for local phases.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AI_RADAR_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$AI_RADAR_ROOT"
exec uv run python -m pytest -v tests/playwright "$@"
