#!/usr/bin/env bash
# Run all devkit tests: backend (pytest) then frontend (vitest)
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== Backend tests ==="
cd "$SCRIPT_DIR"
uv run pytest -q

echo ""
echo "=== Frontend tests ==="
cd "$SCRIPT_DIR/frontend"
npx vitest run

echo ""
echo "All devkit tests passed."
