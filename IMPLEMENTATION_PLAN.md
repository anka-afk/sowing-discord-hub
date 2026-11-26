# AstrBot Sowing Discord v2（搬史自动化 2.0）最终一致实现方案

> 本文档由「服务端与安全架构师」与「AstrBot 插件与交互架构师」多轮架构对齐后正式冻结，确立了「**方案 B（中心化公益内容 Hub + 人工审核）+ 方案 C（多源可插拔跨平台插件 + 单官方号投稿群初筛）**」的完整工程蓝图，可直接作为前后端、插件端与采集器并行开发的统一技术契约。

---

## 一、核心架构演进与设计原则

### 1.1 彻底解决 v1 痛点
v1 版本依赖「所有用户的 Bot 统一加入公共 QQ 源群 + 暴力轮询百个 `fetch_emoji_like` + `forward_group_single_msg`」，存在极高的风控连坐、源群失联、恶意投毒炸号、无法跨平台及打扰群聊等问题。

v2 版本实现**采集/审核/托管**与**终端分发**的彻底解耦：
- **用户 Bot 零加群**：装上插件默认直连公益 Hub，不再要求用户 Bot 加入任何公共 QQ 群。
- **严控公益运维成本（总计 \$0~4/月）**：不依赖全量 LLM 审核，采用「零成本规则初筛 + 可信管理员极速人工审史 + Cloudflare R2 零流量费托管」。
- **两端零重型渲染依赖**：既不在 1C2G 低配 VPS 上跑 Playwright，也不在 AstrBot 插件端塞 Chromium/字体包。
- **真正跨平台与联邦化**：统一采用跨平台 Block 协议，无缝支持 QQ / Telegram / Discord / 微信 / 企微 / 飞书，并支持多 Hub 联邦订阅与本地源群平滑迁移。

### 1.2 系统全景架构

```text
┌─────────────────────────────┐
│          内容上游            │
│ 1. Web 网站真人投稿(Turnstile)│
│ 2. 插件 /投稿 (本地脱敏直传)  │
│ 3. 单个官方号蹲守投稿群(初筛)  │
└──────────────┬──────────────┘
               ▼
┌──────────────────────────────────────────────────────────┐
│           中心站：sowing-discord-hub (方案 B)              │
│  ┌──────────────────┐  ┌──────────────────────────────┐  │
│  │ 自动初筛与媒体流水线│  │ Web 极速审核台 (CF Pages SPA) │  │
│  │ • MagicBytes/防炸弹│  │ • 键盘流单卡审史 (2~3s/条)    │  │
│  │ • 清 EXIF / 转 WebP│  │ • 浏览器端 Canvas 切片渲染    │  │
│  │ • SHA256/pHash 去重│  │   聊天记录 (720×1800 WebP)   │  │
│  │ • 正则隐私风险高亮  │  │ • Kill Switch 一键紧急下架   │  │
│  └────────┬─────────┘  └──────────────┬───────────────┘  │
│           ▼                           ▼                  │
│  SQLite (WAL + Litestream 实时备份) + Cloudflare R2/CDN  │
│  开放无状态 REST API (/api/v1): meta / feed / random /    │
│  instances / submissions / feedback / admin              │
└──────────────────────────┬───────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────┐
│        客户端：astrbot_sowing_discord v2 (方案 C)         │
│  ┌───────────────────┐  ┌─────────────────────────────┐  │
│  │ Source (多源并存)  │  │ Pipeline (SQLite 去重与调度) │  │
│  │ • 官方/多 Hub API  │─▶│ • 按群已发去重 & 墓碑同步清理 │  │
│  │ • 通用 RSS / Atom │  │ • 五态「读空气」智能破冰状态机 │  │
│  │ • 本地事件驱动源群  │  │ • 可选用户自有 LLM 简短吐槽  │  │
│  └───────────────────┘  └──────────────┬──────────────┘  │
│                                        ▼                 │
│  Sink (全平台发送 + 本地缓存降级 + 批量匿名反馈闭环)         │
└──────────────────────────────────────────────────────────┘
```

