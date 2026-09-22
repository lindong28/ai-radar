#!/usr/bin/env bash
set -euo pipefail
RADAR_ENGINE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
export PYTHONPATH="${RADAR_ENGINE_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"
exec uv run --project "${RADAR_ENGINE_ROOT}" python -m airadar.interpret.engine.summarizer "$@"
