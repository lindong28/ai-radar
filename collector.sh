#!/usr/bin/env bash
# Independently scheduled collection, with its own lock and no AI stages.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/pipeline.sh" --collect-only
