#!/usr/bin/env bash
#
# automation/caddy/setup.sh
#
# Provisions Caddy as a host-side reverse proxy on the VM that runs the DPG
# stack. Terminates HTTPS for four services via per-host Let's Encrypt certs
# and sslip.io DNS, applies HTTP basic auth on dev-kit (which has effective
# root via the mounted docker socket), and rate-limits the public chat and
# voice endpoints so a single client can't burn the Anthropic budget or amplify
# webhook loops.
#
# Routing:
#   devkit.<ip-dashes>.sslip.io   ->  localhost:8080  (basic auth required)
#   chat.<ip-dashes>.sslip.io     ->  localhost:8005  (open, rate limited)
#   voice.<ip-dashes>.sslip.io    ->  localhost:8006  (open, rate limited)
#   grafana.<ip-dashes>.sslip.io  ->  localhost:3000  (no Caddy auth -- Grafana
#                                                     enforces its own login)
#
# Prerequisites on the VM:
#   - Ubuntu/Debian host with apt
#   - Inbound TCP 80 and 443 open in Azure NSG (80 is required for the
#     Let's Encrypt HTTP-01 challenge; 443 is the public surface)
#   - The four backend services already published on the host loopback
#     (`ss -tlnp | grep -E ':(8080|8005|8006|3000)\b'`)
#
# Usage:
#   sudo VM_PUBLIC_IP=4.188.84.220 ./setup.sh
#
# Optional environment variables:
#   DEVKIT_AUTH_USER       Basic-auth username for dev-kit (default: admin)
#   DEVKIT_AUTH_PASSWORD   Plaintext password to hash. If unset, the script
#                          prompts interactively (recommended).
#   CHAT_RATE_LIMIT        Requests/minute/IP for chat (default: 30)
#   VOICE_RATE_LIMIT       Requests/minute/IP for voice (default: 60)
#
# The script is idempotent: re-runs reuse the installed Caddy + plugin and
# regenerate the Caddyfile from current env values.

set -euo pipefail

# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

if [[ "${EUID}" -ne 0 ]]; then
  echo "ERROR: must run as root (use sudo)." >&2
  exit 1
fi

if [[ -z "${VM_PUBLIC_IP:-}" ]]; then
  echo "ERROR: VM_PUBLIC_IP must be set, e.g.:" >&2
  echo "  sudo VM_PUBLIC_IP=4.188.84.220 $0" >&2
  exit 1
fi

