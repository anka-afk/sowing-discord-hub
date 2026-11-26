# Sowing Discord Hub（搬史自动化 2.0 中心站）

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](./LICENSE)

配套 [astrbot_sowing_discord v2](https://github.com/anka-afk/astrbot_sowing_discord) 的中心化公益内容站、极速人工审核台与单官方号投稿群初筛采集器。

- **前端网站 & 键盘流审核台（推荐域名）**：`https://banshi.ankaanka.org`（Cloudflare Pages）
- **后端 API（推荐域名）**：`https://banshi-api.ankaanka.org`（免费 Cloudflare Tunnel / 1C2G VPS + Caddy）
- **R2 公开图床 CDN（推荐域名）**：`https://cdn.banshi.ankaanka.org`（Cloudflare R2，0 出站流量费）
- **原发布页保留不变**：`https://sowingdiscord.ankaanka.org`

> 完整架构设计与冻结契约（JSON Schema v1.0、REST API、数据库表结构、熔断机制）见 [IMPLEMENTATION_PLAN.md](./IMPLEMENTATION_PLAN.md)。

---

## 目录结构

```text
sowing-discord-hub/
├── hub-backend/    # FastAPI + SQLite(WAL) + Litestream + R2/Local + Caddy 一键 HTTPS + 多管理员 CLI
├── hub-web/        # Vue 3 + TypeScript + Vite SPA（广场、投稿、键盘流审核台 + 浏览器 Canvas 720×1800 切片）
├── collector/      # 独立单官方号 OneBot v11 WS 投稿群初筛采集器（AC 自动机 + pHash + HMAC）
└── tests/e2e/      # 跨仓库全链路端到端集成测试（联动 astrbot_sowing_discord@v2）
```

---

## 快速部署

### 1. 前端一键部署到 Cloudflare Pages (`hub-web/`)

在 Cloudflare Dashboard → **Workers & Pages → Create → Pages → Connect to Git** 选择 `anka-afk/sowing-discord-hub`：

| 配置项 | 填写值 |
| :--- | :--- |
| Production branch | `main` |
| Root directory (Path) | `hub-web` |
| Build command | `npm run build` |
| Build output directory | `dist` |
| 环境变量 `NODE_VERSION` | `22` |
| 环境变量 `VITE_API_BASE_URL` | `https://banshi-api.ankaanka.org` |
| 环境变量 `VITE_TURNSTILE_SITE_KEY` | *(可选，接入 Cloudflare Turnstile 后填入)* |

部署完成后在 **Custom domains** 绑定 `banshi.ankaanka.org`。

### 2. 后端一键部署到 1C2G VPS (`hub-backend/`)

```bash
git clone https://github.com/anka-afk/sowing-discord-hub.git
cd sowing-discord-hub/hub-backend
cp .env.example .env
# 编辑 .env：务必替换 JWT_SECRET、HMAC_SALT、COLLECTOR_HMAC_SECRET、ADMIN_PASSWORD，
# 并填入 Cloudflare R2 凭证与 API_DOMAIN=api.banshi.ankaanka.org

# 一键启动后端 + Caddy 自动 HTTPS + Litestream 实时备份 SQLite 至 R2
docker compose --profile production up -d --build
```

#### 管理多位可信审核员账号（CLI）

```bash
docker compose exec hub-api python -m app.cli_admin list
docker compose exec hub-api python -m app.cli_admin create --username alice --password "StrongPassword!" --role moderator
docker compose exec hub-api python -m app.cli_admin set-active --username alice --active false
docker compose exec hub-api python -m app.cli_admin reset-password --username alice --password "NewPassword!"
```

#### 定时清理过期/拒审媒体（Cron）

```bash
# 每日凌晨 4 点清理 7 天前拒审媒体与 14 天过期未审媒体（保留 sha256/phash 防重投）
0 4 * * * cd /path/to/sowing-discord-hub/hub-backend && docker compose exec -T hub-api python -m app.jobs
```

### 3. 运行单官方号投稿群采集器 (`collector/`)

```bash
cd sowing-discord-hub/collector
cp config.example.yaml config.yaml
# 填写 onebot.ws_url、watch_groups 以及与后端一致的 hub.shared_secret
docker build -t sowing-collector .
docker run -d --name sowing-collector --restart unless-stopped \
  -v $(pwd)/config.yaml:/app/config.yaml:ro \
  -v $(pwd)/data:/app/data \
  sowing-collector
```