---

## 二、双方博弈达成的 5 项核心架构共识

| 议题 | 最终冻结决策 | 解决的核心痛点 |
| :--- | :--- | :--- |
| **1. 聊天记录长图渲染归属** | **Web 管理员审核台（浏览器端）预渲染切片并上传 R2**。<br>画布宽固定 `720px`，严格按**完整对话气泡边界**换页，单张高上限 `1800px`（宽高比 ≤ `1:2.5`），导出 WebP。服务端审核接口硬校验 `chat_record` 必须附带非空 `rendered_images`，同时保留脱敏 `messages` 结构化 JSON。 | 服务端 1C2G VPS 零 Playwright/OOM 风险；插件端零渲染/字体依赖；利用管理员浏览器原生中文与彩色 Emoji 渲染保真度最高；规避 QQ/TG 长图折叠压糊。 |
| **2. 媒体压缩、动图与视频边界** | • **静态图**：去 EXIF/GPS，长边 ≤ `1920px`，转 `image/webp`（质量 80，单图 80~250KB）。<br>• **动图**：**强制原样保留 `image/gif`（≤ 5MB）**，防解压炸弹、清注释块、抽首帧算 pHash，**绝不转 Animated WebP**。<br>• **视频**：**MVP（Phase 1 & 2）严禁托管原始视频文件**，仅支持白名单 `video_link` 外链卡片（封面图转存 R2 防破图）；Schema 预留 `video_file`，Phase 3 按资源通过 `/meta` 能力开关解锁。 | 死守 Cloudflare R2 10GB 免费存储额度（可存 4~10 万张史图）；避开微信与部分 OneBot 端不支持动图 WebP 的兼容性深坑；避免低配服务器跑 FFmpeg 转码拖垮。 |
| **3. 媒体缓存、防盗链与下架同步** | • `pending` 待审媒体存 R2 **私有前缀**（走管理员鉴权代理或 5 分钟签名 URL），防白嫖图床传播违禁图。<br>• 过审后落盘**公开前缀**，下发**永久无签名 CDN URL**（`Cache-Control: public, max-age=31536000, immutable`）。<br>• 服务端维护全局单调 `global_seq`，发布条目与下架墓碑共享序号；`/feed` 同步返回 `tombstones`，插件立即清理本地缓存；紧急下架时删 R2 对象并 Purge CF 缓存。 | 彻底解决插件本地热缓存池 URL 过期失效问题；同时保证紧急下架时全网插件秒级同步撤回、不发出死链或违规图。 |
| **4. 零门槛匿名鉴权、去重与脱敏** | • 插件首次启动通过后台协程完成 16-bit 前导零 SHA-256 轻量 PoW（<500ms），自动换取持久化 `instance_id + instance_token`；可选配 `X-User-Token` 走网站可信投稿人高优通道。<br>• **服务端完全无状态**，不维护全网已读记录；插件本地 SQLite 负责按群去重，`/random` 一次批量吐 20 条候选供本地过滤补仓。<br>• **双保险脱敏**：插件端 `/投稿` 强制拍平聊天记录（`max_depth=1, max_nodes=20`）、抹除群号/QQ 号/头像映射为 `群友A/B`，本地拉图后直传服务端；服务端拒绝回源拉外链（防 SSRF），再做 EXIF 清理与敏感正则高亮。 | 用户装上插件零注册即可使用与投稿；防脚本刷库与 SSRF；消灭冗长 `exclude_ids` 参数。 |
| **5. 匿名众包反馈与举报熔断** | 插件通过 `POST /api/v1/feedback/batch`（单批 ≤50 条）上报 `like / dislike / report`，使用本地加盐盲化哈希 `voter_hash = sha256(instance_salt + group_id + user_id)`。<br>举报原因标准化；同一条目被 **≥3 个独立且注册满 24h 的实例**举报，自动触发熔断降级为 `pending_recheck` 并写入 `tombstones` 暂停全网分发。 | 防止单群/新注册脚本恶意刷榜或恶意举报下架正常内容；形成高质量「今日史王」社区飞轮。 |