# Shape check: four octets, each 0-255 (basic guard, not RFC-strict).
if ! [[ "${VM_PUBLIC_IP}" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
  echo "ERROR: VM_PUBLIC_IP=${VM_PUBLIC_IP} doesn't look like a v4 address." >&2
  exit 1
fi

DEVKIT_AUTH_USER="${DEVKIT_AUTH_USER:-admin}"
CHAT_RATE_LIMIT="${CHAT_RATE_LIMIT:-30}"
VOICE_RATE_LIMIT="${VOICE_RATE_LIMIT:-60}"

# sslip.io accepts both dotted and dashed IP forms; the dashed form is
# friendlier as a subdomain prefix.
IP_DASHES="${VM_PUBLIC_IP//./-}"
HOST_DEVKIT="devkit.${IP_DASHES}.sslip.io"
HOST_CHAT="chat.${IP_DASHES}.sslip.io"
HOST_VOICE="voice.${IP_DASHES}.sslip.io"
HOST_GRAFANA="grafana.${IP_DASHES}.sslip.io"

# ---------------------------------------------------------------------------
# Install Caddy from the official Cloudsmith apt repo (idempotent)
# ---------------------------------------------------------------------------

if ! command -v caddy >/dev/null 2>&1; then
  echo "[1/5] Installing Caddy from the official apt repo..."
  apt-get update -y
  apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl

  curl -fsSL 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg

  curl -fsSL 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    | tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null

  apt-get update -y
  apt-get install -y caddy
else
  echo "[1/5] Caddy already installed: $(caddy version | head -1)"
fi

# ---------------------------------------------------------------------------
# Install rate-limit plugin (idempotent). `caddy add-package` re-builds
# Caddy with the requested module; no-op if already present.
# ---------------------------------------------------------------------------

echo "[2/5] Ensuring rate_limit plugin is installed..."
if caddy list-modules 2>/dev/null | grep -q '^http\.handlers\.rate_limit$'; then
  echo "      rate_limit module already present, skipping."
else
  caddy add-package github.com/mholt/caddy-ratelimit
fi

# ---------------------------------------------------------------------------
# Collect dev-kit basic-auth password and hash it via Caddy's bcrypt helper.
# ---------------------------------------------------------------------------

echo "[3/5] Hashing dev-kit basic-auth password..."
if [[ -n "${DEVKIT_AUTH_PASSWORD:-}" ]]; then
  DEVKIT_AUTH_HASH="$(caddy hash-password --plaintext "${DEVKIT_AUTH_PASSWORD}")"
else
  # Interactive prompt; doesn't echo input.
  read -r -s -p "      Enter dev-kit password for user '${DEVKIT_AUTH_USER}': " _pw
  echo
  if [[ -z "${_pw}" ]]; then
    echo "ERROR: empty password. Aborting." >&2
    exit 1
  fi
  DEVKIT_AUTH_HASH="$(caddy hash-password --plaintext "${_pw}")"
  unset _pw
fi

# ---------------------------------------------------------------------------
# Render the Caddyfile.
# ---------------------------------------------------------------------------

echo "[4/5] Writing /etc/caddy/Caddyfile..."

# Back up any existing config once per day so re-runs don't churn the history.
if [[ -f /etc/caddy/Caddyfile ]]; then
  cp -p /etc/caddy/Caddyfile "/etc/caddy/Caddyfile.bak.$(date +%Y%m%d)"
fi

cat >/etc/caddy/Caddyfile <<CADDY
# Generated by automation/caddy/setup.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ)
# Source IP: ${VM_PUBLIC_IP}
# Re-run the script to regenerate this file.

# ---------------------------------------------------------------------------
# dev-kit (port 8080) -- basic auth required.
# Anyone reaching this UI can deploy/destroy stacks and read entered secrets,
# so leaving it open to the internet is equivalent to leaving root open.
# ---------------------------------------------------------------------------
${HOST_DEVKIT} {
    basic_auth {
        ${DEVKIT_AUTH_USER} ${DEVKIT_AUTH_HASH}
    }
    reverse_proxy localhost:8080
}

# ---------------------------------------------------------------------------
# Public chat (reach_layer_web, port 8005) -- intentionally open for demos,
# rate-limited per IP so a single scraper can't drain the Anthropic budget.
# ---------------------------------------------------------------------------
${HOST_CHAT} {
    rate_limit {
        zone chat_per_ip {
            key    {remote_host}
            events ${CHAT_RATE_LIMIT}
            window 1m
        }
    }
    reverse_proxy localhost:8005
}

# ---------------------------------------------------------------------------
# Voice webhook (reach_layer_voice, port 8006) -- Vobiz's HMAC signature is
# the auth gate; we just provide TLS and reachability. A light per-IP rate
# limit guards against accidental webhook loops.
# ---------------------------------------------------------------------------
${HOST_VOICE} {
    rate_limit {
        zone voice_per_ip {
            key    {remote_host}
            events ${VOICE_RATE_LIMIT}
            window 1m
        }
    }
    reverse_proxy localhost:8006
}

# ---------------------------------------------------------------------------
# Grafana (port 3000) -- no Caddy basic auth: Grafana enforces its own login
# and we don't want to layer two unrelated credentials operators have to
# remember. Make sure GF_SECURITY_ADMIN_PASSWORD is set to a real value in
# the compose env -- the default 'admin/admin' is universally known.
# ---------------------------------------------------------------------------
${HOST_GRAFANA} {
    reverse_proxy localhost:3000
}
CADDY

chown root:caddy /etc/caddy/Caddyfile
chmod 0640 /etc/caddy/Caddyfile

# ---------------------------------------------------------------------------
# Validate, then reload (no downtime). systemd's hot-reload picks up changes
# without dropping in-flight TLS handshakes.
# ---------------------------------------------------------------------------

echo "[5/5] Validating and reloading Caddy..."
caddy validate --config /etc/caddy/Caddyfile

systemctl enable caddy >/dev/null 2>&1 || true
if systemctl is-active --quiet caddy; then
  systemctl reload caddy
else
  systemctl start caddy
fi

cat <<EOF

Caddy is now serving:

  Service   URL                                                 Notes
  -------   ---                                                 -----
  dev-kit   https://${HOST_DEVKIT}                              basic auth: ${DEVKIT_AUTH_USER} / <your password>
  chat      https://${HOST_CHAT}                                open, ${CHAT_RATE_LIMIT} req/min/IP
  voice     https://${HOST_VOICE}                               open, ${VOICE_RATE_LIMIT} req/min/IP
  grafana   https://${HOST_GRAFANA}                             Grafana login (set GF_SECURITY_ADMIN_PASSWORD)

Verify (from any machine outside the VM):
  curl -I https://${HOST_DEVKIT}     # expect 401 Unauthorized
  curl -I https://${HOST_CHAT}/      # expect 200 / 404 from reach_layer_web
  curl -u ${DEVKIT_AUTH_USER}:'<password>' -I https://${HOST_DEVKIT}    # expect 200 / 3xx

Tighten the per-service public ports once everything works:
  - In docker compose, change e.g. \`ports: ["8080:8080"]\` -> \`ports: ["127.0.0.1:8080:8080"]\`
  - Remove the corresponding inbound rules for 8080/8005/8006/3000 in Azure NSG.
  - Caddy still reaches them on the host loopback.

For long-term auth (Tailscale / OAuth) and abuse controls, see issue #273.
EOF
