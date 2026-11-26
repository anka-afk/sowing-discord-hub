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

echo "=== 3. Ensure system user, python3-venv & clone repo ==="
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3-venv curl ca-certificates >/dev/null

if ! id -u sowinghub >/dev/null 2>&1; then
  useradd --system --user-group --home-dir /opt/sowing-discord-hub --shell /usr/sbin/nologin sowinghub
fi

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
  ADM_PWD=$(.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(16))")
  TS_KEY="${TURNSTILE_SECRET_KEY:-}"
  TS_ENABLED="false"
  if [ -n "$TS_KEY" ]; then
    TS_ENABLED="true"
  else
    echo "WARNING: TURNSTILE_SECRET_KEY is empty; web Turnstile verification is disabled until configured in $ENV_FILE." >&2
  fi
  (
    umask 077
    cat > "$ENV_FILE" <<EOF
APP_ENV=production
HUB_ID=official-main
HUB_NAME=Sowing Discord Hub
WEB_BASE_URL=https://banshi.ankaanka.org
API_BASE_URL=https://banshi-api.ankaanka.org
API_DOMAIN=banshi-api.ankaanka.org

DATABASE_PATH=/opt/sowing-discord-hub/data/hub.db
STORAGE_BACKEND=local
LOCAL_STORAGE_DIR=/opt/sowing-discord-hub/data/media
PUBLIC_MEDIA_BASE_URL=https://banshi-api.ankaanka.org/media/public
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
  )
  echo "Generated $ENV_FILE (mode 0640 root:sowinghub). Inspect it securely via 'sudo cat $ENV_FILE'."
else
  echo "ENV_ALREADY_EXISTS=true (kept existing $ENV_FILE)"
fi

# Keep code/venv owned by root:root (prevents git dubious-ownership & restricts sowinghub to data dir only)
chown root:sowinghub "$ENV_FILE"
chmod 640 "$ENV_FILE"
chown -R sowinghub:sowinghub /opt/sowing-discord-hub/data

echo "=== 6. Install & start hardened sowing-hub systemd service ==="
cat > /etc/systemd/system/sowing-hub.service <<'EOF'
[Unit]
Description=Sowing Discord Hub Backend (FastAPI)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=sowinghub
Group=sowinghub
WorkingDirectory=/opt/sowing-discord-hub/hub-backend
EnvironmentFile=/opt/sowing-discord-hub/hub-backend/.env
ExecStart=/opt/sowing-discord-hub/hub-backend/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips=127.0.0.1
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/opt/sowing-discord-hub/data

[Install]
WantedBy=multi-user.target
EOF
chmod 644 /etc/systemd/system/sowing-hub.service

systemctl daemon-reload
systemctl enable --now sowing-hub.service
sleep 3
systemctl is-active sowing-hub.service

echo "=== 7. Setup daily media cleanup cron (non-root) ==="
touch /var/log/sowing-hub-cleanup.log
chown sowinghub:sowinghub /var/log/sowing-hub-cleanup.log
cat > /etc/cron.d/sowing-hub-cleanup <<'EOF'
0 4 * * * sowinghub cd /opt/sowing-discord-hub/hub-backend && /opt/sowing-discord-hub/hub-backend/.venv/bin/python -m app.jobs >>/var/log/sowing-hub-cleanup.log 2>&1
EOF
chmod 644 /etc/cron.d/sowing-hub-cleanup

echo "=== 8. Verify local backend health & meta ==="
curl -fsS http://127.0.0.1:8000/healthz
echo ""
curl -fsS http://127.0.0.1:8000/api/v1/meta | head -c 300
echo ""

echo "=== 9. Install cloudflared via signed Cloudflare apt repo ==="
if ! command -v cloudflared >/dev/null 2>&1; then
  mkdir -p --mode=0755 /usr/share/keyrings
  curl -fsSL --max-time 30 https://pkg.cloudflare.com/cloudflare-main.gpg -o /usr/share/keyrings/cloudflare-main.gpg
  echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' > /etc/apt/sources.list.d/cloudflared.list
  chmod 644 /usr/share/keyrings/cloudflare-main.gpg /etc/apt/sources.list.d/cloudflared.list
  apt-get update -qq && apt-get install -y -qq cloudflared >/dev/null
fi
cloudflared --version

if [ ! -f /root/.cloudflared/cert.pem ]; then
  pkill -f "cloudflared tunnel login" 2>/dev/null || true
  CF_LOG=$( (umask 077; mktemp /tmp/cf_login.XXXXXX.log) )
  setsid "$(command -v cloudflared)" tunnel login >"$CF_LOG" 2>&1 </dev/null &
  sleep 4
  echo "=== 请复制下方链接在浏览器打开并授权 ankaanka.org ==="
  cat "$CF_LOG"
else
  echo "CLOUDFLARED_CERT_ALREADY_EXISTS=true"
fi