---

## 三、基础设施与技术栈选型

### 3.1 服务端与前端（`sowing-discord-hub`）
- **后端 API & 媒体处理**：Python 3.12 + **FastAPI** + Uvicorn + Pillow + `imagehash`（单容器 Docker Compose 部署于 1C2G VPS，月成本 \$0~4）。
- **数据库与容灾**：**SQLite (WAL 模式)** + **Litestream** 实时增量备份至 R2（免独立数据库费用与运维，数据秒级可恢复）。
- **对象存储 & CDN**：**Cloudflare R2**（免费 10GB + **0 出站流量费**）+ 自定义域名接入 **Cloudflare CDN**。
- **前端网站 & 审核台**：Vue 3 / React + Vite 静态 SPA + `modern-screenshot`/Canvas 切片渲染，部署于 **Cloudflare Pages**（免费不限流量）。
- **投稿群采集器（独立轻量进程）**：Python 异步客户端，对接**单个官方号** OneBot v11 WS（如 NapCat），与主站解耦，经本地规则初筛（群精华 / `#投稿` / 贴表情达标 + AC 自动机敏感词 + 哈希查重）后经 HMAC 签名接口推送待审池。
- **存储生命周期维护（Cron）**：`rejected` 或超过 14 天未审条目的媒体，7 天后物理删除 R2 对象，仅保留 `sha256` / `phash` 记录防重投。

### 3.2 插件端（`astrbot_sowing_discord` v2）
- 严格遵循 AstrBot `Star` 规范，持久化文件统一存放于 `StarTools.get_data_dir("astrbot_sowing_discord")`。
- **零重依赖**：仅使用 AstrBot 自带的 `aiohttp`/`httpx` 与 Python 标准库 `sqlite3`/`xml.etree`（Pillow 仅作为微信等极少数不支持 WebP 平台的按需软降级，非强依赖）。

---

## 四、统一「搬史协议 JSON Schema (v1.0)」

### 4.1 枚举与边界常量（由 `GET /api/v1/meta` 动态下发）
- **`nsfw_level` 分级**（违法/涉黄/涉政/血腥暴恐由人工审核直接 Reject 不入库）：
  - `0`：全年龄安全
  - `1`：轻度敏感（地狱笑话、轻度擦边二次元、轻度抽象/脏话；**插件默认群上限**）
  - `2`：高能预警（重度抽象/精神污染/较明显擦边但未触红线，需群主显式开启）
- **默认媒体与结构限制**：
  - `max_image_bytes`: `10485760` (10MB)
  - `max_gif_bytes`: `5242880` (5MB)
  - `max_images_per_submission`: `9`
  - `max_chat_nodes`: `20`
  - `max_chat_depth`: `1`
- **`report` 举报原因枚举**：`nsfw_unmarked | privacy_leak | copyright | broken_media | duplicate | other`

