#!/usr/bin/env bash
# FRIDAY — Run script
# Loads .env, sets library paths, then starts the assistant.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="$(dirname "$SCRIPT_DIR")"

if [ ! -f "$BASE/.env" ]; then
    echo "[ERROR] .env not found. Run: ./scripts/setup.sh"
    exit 1
fi

if [ ! -f "$BASE/bin/assistant" ]; then
    echo "[ERROR] Binary not built. Run: ./scripts/setup.sh"
    exit 1
fi

# Load .env into current shell environment
set -a
# shellcheck disable=SC1091
source "$BASE/.env"
set +a

# Make Piper and Picovoice shared libs visible at runtime
export LD_LIBRARY_PATH="$BASE/lib:$BASE/piper:$LD_LIBRARY_PATH"

echo "Starting FRIDAY from $BASE..."
exec "$BASE/bin/assistant"
