# AI Office Platform

Multi-tenant AI back-office for small businesses: CRM, AI receptionist, scheduling,
quotes → jobs → invoices → payments (Stripe Connect), automations, and industry intel —
with a public website, waitlist mode, and client portals. Built to deploy cheaply:
frontend on **Cloudflare Pages**, backend as a **Docker container** on any VM
(Oracle Always Free, Fly, Hetzner, …), MongoDB on Atlas.

## Repo layout

```
backend/            FastAPI backend (44 routers in backend/routers/)
frontend/           React 19 SPA (Create React App + Tailwind), built to frontend/build
docker-compose.yml  Backend + optional local Mongo for portable VM deploys
wrangler.toml       Cloudflare Pages project config (name: empire-ai-office)
docs/               Deploy runbooks, ops checklists, Cloudflare notes
test_reports/       JSON results of backend test iterations
scripts/            Repo-level helpers (i18n parity check)
HANDOFF.md          Session handoff: round status, env inventory, deploy commands
```

## Run locally

**Backend** (needs Python 3.11 and a MongoDB):

```bash
cd backend
cp .env.example .env        # fill in values; NEVER commit .env
pip install -r requirements.txt
uvicorn server:app --port 8001
```

**Frontend:**

```bash
cd frontend
yarn install --frozen-lockfile
REACT_APP_BACKEND_URL=http://localhost:8001 yarn start
```

Or run the whole portable stack: `docker-compose up --build` (backend on
`127.0.0.1:8001` + local Mongo; point `MONGO_URL` at Atlas to skip the local DB).

## Deploy

**Frontend → Cloudflare Pages** (dashboard → Pages → Connect to GitHub):
- Build command: `cd frontend && yarn install --frozen-lockfile --non-interactive && yarn build`
- Build output: `frontend/build`
- Production env: `REACT_APP_BACKEND_URL=https://api.your-domain.com`

**Backend → Docker on any VM** (see `docs/deploy/` for runbooks):

```bash
cd backend
cp .env.example .env   # fill locally; NEVER commit
docker build -t ai-office-backend .
docker run -d --name aio --env-file .env -p 8001:8001 ai-office-backend
curl -fsS https://api.your-domain.com/api/health/deployment
```

## Environment

All variable names, purpose, and which are required live in
`backend/.env.example` (checked in, no values). Highlights:

| Group | Key variables |
|---|---|
| Core | `MONGO_URL`, `DB_NAME`, `JWT_SECRET`, `CORS_ORIGINS`, `FRONTEND_URL` |
| Admin | `ADMIN_EMAIL`, `ADMIN_PASSWORD` (first seed only) |
| AI | one of `AI_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY` |
| Payments | `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_WEBHOOK_SECRET` |
| Email | `RESEND_API_KEY` + `RESEND_FROM_EMAIL` (operator-owned Resend account) |
| Crons | `WEBHOOK_CRON_SECRET` |
| Voice/SMS | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER` |

## Tests

```bash
cd backend
python -m pytest tests/ -q
```

Results are archived per iteration in `test_reports/`.

## Docs

- `HANDOFF.md` — round-by-round status, branch plan, full env inventory, deploy commands
- `docs/deploy/` — VM, Cloudflare, waitlist-only, and merge-order runbooks
- `docs/cloudflare/` — Cloudflare-specific audit, env inventory, ops checklists
- `memory/PRD.md` — product requirements