### 4.2 `MemeItem` 标准结构
```json
{
  "schema_version": "1.0",
  "id": "01JABCDEF1234567890ABCDEFG",
  "seq": 1042,
  "hub_id": "official-main",
  "title": "可选简短标题",
  "summary": "一句话文字梗概（管理员未填时由服务端自动截取前 120 字纯文本兜底，专供纯文本 LLM 吐槽）",
  "tags": ["沙雕", "群聊日常"],
  "nsfw_level": 0,
  "score": 12.5,
  "stats": {
    "likes": 42,
    "dislikes": 2
  },
  "attribution": {
    "submitter_name": "匿名网友",
    "origin_label": "群友投稿"
  },
  "web_url": "https://sowingdiscord.ankaanka.org/m/01JABCDEF1234567890ABCDEFG",
  "published_at": "2026-11-26T09:00:00Z",
  "blocks": [
    {
      "type": "text",
      "text": "背景补充文本..."
    },
    {
      "type": "image",
      "url": "https://cdn.ankaanka.org/m/a.webp",
      "mime": "image/webp",
      "w": 1080,
      "h": 1440,
      "size_bytes": 142000
    },
    {
      "type": "gif",
      "url": "https://cdn.ankaanka.org/m/b.gif",
      "mime": "image/gif",
      "w": 480,
      "h": 480,
      "size_bytes": 1200000
    },
    {
      "type": "chat_record",
      "rendered_images": [
        {
          "url": "https://cdn.ankaanka.org/m/c_1.webp",
          "mime": "image/webp",
          "w": 720,
          "h": 1650,
          "size_bytes": 98000
        }
      ],
      "messages": [
        {
          "sender_alias": "群友A",
          "avatar_color_idx": 0,
          "time_label": "14:20",
          "content": [
            { "type": "text", "text": "绷不住了" },
            { "type": "image", "url": "https://cdn.ankaanka.org/m/inline.webp", "w": 500, "h": 500 }
          ]
        }
      ]
    },
    {
      "type": "video_link",
      "url": "https://www.bilibili.com/video/BV1xxxxxxxxx",
      "platform": "bilibili",
      "title": "视频标题",
      "cover_url": "https://cdn.ankaanka.org/m/cover.webp",
      "duration_sec": 45
    }
  ]
}
```
> **Phase 3 预留 Block (`video_file`)**：`{ "type": "video_file", "url": "...", "cover_url": "...", "play_page_url": "...", "mime": "video/mp4", "w": 1280, "h": 720, "duration_sec": 12, "size_bytes": 6500000 }`。仅在 `/meta` 的 `capabilities.video_file == true` 时启用。

---

## 五、完整 REST API 契约（统一前缀 `/api/v1`）

### 5.1 联邦元数据与实例无感鉴权
| 方法与路径 | 说明 | 请求 / 响应要点 |
| :--- | :--- | :--- |
| `GET /api/v1/meta` | 站点自发现与能力探测（缓存 `max-age=300`） | 返回 `{ hub_id, hub_name, schema_version: "1.0", capabilities: { video_file: false, feedback: true }, limits: {...}, tags: [...] }` |
| `GET /api/v1/instances/challenge` | 获取轻量 PoW 挑战（5 分钟一次性有效） | 返回 `{ challenge_id, nonce_prefix, difficulty_bits: 16, expires_at }` |
| `POST /api/v1/instances/register` | 提交 PoW 解换取持久凭证 | 请求 `{ challenge_id, solution, client_version }`；返回 `{ instance_id, instance_token }`；后续接口携带 `Authorization: Bearer <instance_token>` |

### 5.2 公开无状态内容拉取
| 方法与路径 | 说明 | 请求 / 响应要点 |
| :--- | :--- | :--- |
| `GET /api/v1/feed` | 游标增量拉取 + 下架墓碑同步（支持 `ETag`/`304`） | 参数：`after_seq`, `before_seq`（历史翻页）, `limit=20`（最大 50）, `tags`, `exclude_tags`, `max_nsfw=1`。<br>返回：`{ items: MemeItem[], tombstones: ["01J..."], next_seq: 1058, has_more: false }` |
| `GET /api/v1/random` | 批量加权随机补仓（供 `/来坨史` 与老库补货） | 参数：`limit=20`, `tags`, `exclude_tags`, `max_nsfw=1`, `older_than_days`, `within_days`, `min_score`。<br>返回：`{ items: MemeItem[] }`（插件本地 SQLite 过滤已发记录并缓存余量） |
| `GET /api/v1/items/{id}` | 单条详情与存活校验 | 返回 `MemeItem` 或 `404 / 410 Gone` |

