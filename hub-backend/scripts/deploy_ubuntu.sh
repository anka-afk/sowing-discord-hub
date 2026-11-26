#!/usr/bin/env bash
set -euo pipefail

echo "=== 1. Stop old unused services ==="
systemctl disable --now wuhu-platform-bootstrap.service 2>/dev/null || true
systemctl disable --now nginx.service 2>/dev/null || true

echo "=== 2. Ensure 1G swap ==="
if [ ! -f /swapfile ] && [ "$(swapon --show | wc -l)" -eq 0 ]; then
  fallocate -l 1G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=1024
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "=== 3. Ensure python3-venv & clone repo ==="
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3-venv curl ca-certificates >/dev/null

mkdir -p /opt/sowing-discord-hub/data/media
if [ -d /opt/sowing-discord-hub/.git ]; then
  git -C /opt/sowing-discord-hub pull --ff-only
else
  rm -rf /opt/sowing-discord-hub
  git clone --depth 1 https://github.com/anka-afk/sowing-discord-hub.git /opt/sowing-discord-hub
  mkdir -p /opt/sowing-discord-hub/data/media
fi

echo "=== 4. Setup Python venv & install dependencies ==="
cd /opt/sowing-discord-hub/hub-backend
python3 -m venv .venv
.venv/bin/pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple -q
.venv/bin/pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple -q

echo "=== 5. Generate production .env ==="
ENV_FILE=/opt/sowing-discord-hub/hub-backend/.env
if [ ! -f "$ENV_FILE" ]; then
  JWT_SEC=$(.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(36))")
  HMAC_SEC=$(.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(32))")
  COLL_SEC=$(.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(32))")
  ADM_PWD=$(.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(14))")
  TS_KEY="${TURNSTILE_SECRET_KEY:-}"
  TS_ENABLED="false"
  if [ -n "$TS_KEY" ]; then
    TS_ENABLED="true"
  fi
  cat > "$ENV_FILE" <<EOF
HUB_ID=official-main
HUB_NAME=Sowing Discord Hub
WEB_BASE_URL=https://banshi.ankaanka.org
API_BASE_URL=https://api.banshi.ankaanka.org
API_DOMAIN=api.banshi.ankaanka.org

DATABASE_PATH=/opt/sowing-discord-hub/data/hub.db
STORAGE_BACKEND=local
LOCAL_STORAGE_DIR=/opt/sowing-discord-hub/data/media
PUBLIC_MEDIA_BASE_URL=https://api.banshi.ankaanka.org/media/public
STORAGE_LIMIT_BYTES=10737418240

JWT_SECRET=${JWT_SEC}
JWT_EXPIRE_SECONDS=86400
HMAC_SALT=${HMAC_SEC}
COLLECTOR_HMAC_SECRET=${COLL_SEC}

ADMIN_USERNAME=admin
ADMIN_PASSWORD=${ADM_PWD}

TURNSTILE_ENABLED=${TS_ENABLED}
TURNSTILE_SECRET_KEY=${TS_KEY}

POW_DIFFICULTY_BITS=16
POW_TTL_SECONDS=300
FEEDBACK_REPORT_THRESHOLD=3
FEEDBACK_INSTANCE_MIN_AGE_HOURS=24
FEEDBACK_MAX_GROUP_CONTRIBUTION=10
CORS_ORIGINS=https://banshi.ankaanka.org,https://sowing-discord-hub.pages.dev
EOF
  chmod 600 "$ENV_FILE"
  echo "=========================================="
  echo "ADMIN_USERNAME=admin"
  echo "ADMIN_PASSWORD=${ADM_PWD}"
  echo "COLLECTOR_HMAC_SECRET=${COLL_SEC}"
  echo "=========================================="
else
  echo "ENV_ALREADY_EXISTS=true (kept existing $ENV_FILE)"
fi

echo "=== 6. Install & start sowing-hub systemd service ==="
cat > /etc/systemd/system/sowing-hub.service <<'EOF'
[Unit]
Description=Sowing Discord Hub Backend (FastAPI)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/sowing-discord-hub/hub-backend
EnvironmentFile=/opt/sowing-discord-hub/hub-backend/.env
ExecStart=/opt/sowing-discord-hub/hub-backend/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --proxy-headers
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now sowing-hub.service
sleep 3
systemctl is-active sowing-hub.service

echo "=== 7. Setup daily media cleanup cron ==="
cat > /etc/cron.d/sowing-hub-cleanup <<'EOF'
0 4 * * * root cd /opt/sowing-discord-hub/hub-backend && /opt/sowing-discord-hub/hub-backend/.venv/bin/python -m app.jobs >/var/log/sowing-hub-cleanup.log 2>&1
EOF
chmod 644 /etc/cron.d/sowing-hub-cleanup

echo "=== 8. Verify local backend health & meta ==="
curl -fsS http://127.0.0.1:8000/healthz
echo ""
curl -fsS http://127.0.0.1:8000/api/v1/meta | head -c 300
echo ""

echo "=== 9. Install cloudflared & start cardless tunnel login ==="
if ! command -v cloudflared >/dev/null 2>&1; then
  curl -L -s --max-time 120 -o /usr/local/bin/cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
  chmod +x /usr/local/bin/cloudflared
fi
cloudflared --version

if [ ! -f /root/.cloudflared/cert.pem ]; then
  pkill -f "cloudflared tunnel login" 2>/dev/null || true
  rm -f /tmp/cf_login.log
  setsid /usr/local/bin/cloudflared tunnel login >/tmp/cf_login.log 2>&1 </dev/null &
  sleep 4
  echo "=== 请复制下方链接在浏览器打开并授权 ankaanka.org ==="
  cat /tmp/cf_login.log
else
  echo "CLOUDFLARED_CERT_ALREADY_EXISTS=true"
fi
