"""Admin: downloadable source zip for easy duplication of the AI Office platform."""
import io
import os
import zipfile
from pathlib import Path
from datetime import datetime, timezone
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from security import require_platform_admin

router = APIRouter(prefix="/admin/source", tags=["admin-source"],
                   dependencies=[Depends(require_platform_admin)])

APP_ROOT = Path("/app")

# What to exclude from the zip (paths or path parts)
EXCLUDE_DIRS = {
    "node_modules", ".git", ".emergent", "__pycache__", ".cache",
    ".venv", "venv", "dist", "build", ".next", ".parcel-cache",
    "test_reports", "uploads", "coverage", ".pytest_cache",
}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".log", ".lock"}  # keep yarn.lock? we skip to keep zip small; package.json captures intent
EXCLUDE_FILES = {".DS_Store", ".env"}  # real secrets excluded


README_CONTENT = """# AI Office Platform — Self-host / Duplicate

This is a snapshot of the full application. Follow these steps to run it anywhere.

## Stack
- Backend: FastAPI (Python 3.11+) + MongoDB
- Frontend: React (CRA + Craco) + Tailwind + shadcn/ui
- Auth: JWT (email/password) + Emergent-managed Google OAuth
- Payments: Stripe Checkout (subscriptions + overage invoice items)
- LLM: Emergent LLM key (OpenAI / Claude / Gemini via `emergentintegrations`)

## Prerequisites
- Python 3.11, Node 18+, yarn, MongoDB 6+
- A Stripe secret key (test or live)
- An Emergent LLM key for AI features

## Backend
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# install emergentintegrations
pip install emergentintegrations --extra-index-url https://d33sy5i8bnduwe.cloudfront.net/simple/

cp .env.example .env            # fill in values below
uvicorn server:app --host 0.0.0.0 --port 8001 --reload
```

Required backend env:
```
MONGO_URL=mongodb://localhost:27017
DB_NAME=aioffice
JWT_SECRET=<long random>
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
EMERGENT_LLM_KEY=<provided by Emergent>
FRONTEND_URL=http://localhost:3000
```

## Frontend
```bash
cd frontend
yarn install
cp .env.example .env            # set REACT_APP_BACKEND_URL to backend origin
yarn start                      # http://localhost:3000
```

Required frontend env:
```
REACT_APP_BACKEND_URL=http://localhost:8001
```

> All backend routes are prefixed with `/api`. The frontend uses `REACT_APP_BACKEND_URL + /api/...`.

## First-run
- Backend auto-seeds industries, countries, plans and the admin user on startup.
- Platform admin: set `ADMIN_EMAIL` (and optionally a strong `ADMIN_PASSWORD`) before first start; there is no default admin or password
- Visit `/pricing` to see live tiered plans (reads `/api/plans`).

## Admin workflow
1. Log in → `/admin/plans` → paste real Stripe Price IDs on each plan.
2. Set overage rates per plan (cents per unit for ai_minutes / calls / sms).
3. Deploy; webhook endpoint lives at `POST /api/stripe/webhook`.
4. Nightly overage: run `POST /api/admin/overage/run` (or schedule a cron).

## Routes you'll want to try
- `/` — marketing landing
- `/pricing` — public plan grid
- `/signup` → `/app` — tenant workspace
- `/admin` — platform admin console

## Troubleshooting
- Mongo connection: double-check `MONGO_URL` + `DB_NAME`.
- Cookies: cross-site auth uses `SameSite=None; Secure`. Use HTTPS or `localhost` in both envs.
- Stripe Checkout: without `stripe_price_id` on a plan, we fall back to `price_data` (ad-hoc). Set the real Price IDs for proper subscription management.

Enjoy your AI Office. ✨
"""

BACKEND_ENV_EXAMPLE = """MONGO_URL=mongodb://localhost:27017
DB_NAME=aioffice
JWT_SECRET=replace-with-long-random-string
STRIPE_SECRET_KEY=sk_test_replace_me
STRIPE_WEBHOOK_SECRET=whsec_replace_me
EMERGENT_LLM_KEY=replace-with-your-emergent-llm-key
FRONTEND_URL=http://localhost:3000
"""

FRONTEND_ENV_EXAMPLE = """REACT_APP_BACKEND_URL=http://localhost:8001
"""


def _should_skip(rel: Path) -> bool:
    parts = set(rel.parts)
    if parts & EXCLUDE_DIRS:
        return True
    if rel.name in EXCLUDE_FILES:
        return True
    if rel.suffix in EXCLUDE_SUFFIXES:
        return True
    return False


def _iter_files(root: Path):
    for p in root.rglob("*"):
        if p.is_dir():
            continue
        try:
            rel = p.relative_to(root)
        except ValueError:
            continue
        if _should_skip(rel):
            continue
        yield p, rel


def _build_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for p, rel in _iter_files(APP_ROOT):
            try:
                zf.write(p, arcname=str(Path("ai-office-platform") / rel))
            except (OSError, PermissionError):
                continue
        zf.writestr("ai-office-platform/README.DUPLICATE.md", README_CONTENT)
        zf.writestr("ai-office-platform/backend/.env.example", BACKEND_ENV_EXAMPLE)
        zf.writestr("ai-office-platform/frontend/.env.example", FRONTEND_ENV_EXAMPLE)
    buf.seek(0)
    return buf.getvalue()


@router.get("/zip")
async def download_source_zip():
    data = _build_zip()
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    headers = {"Content-Disposition": f'attachment; filename="ai-office-platform-{ts}.zip"'}
    return StreamingResponse(io.BytesIO(data), media_type="application/zip", headers=headers)


@router.get("/manifest")
async def manifest():
    """Lightweight info about what will be included (file count + size)."""
    count = 0
    bytes_ = 0
    for p, _rel in _iter_files(APP_ROOT):
        try:
            bytes_ += p.stat().st_size
            count += 1
        except OSError:
            continue
    return {"files": count, "approx_bytes_raw": bytes_, "excludes": sorted(EXCLUDE_DIRS)}