### 5.3 两步投稿与去重早停
| 方法与路径 | 说明 | 请求 / 响应要点 |
| :--- | :--- | :--- |
| `POST /api/v1/submissions/media` | 上传单张图或 GIF（防 SSRF + 压缩 + 实时查重） | `multipart/form-data`，鉴权：Web 凭 Turnstile 会话 / 插件凭 `Bearer instance_token`。<br>返回：`{ media_id, duplicate: bool, match_type?: "exact"|"similar", existing_status?: "approved"|"pending", existing_item_id?: string|null }`。<br>**隐私安全**：仅当原条目已 `approved` 时返回 `existing_item_id`，`pending` 状态必须返回 `null` 防止探测未审内容。 |
| `POST /api/v1/submissions` | 提交结构化待审条目 | 鉴权：Web 强制带 `turnstile_token`；插件带 `Bearer instance_token`，可选带 `X-User-Token`（高优通道）。<br>请求 `{ title?, tags?, blocks: [...] }`（媒体引用 `media_id`）；返回 `{ submission_id, status: "pending" }`。 |

### 5.4 匿名批量反馈与举报熔断
| 方法与路径 | 说明 | 请求 / 响应要点 |
| :--- | :--- | :--- |
| `POST /api/v1/feedback/batch` | 批量上报群友互动与举报（单批 ≤50 条） | 鉴权：`Bearer instance_token`。<br>请求：`{ events: [{ item_id, action: "like"|"dislike"|"report", reason?, voter_hash, group_hash, occurred_at }] }`。<br>防刷：`(item_id, instance_id, voter_hash, action)` 唯一约束；单群贡献封顶；≥3 个独立且注册满 24h 的实例举报自动熔断 `pending_recheck` 并写 `item_tombstones`。 |

### 5.5 采集器与管理审核台
| 方法与路径 | 说明 | 鉴权与行为 |
| :--- | :--- | :--- |
| `POST /api/v1/internal/collector/ingest` | 官方单号投稿群采集器入库 | HMAC-SHA256 内部签名鉴权，自动去重后进入 `pending` 队列 |
| `GET /api/v1/admin/queue` | 获取待审队列 | Admin JWT/Cookie + RBAC；附带 pHash 相似条目提示、投稿者历史过审率、正则隐私风险高亮 |
| `POST /api/v1/admin/items/{id}/review` | 键盘流极速审单 | 支持 `approve / reject`，编辑 `tags / nsfw_level / title / summary`；若含 `chat_record` 且 `approve`，**强制校验并入库前端渲染的 `rendered_images`**；通过瞬间迁移 R2 公开前缀并分配 `global_seq` |
| `POST /api/v1/admin/items/{id}/takedown` | 一键紧急下架（Kill Switch） | 立即分配 `global_seq` 写入 `item_tombstones` + 删 R2 对象 + 调 CF API Purge 缓存 + 记 `audit_logs` |
| `GET /api/v1/admin/stats` | 运营与健康监控 | 待审积压、R2 容量占比、熔断待复核队列、近期热门排行 |

---

## 六、数据库表结构设计

### 6.1 服务端数据库（SQLite WAL + Litestream）
1. **`seq_generator`**：单调递增全局序列发生器，同时服务 `items.seq` 与 `item_tombstones.seq`。
2. **`items`**：
   - `id` (ULID PK), `seq` (INTEGER UNIQUE, 发布时分配), `status` (`pending` | `approved` | `rejected` | `pending_recheck` | `takedown`)
   - `content_type` (`text` | `image` | `gif` | `mixed` | `chat_record` | `video_link`)
   - `title`, `summary`, `payload_json`, `tags_json`, `nsfw_level` (`0`/`1`/`2`)
   - `content_hash`, `source_type` (`web` | `plugin` | `collector_qq`), `submitter_hash` (加盐 HMAC，不存明文 QQ/IP)
   - `likes`, `dislikes`, `reports`, `score` (Wilson Score 质量排序分)
   - `reviewer_id`, `reject_reason`, `created_at`, `published_at`
