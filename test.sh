#!/usr/bin/env bash
# Run the ai-radar test suite.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

exec uv run python -m pytest tests -v "$@"
