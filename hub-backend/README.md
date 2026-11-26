# sowing-discord-hub Backend (`hub-backend`)

FastAPI + SQLite (WAL) + Cloudflare R2 / Local Storage backend implementing Schema v1.0 for AstrBot Sowing Discord v2.

## Quick Start (Local Development)

```powershell
# 1. Copy example env
copy .env.example .env

# 2. Run API server (uses local SQLite + local media storage by default)
.\.venv\Scripts\uvicorn.exe app.main:app --reload --host 0.0.0.0 --port 8000
```

- **Default Dev Admin**: `admin` / `admin123456` (seeded automatically on startup when `admins` table is empty; override via `ADMIN_USERNAME` and `ADMIN_PASSWORD`).
- **API Docs**: `http://localhost:8000/docs`
- **Health Check**: `GET /healthz`

## Scheduled Cleanup Job

Deletes media blobs of `rejected` items after 7 days (and stale unreviewed `pending` items after 21 days) while preserving `sha256` and `phash` hashes to prevent re-submissions:

```powershell
.\.venv\Scripts\python.exe -m app.jobs
```

## Running Tests

```powershell
.\.venv\Scripts\pytest.exe -v
```