3. **`item_tombstones`**：`seq` (INTEGER PK), `item_id` (INDEX), `reason`, `created_at`
4. **`media_assets`**：`id` (PK), `item_id`, `sha256` (INDEX), `phash` (INDEX), `mime_type`, `r2_key`, `url`, `w`, `h`, `size_bytes`, `visibility` (`private` | `public`), `is_deleted`, `created_at`
5. **`bot_instances`**：`instance_id` (PK), `token_hash`, `client_version`, `created_at`, `last_seen_at`, `is_banned`
6. **`feedback_events`**：`id` (PK), `item_id`, `instance_id`, `voter_hash`, `group_hash`, `action`, `reason`, `created_at`，联合唯一索引 `UNIQUE(item_id, instance_id, voter_hash, action)`
7. **`submitter_reputation`**：`submitter_hash` (PK), `user_token_hash`, `trust_level`, `approved_cnt`, `rejected_cnt`, `muted_until`
8. **`admins` & `audit_logs`**：RBAC 管理员（`super_admin` / `moderator`，GitHub OAuth 或 Argon2）与不可篡改审计日志。

### 6.2 插件端本地数据库（`sowing_v2.db`，SQLite）
1. **`items_cache`**：`uid` (PK, `hub:<hub_id>:<id>` / `rss:<hash>` / `local:<hash>`), `source_id`, `content_hash`, `payload_json`, `tags`, `nsfw_level`, `score`, `fetched_at`, `expires_at`
2. **`group_sent`**：`group_umo`, `item_uid`, `sent_at`, `bot_msg_id`（用于关联群友引用/贴表情反馈）, `trigger_type` (`ambient` | `manual`), `PRIMARY KEY(group_umo, item_uid)`
3. **`group_settings`**：`group_umo` (PK), `enabled`, `ambient_enabled`, `quiet_hours`, `cooldown_sec`, `max_nsfw`, `blocked_tags`, `llm_comment_prob`
4. **`source_cursors`**：`source_id` (PK), `hub_id`, `instance_id`, `instance_token`, `instance_salt`, `next_seq`, `etag`, `last_sync_at`, `fail_count`, `backoff_until`
5. **`feedback_outbox`**：`id` (PK), `hub_id`, `item_id`, `action`, `reason`, `voter_hash`, `group_hash`, `occurred_at`

---

## 七、服务端审核台与隐私安全关键细节

### 7.1 键盘流极速审史台（Web SPA）
- **单卡聚焦 + 媒体预加载**：提前预加载后 2 条待审媒体，减少等待。
- **快捷键操作（目标 2~3 秒/条）**：`A` 通过、`R` 拒绝（子键 `1` 重复 / `2` 违规 / `3` 质量低）、`N` 切换 `nsfw_level`、`T` 快选标签、`Space` 跳过。
- **辅助决策卡片**：
  - 实时提示「与库内条目 `#xxx` 视觉相似度 93%（疑似重复）」。
  - 展示投稿者历史过审率与信誉等级。
  - 正则自动检出文本中疑似 QQ 号/手机号/邮箱并标红警告，提醒审核员检查是否遗漏脱敏。
- **浏览器端聊天记录切片渲染流程**：
  1. 审核台把 `chat_record.messages` 渲染为统一 720px 宽的仿聊天记录 DOM 预览（审核员可实时所见即所得审阅内容与编辑脱敏别名）。
  2. 点击「通过」时，前端按完整对话气泡高度自动分组切页（每页高度 ≤ 1800px），调用 `modern-screenshot`/Canvas 导出 WebP 切片。
  3. 上传切片并随 `POST /api/v1/admin/items/{id}/review` 提交；服务端硬校验 `rendered_images` 非空后置为 `approved`。

