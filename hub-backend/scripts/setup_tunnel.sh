#!/usr/bin/env bash
set -euo pipefail

TUNNEL_NAME="${TUNNEL_NAME:-sowing-discord-hub}"
HOSTNAME="${API_HOSTNAME:-banshi-api.ankaanka.org}"

if [ ! -f /root/.cloudflared/cert.pem ]; then
  echo "ERROR: /root/.cloudflared/cert.pem not found! Please open the login URL and authorize first."
  exit 1
fi

echo "=== 1. Create (or reuse) Cloudflare Tunnel: ${TUNNEL_NAME} ==="
if ! cloudflared tunnel info "${TUNNEL_NAME}" >/dev/null 2>&1; then
  cloudflared tunnel create "${TUNNEL_NAME}"
fi

TUNNEL_ID=$(cloudflared tunnel list --output json | /opt/sowing-discord-hub/hub-backend/.venv/bin/python -c '
import json, sys
target = sys.argv[1]
items = json.load(sys.stdin)
for it in items:
    if it.get("name") == target:
        print(it["id"])
        break
' "${TUNNEL_NAME}")

if [ -z "$TUNNEL_ID" ]; then
  echo "ERROR: Could not find tunnel ID for ${TUNNEL_NAME}"
  exit 1
fi
echo "Tunnel ID: ${TUNNEL_ID}"

echo "=== 2. Route DNS: ${HOSTNAME} -> ${TUNNEL_NAME} ==="
cloudflared tunnel route dns --overwrite-dns "${TUNNEL_NAME}" "${HOSTNAME}"

echo "=== 3. Write /etc/cloudflared/config.yml ==="
mkdir -p /etc/cloudflared
cat > /etc/cloudflared/config.yml <<EOF
tunnel: ${TUNNEL_ID}
credentials-file: /root/.cloudflared/${TUNNEL_ID}.json

ingress:
  - hostname: ${HOSTNAME}
    service: http://127.0.0.1:8000
  - service: http_status:404
EOF

echo "=== 4. Install & start cloudflared systemd service ==="
cloudflared service uninstall 2>/dev/null || true
cloudflared service install
systemctl enable --now cloudflared
sleep 3
systemctl is-active cloudflared

echo "=== 5. Verify public HTTPS endpoint ==="
for i in 1 2 3 4 5; do
  if curl -fsS --max-time 8 "https://${HOSTNAME}/healthz"; then
    echo ""
    echo "SUCCESS! https://${HOSTNAME} is LIVE!"
    exit 0
  fi
  echo "Waiting for DNS/tunnel propagation ($i/5)..."
  sleep 3
done
echo "Tunnel service is running! Wait ~30s for DNS propagation and test https://${HOSTNAME}/healthz"
