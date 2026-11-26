# sowing-discord-hub Web 前端与极速审核台 (`hub-web`)

基于 **Vue 3 + TypeScript + Vite** 构建的静态 SPA，适配 **Cloudflare Pages** 零成本部署。

## 核心页面与功能

- **首页广场 (`/`) & 单条详情 (`/m/:id`)**
  - 对接 `GET /api/v1/feed`、`GET /api/v1/random`、`GET /api/v1/items/{id}`
  - 支持按 `tags`、`max_nsfw`（L0/L1/L2）筛选与游标翻页
  - 完整渲染全部 5 种 Block：`text`、`image`、`gif`、`chat_record`（优先展示 720px 切片图，支持折叠展开结构化气泡）、`video_link` 卡片
  - 优雅处理 `410 Gone` 紧急下架墓碑状态
- **真人投稿页 (`/submit`)**
  - 支持图片/GIF 拖拽多图上传（`POST /api/v1/submissions/media`），命中 `duplicate: true` 立即友好提示「火星了」并按隐私规则决定展示已有详情链接或待审提示
  - 内置结构化聊天记录编辑器（最多 20 条气泡、支持文字与插图、右侧实时所见即所得预览、本地隐私正则预警）
  - 支持外链视频卡片与纯文本投稿；集成 Cloudflare Turnstile 组件（开发环境无 SiteKey 自动降级放行）
- **管理员极速审核台 (`/admin`)**
  - JWT 登录（`POST /api/v1/admin/login`）与运营概览条（`GET /api/v1/admin/stats`）
  - 单卡聚焦 + 后 2 条待审媒体预加载 + 键盘流快捷键：`A` 通过、`R` 拒绝（`1` 重复 / `2` 违规 / `3` 质量低）、`N` 切换 `nsfw_level`、`T` 快选标签、`Space` 跳过
  - 决策辅助告警栏：展示后端 `similar_items`、投稿者过审率、服务端+本地正则隐私告警
  - 浏览器端 Canvas 切片渲染引擎 (`src/utils/chatRenderer.ts`)：固定 `720px` 宽、严格按完整气泡边界分页、单张高度 `<= 1800px`、导出 `image/webp` 并在点击 `A` 通过时自动上传至 `POST /api/v1/admin/media` 随审单提交
  - Kill Switch 紧急下架：支持按条目 ID 或已过审列表一键触发 `POST /api/v1/admin/items/{id}/takedown`

## 本地开发与构建

```bash
cp .env.example .env.local
npm install
npm run dev      # 默认 http://localhost:5173，代理 /api 与 /media 至 http://127.0.0.1:8000
npm test         # 运行 Vitest 单元测试
npm run build    # TypeScript 类型检查 + 生产构建至 dist/
npm run preview  # 本地预览 dist/
```

## Cloudflare Pages 部署说明

- **Build command**: `npm run build`
- **Build output directory**: `dist`
- **Root directory**: `hub-web`
- **Environment variables**:
  - `VITE_API_BASE_URL`: 后端 API 域名（如 `https://api.example.org`，同源反代可留空）
  - `VITE_TURNSTILE_SITE_KEY`: Cloudflare Turnstile Site Key（留空则降级放行）
- SPA 路由回退已由 `public/_redirects` (`/* /index.html 200`) 自动配置。