### 7.2 隐私与防滥用双保险
- **插件端前置脱敏**：`/投稿` 时剥离群号、真实 QQ 号与头像；合并转发强制拍平至 `max_chat_depth=1, max_chat_nodes=20`，角色按出现顺序匿名化为 `群友A / 群友B` + `avatar_color_idx`；本地下载防盗链图片后直传 `/submissions/media`。
- **服务端兜底**：绝不落库明文 QQ 号、群号与真实 IP（全部加盐 HMAC）；图片强制清洗 EXIF/GPS 与 GIF 注释块；拒绝接受外部图片 URL 回源拉取（防 SSRF）。

---

## 八、AstrBot 插件 v2 详细架构设计

### 8.1 插件目录结构（`Source -> Pipeline -> Sink`）

```text
astrbot_plugin_sowing_discord/
├── metadata.yaml                # 插件元信息与版本声明 (v2.0)
├── _conf_schema.json            # AstrBot WebUI 可视化配置 Schema
├── main.py                      # Star 入口：生命周期、迁移检测、指令与事件注册
├── models/
│   └── meme.py                  # 统一数据模型 MemeItem / Block / FeedbackEvent
├── sources/                     # [Source 层] 可插拔多源采集
│   ├── base.py                  # BaseSource 抽象基类
│   ├── hub_source.py            # 官方/多 Hub 客户端（/meta、PoW、after_seq+ETag、tombstones）
│   ├── rss_source.py            # 通用 RSS/Atom/RSSHub 图文源 (Phase 2)
│   └── local_onebot_source.py   # 事件驱动本地源群（监听 group_msg_emoji_like，废除轮询）
├── pipeline/                    # [Pipeline 层] 去重、过滤、增强
│   ├── dedup.py                 # 全局 uid 去重 + 按群维度严格去重
│   ├── filter.py                # 按群 max_nsfw / 标签黑白名单 / 类型过滤
│   └── commentary.py            # 可选用户自有 LLM 简短吐槽（喂 title+summary+tags，5s 硬超时）
├── sinks/                       # [Sink 层] 跨平台投递
│   ├── astrbot_sink.py          # unified_msg_origin + MessageChain 全平台发送
│   └── media_cache.py           # 本地临时下载（LRU 200MB）+ 必要时 WebP->JPEG 软降级
├── ambient/
│   └── air_reader.py            # 五态「读空气」智能投放状态机（纯内存滑动窗口）
├── commands/
│   ├── manual_pull.py           # /来坨史 [tag]（兼容别名 /搬史）
│   ├── submit.py                # /投稿 [标签/附言]（脱敏、拦本地视频、去重早停）
│   ├── report.py                # /烂史 [原因]（引用 Bot 消息触发负反馈/举报）
│   └── admin.py                 # /史库 <状态|开关|订阅|屏蔽|分级|读空气>
├── feedback/
│   └── collector.py             # 采集互动信号（emoji_like/引用关键词/举报）并定时批量上报
├── storage/
│   └── db.py                    # SQLite 封装 (sowing_v2.db)
└── migration.py                 # v1 -> v2 配置自动备份与无损迁移
```

### 8.2 「读空气」智能投放状态机（纯内存，不存群聊原文）
按群独立维护 `last_msg_ts`、`last_sender_is_bot`、`msg_count_5m`、`human_msgs_today`：
1. **`QUIET_HOURS`**：免打扰时段（默认 `01:00–08:00`），禁发。
2. **`COOLDOWN`**：距上次搬史未满冷却期（默认 `90 分钟`），禁发。
3. **`ACTIVE_CHAT`**：近 5 分钟人类消息 `≥ 6` 条，群友正在热聊，**绝不打断**。
4. **`DEAD_GROUP`**：超 24 小时无任何人类发言，禁止向死群空投刷屏。
5. **`LULL_READY`（投放窗口）**：今日有活跃 + 距最后一条人类消息 `15~45 分钟`（聊完刚冷场）+ **最后一条消息不是 Bot 自己发的**（若上一坨史发完无人理，冷却时间自动翻倍退避，杜绝自说自话刷屏）。

