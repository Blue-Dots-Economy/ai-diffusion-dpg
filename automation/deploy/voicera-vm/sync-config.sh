#!/usr/bin/env bash
# Copy the config this deployment mounts out of the repo into ./config.
# Run from a checkout; the result is what you ship to the VM.
#   ./sync-config.sh [domain]        (default: blue-dots)
set -euo pipefail
DOMAIN="${1:-blue-dots}"
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"

mkdir -p "$HERE/config/dpg" "$HERE/config/domain" "$HERE/otelcol"
cp "$REPO/dev-kit/dpg/"*.yaml                 "$HERE/config/dpg/"
cp "$REPO/dev-kit/configs/$DOMAIN/"*.yaml     "$HERE/config/domain/"
cp "$REPO/automation/docker/otelcol/otelcol-config.yaml" "$HERE/otelcol/"

echo "config synced for domain '$DOMAIN':"
echo "  dpg:    $(ls "$HERE/config/dpg" | wc -l | tr -d ' ') files"
echo "  domain: $(ls "$HERE/config/domain" | wc -l | tr -d ' ') files"
grep -rl "yopmail" "$HERE/config/domain" 2>/dev/null \
  && echo "WARNING: local-test yopmail address found in synced config — remove before shipping" || true