### 8.3 跨平台投递策略
- **图片 / GIF**：通过 AstrBot `MessageChain` (`Plain` + `Image`) 发送；若平台对外部 URL 支持不佳（如微信/企微），经 `media_cache.py` 先落本地临时文件再 `Image.fromFileSystem()` 发送。
- **聊天记录（`chat_record`）**：全平台默认按序发送 `rendered_images` 切片图；QQ（`aiocqhttp`）端在 Phase 2 可选开启「尝试原生 `send_group_forward_msg`」，失败自动无缝回退切片图。
- **外链视频（`video_link`）**：渲染为「R2 托管封面图 + 标题 + 平台原链接」卡片消息。

### 8.4 老版本 v1 -> v2 平滑迁移（`migration.py`）
1. 启动时检测 v1 配置特征键（`banshi_group_list` / `banshi_target_list` / `banshi_cooldown_*` 等），先完整备份为 `config.v1.bak.json`。
2. `banshi_target_list` 自动映射为 v2 目标群名单；`banshi_group_list` 自动迁入 `local_onebot_source`（自动升级为事件驱动监听，彻底停用旧版轮询）；同时默认启用官方公益 Hub。
3. 在启动日志与 `/史库 状态` 中清晰提示老用户迁移结果，并告知可在 WebUI 一键关闭官方 Hub 仅保留私有源群。

---

## 九、三阶段并行落地路线图

### Phase 1：MVP 核心闭环（彻底替代老架构）
- **服务端 & Web**：
  - 搭建 FastAPI + SQLite(WAL) + Litestream + R2/CDN 基础工程；
  - 实现 `/meta`、`/instances/*`（16-bit PoW）、`/feed`（含 `global_seq` 与 `tombstones`）、`/random`、`/submissions/*`（WebP 转换、GIF 保留、pHash 查重）、`/admin/*`（审单与 Kill Switch）；
  - 上线 Cloudflare Pages 前端：Web 投稿页（Turnstile）+ 键盘流审核台（含浏览器端 `720×1800` 聊天记录切片渲染）。
- **插件端**：
  - 完成 `Source -> Pipeline -> Sink` 框架重构与 `sowing_v2.db`；
  - 对接官方 Hub（`/meta` + 无感 PoW + `after_seq` 增量与 `tombstones` 同步 + `/random` 补仓）；
  - 上线「读空气」状态机、`/来坨史`（别名 `/搬史`）、`/投稿`（脱敏 + 本地拉图 + 去重早停）、跨平台图文/GIF/切片图发送；
  - 交付 `v1 -> v2` 自动迁移与事件驱动 `local_onebot_source`。

### Phase 2：社区飞轮与个性增强
- **服务端 & 采集器**：
  - 上线独立单官方号投稿群采集器（规则初筛 + HMAC 签名入审）；
  - 上线 `/feedback/batch`、Wilson Score 排序、投稿者信誉分、`X-User-Token` 高优通道、3 实例满 24h 举报自动熔断、7 天拒审媒体清理 Cron。
- **插件端**：
  - 上线互动信号采集（`emoji_like` / 引用关键词 / `/烂史` 举报）与批量匿名上报；
  - 上线多 Hub 联邦订阅、通用 RSS 源、基于 `title + summary` 的用户自有纯文本 LLM 吐槽、QQ 原生合并转发可选切换。

### Phase 3：生态扩展（按社区资源按需开启）
- 视存储扩容情况，通过 `/meta` 能力开关解锁 `video_file`（投稿端预限 ≤15s/≤8MB MP4，`ffprobe` 校验不做软转码，30 天冷淘汰 + 静态播放短链）；
- 上线 Web 公共内容广场、日/周「史王排行榜」、多审核员协作与细粒度权限审计面板。
