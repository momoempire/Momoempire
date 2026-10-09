# EMP-DEV-003 — Linode VPS deployment runbook (FastAPI stack)

**Branch context:** `Cloudflare-2`  
**Date:** 2026-10-08 (America/Chicago)  
**Status:** Documentation only. No servers provisioned, no DNS changes, no purchases, no deploys.

---

## Tracker discrepancy (read first)

The Empire tracker text for EMP-DEV-003 mentions a **Node/Express** stack. That is **incorrect for this repo**.

| Tracker said | Actual stack on `Cloudflare-2` |
| --- | --- |
| Node / Express | **Python FastAPI + Uvicorn** (`backend/server.py`, `fastapi==0.110.1`) |
| (implied Node host) | **Motor / MongoDB** (`backend/db.py`) |
| — | **React CRA/CRACO** frontend (`frontend/`), typically on **Cloudflare Pages** |

This runbook is written for the **actual** FastAPI stack. Aligns with ChatGPT’s `docs/cloudflare/ops-runbook.md` and EMP-CF-002 (`/workspace/momoempire-cf/EMP-CF-002-runtime-target.md` / repo `docs/cloudflare/runtime-notes.md`): **Pages for SPA + separate Docker host for API**.

---

## Recommended topology (Brann decides hosts)

| Layer | Recommended | Alternative |
| --- | --- | --- |
| Frontend | Cloudflare Pages (`frontend/build`) | Nginx on this VPS serving `frontend/build` |
| Backend | **This Linode** — Docker image from `backend/Dockerfile` (port **8001**) | venv + systemd (briefly below) |
| DB | **MongoDB Atlas** (M0 free for pilot; see limits) | Compose `mongo` service (dev only; compose file says Atlas for prod) |
| Crons | **systemd timers or crontab** on the VPS curling `/api/cron/*` with Bearer secret | Optional CF Cron Trigger Worker that only POSTs (EMP-CF-002) |
| TLS | Nginx + Certbot on the VPS for `api.<domain>` | — |

**Do not** expect FastAPI+Motor to run on Cloudflare Workers as-is (EMP-CF-002).

---

## Cost estimates (labeled; not purchases)

Sources checked 2026-10-08:

| Item | Estimate | Source |
| --- | --- | --- |
| Linode Shared CPU **Nanode 1 GB** | **~$5/mo** ($0.0075/hr) | [Akamai Cloud NA pricing](https://www.akamai.com/cloud/pricing/north-america) |
| Linode Shared CPU **2 GB** | **~$12/mo** | same |
| Linode Shared CPU **4 GB** | **~$24/mo** | same |
| MongoDB Atlas **M0** | **$0** (512 MB storage; free forever tier) | [MongoDB pricing](https://www.mongodb.com/pricing) |
| Extra Linode egress | **~$0.005/GB** over plan transfer (most regions) | [Akamai Essential Compute](https://www.akamai.com/products/essential-compute) |

**Sizing note (estimate, not a product decision):** Nanode 1 GB is tight for Docker + Nginx + peak RAG parse; **2 GB Shared** is a safer pilot floor. Brann chooses.

---

## 0. Prerequisites (before any Linode create)

1. Domain / subdomain decisions: e.g. `api.example.com` for backend; marketing site on Pages or apex.
2. MongoDB Atlas project (or approval to create one) — **Brann must approve** any signup.
3. Secrets inventory (names only until CF-004): see §4 and `docs/cloudflare/env-inventory.md`.
4. Read `docs/cloudflare/ops-runbook.md` for prelaunch / rollback philosophy — this doc is the Linode-shaped expansion of that plan.
5. Explicit Brann approval before: creating a Linode, spending money, changing DNS, or enabling production billing/Stripe live keys.

---

## 1. Create Ubuntu LTS Linode (manual — when approved)

1. Cloud Manager → Create Linode → **Ubuntu 24.04 LTS** (or current LTS).
2. Region near users / Atlas region.
3. Plan: see cost table (Brann picks).
4. Set a strong root password or SSH key at create time.
5. Note public IPv4.

*(No create step is executed by this task.)*

---

## 2. User, SSH hardening, UFW

Run as root on a fresh box (illustrative):

```bash
# Non-root deploy user
adduser --disabled-password --gecos "" deploy
usermod -aG sudo deploy
mkdir -p /home/deploy/.ssh
# Install YOUR pubkey (not in git):
# echo 'ssh-ed25519 AAAA…' >> /home/deploy/.ssh/authorized_keys
chmod 700 /home/deploy/.ssh
chmod 600 /home/deploy/.ssh/authorized_keys
chown -R deploy:deploy /home/deploy/.ssh

# SSH hardening (edit /etc/ssh/sshd_config.d/99-momoempire.conf)
cat >/etc/ssh/sshd_config.d/99-momoempire.conf <<'SSHEOF'
PermitRootLogin no
PasswordAuthentication no
PubkeyAuthentication yes
ChallengeResponseAuthentication no
SSHEOF
sshd -t && systemctl reload ssh

# Firewall
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
ufw status
```

Do **not** expose MongoDB (27017) or Uvicorn (8001) publicly — only Nginx 80/443.

---

## 3. Install Docker (primary path) + Nginx + Certbot

Primary path matches `backend/Dockerfile` and `docker-compose.yml` (API on **8001**).

```bash
# As deploy (with sudo)
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg nginx certbot python3-certbot-nginx

# Docker Engine (follow current official Ubuntu install):
# https://docs.docker.com/engine/install/ubuntu/
# Then:
sudo usermod -aG docker deploy
# re-login so docker group applies
docker --version
```

Clone the approved branch (read-only ops clone; deploy keys preferred):

```bash
sudo mkdir -p /opt/momoempire
sudo chown deploy:deploy /opt/momoempire
cd /opt/momoempire
git clone --branch Cloudflare-2 https://github.com/momoempire/Momoempire.git app
# Or pin a release tag once Brann tags one.
cd app
```

### 3a. Docker path (recommended)

```bash
cd /opt/momoempire/app
# Create secrets file OUTSIDE git (see §4)
# cp backend/.env.example /opt/momoempire/secrets/backend.env
# chmod 600 /opt/momoempire/secrets/backend.env
# edit values…

# Build from backend/Dockerfile (PORT=8001, health → /api/health)
docker build -t ai-office-backend:$(git rev-parse --short HEAD) ./backend
docker tag ai-office-backend:$(git rev-parse --short HEAD) ai-office-backend:latest

# Run — bind localhost only; Nginx proxies
docker run -d --name momoempire-api --restart unless-stopped \
  --env-file /opt/momoempire/secrets/backend.env \
  -p 127.0.0.1:8001:8001 \
  ai-office-backend:latest

docker ps
curl -fsS http://127.0.0.1:8001/api/health
```

Optional: use `docker-compose.yml` but **point `MONGO_URL` at Atlas** and omit/disable the local `mongo` service for production (compose comment already recommends Atlas Free).

### 3b. venv + systemd alternative (brief)

If Brann prefers bare metal Python instead of Docker:

```bash
sudo apt-get install -y python3.11-venv build-essential libffi-dev
cd /opt/momoempire/app/backend
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
# systemd unit: WorkingDirectory=/opt/momoempire/app/backend
# ExecStart=.../uvicorn server:app --host 127.0.0.1 --port 8001 --proxy-headers
# EnvironmentFile=/opt/momoempire/secrets/backend.env
```

Same Nginx front; same cron curls. Prefer Docker for parity with Dockerfile HEALTHCHECK and CF ops docs.

---

## 4. Environment setup (names only; permissions 600)

1. Copy **names** from `backend/.env.example` into `/opt/momoempire/secrets/backend.env`.
2. `chmod 600` that file; owner `deploy` (or root-readable only by the runtime user).
3. **Never** commit `.env` or real values. Confirm `.gitignore` covers `.env`.
4. Set values only in the host secret store / this file after CF-004 / Brann approval.

### Names from `backend/.env.example` (verified present)

| Name | Role |
| --- | --- |
| `MONGO_URL` | Atlas SRV connection string |
| `DB_NAME` | Database name |
| `JWT_SECRET` | Auth signing |
| `CORS_ORIGINS` | Explicit SPA origin(s), comma-separated |
| `FRONTEND_URL` | Canonical frontend URL |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | Bootstrap admin (private) |
| `OPENAI_API_KEY` or `EMERGENT_LLM_KEY` | LLM |
| `RESEND_API_KEY` or `EMERGENT_EMAIL_KEY` | Email |
| `STRIPE_SECRET_KEY` / `STRIPE_PUBLISHABLE_KEY` / `STRIPE_WEBHOOK_SECRET` / `STRIPE_CONNECT_WEBHOOK_SECRET` | Payments |
| `WEBHOOK_CRON_SECRET` | Bearer for `/api/cron/*` |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` / `TWILIO_PHONE_NUMBER` | Optional telephony |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `GOOGLE_REDIRECT_URI` | Optional OAuth |

Also referenced in code / health probe (set if used): see `docs/cloudflare/env-inventory.md` and `backend/routers/health_deploy.py` (`_REQUIRED` / `_OPTIONAL`).

Frontend **build-time** (Pages, not VPS env file): `REACT_APP_BACKEND_URL` → HTTPS API origin (`frontend/src/lib/api.js`).

---

## 5. MongoDB Atlas

1. Create / use cluster (M0 for pilot if Brann approves free tier).
2. **Network Access:** allowlist the Linode’s public IPv4 (prefer IP allowlist over `0.0.0.0/0`).
3. Database user with least privilege on `DB_NAME`.
4. Connection string: Atlas **SRV** form `mongodb+srv://…` → `MONGO_URL`.
5. **M0 limits (estimate / product docs):** **512 MB** storage, shared RAM/CPU, free forever — fine for early pilot; not a long-term production size ([MongoDB pricing](https://www.mongodb.com/pricing)).
6. Note: Atlas free tier **backup features are limited**; plan off-box `mongodump` (§10).

---

## 6. Nginx reverse proxy + Certbot

Install site config based on `docs/deploy/nginx-api.example.conf`:

- Upstream `127.0.0.1:8001`
- `location /api/` → backend
- `client_max_body_size 12m` — app enforces **10MB** max upload in `knowledge_docs.py` (`10 * 1024 * 1024`)
- `proxy_buffering off` + long read/send timeouts for streaming zip (`StreamingResponse` in `source_export.py`)
- `Upgrade` / `Connection` headers for WebSocket-capable proxying (no WS routes required today; safe)

```bash
sudo cp docs/deploy/nginx-api.example.conf /etc/nginx/sites-available/momoempire-api
# edit server_name + ssl paths
sudo ln -sf /etc/nginx/sites-available/momoempire-api /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx

# After DNS A/AAAA for api.<domain> points here (Brann-approved):
sudo certbot --nginx -d api.example.com
```

Point Stripe / Twilio webhook URLs at `https://api.<domain>/api/...` after TLS works.

### Frontend: Pages vs VPS

| Option | How |
| --- | --- |
| **A — Cloudflare Pages (recommended by EMP-CF-002 / ops-runbook)** | Build `frontend/` → `frontend/build`; set `REACT_APP_BACKEND_URL=https://api.<domain>`; CORS_ORIGINS / FRONTEND_URL = Pages URL |
| **B — Nginx on VPS** | `npm ci && npm run build` (or yarn when lockfile exists — CF-003); `root /opt/momoempire/app/frontend/build;` + SPA `try_files`; same CORS to that origin |

Do not run CRA `start` in production.

---

## 7. Cron jobs → systemd timers or crontab

All jobs require:

```http
Authorization: Bearer <WEBHOOK_CRON_SECRET>
```

(`backend/routers/cron.py` `_authorized`). Schedules from `.emergent/crons.yml` (UTC). Endpoints verified in `backend/routers/cron.py`.

| Job | Schedule (UTC) | Endpoint |
| --- | --- | --- |
| followups-dispatch | `*/15 * * * *` | `POST /api/cron/followups` |
| callbacks-due | `*/5 * * * *` | `POST /api/cron/callbacks-due` |
| appt-reminders | `15 * * * *` | `POST /api/cron/appointment-reminders` |
| appt-confirmations | `25 * * * *` | `POST /api/cron/appt-confirmations` |
| overage-nightly | `30 2 * * *` | `POST /api/cron/overage-nightly` |
| daily-standup | `0 13 * * *` | `POST /api/cron/daily-standup` |
| repeat-reminders | `30 13 * * *` | `POST /api/cron/repeat-reminders` |
| overdue-reminders | `15 14 * * *` | `POST /api/cron/overdue-reminders` |
| weekly-digest | `0 14 * * 1` | `POST /api/cron/weekly-digest` |

### Crontab example (on the VPS; hits localhost via Nginx or direct)

```bash
# /etc/cron.d/momoempire — install secret via EnvironmentFile pattern or root-only file
SHELL=/bin/bash
BASE=http://127.0.0.1:8001
# Prefer reading secret from a 600 file:
# SECRET=$(cat /opt/momoempire/secrets/cron.token)

*/15 * * * * deploy curl -fsS -X POST -H "Authorization: Bearer $(cat /opt/momoempire/secrets/cron.token)" "$BASE/api/cron/followups" >/dev/null
*/5  * * * * deploy curl -fsS -X POST -H "Authorization: Bearer $(cat /opt/momoempire/secrets/cron.token)" "$BASE/api/cron/callbacks-due" >/dev/null
15   * * * * deploy curl -fsS -X POST -H "Authorization: Bearer $(cat /opt/momoempire/secrets/cron.token)" "$BASE/api/cron/appointment-reminders" >/dev/null
25   * * * * deploy curl -fsS -X POST -H "Authorization: Bearer $(cat /opt/momoempire/secrets/cron.token)" "$BASE/api/cron/appt-confirmations" >/dev/null
30   2 * * * deploy curl -fsS -X POST -H "Authorization: Bearer $(cat /opt/momoempire/secrets/cron.token)" "$BASE/api/cron/overage-nightly" >/dev/null
0   13 * * * deploy curl -fsS -X POST -H "Authorization: Bearer $(cat /opt/momoempire/secrets/cron.token)" "$BASE/api/cron/daily-standup" >/dev/null
30  13 * * * deploy curl -fsS -X POST -H "Authorization: Bearer $(cat /opt/momoempire/secrets/cron.token)" "$BASE/api/cron/repeat-reminders" >/dev/null
15  14 * * * deploy curl -fsS -X POST -H "Authorization: Bearer $(cat /opt/momoempire/secrets/cron.token)" "$BASE/api/cron/overdue-reminders" >/dev/null
0   14 * * 1 deploy curl -fsS -X POST -H "Authorization: Bearer $(cat /opt/momoempire/secrets/cron.token)" "$BASE/api/cron/weekly-digest" >/dev/null
```

`cron.token` must equal `WEBHOOK_CRON_SECRET` (`chmod 600`). Routes ack 2xx then `asyncio.create_task` work in-process — Uvicorn must stay up.

systemd timers: one `oneshot` service + timer per schedule with the same `curl` ExecStart is fine if Brann prefers timers over cron.

---

## 8. Health checks

| Check | URL | Notes |
| --- | --- | --- |
| Liveness | `GET /api/health` | Dockerfile HEALTHCHECK; compose healthcheck |
| Deploy probe | `GET /api/health/deployment` | DB round-trip, env **names** present, LLM backend label (`health_deploy.py`) |

```bash
curl -fsS https://api.example.com/api/health
curl -fsS https://api.example.com/api/health/deployment
docker inspect --format='{{.State.Health.Status}}' momoempire-api
```

Wire uptime monitoring (Brann picks vendor) to `/api/health`. Aligns with ops-runbook “API availability” alerts.

---

## 9. Log rotation

- **Docker:** `json-file` driver with `max-size` / `max-file`, or ship to a host file via `docker logs`.
- **Nginx:** `/var/log/nginx/*.log` — ensure `logrotate` package present (Ubuntu default).
- **systemd (venv path):** `journalctl -u momoempire-api` + `SystemMaxUse=` in journald if needed.

Do not log secrets, raw Stripe payloads, or full JWTs.

---

## 10. Backups (mongodump → off-box)

M0 has limited managed backup — schedule dumps:

```bash
# On a secure runner with Atlas URI (never commit the URI)
mkdir -p /opt/momoempire/backups
mongodump --uri="$MONGO_URL" --db="$DB_NAME" --out="/opt/momoempire/backups/$(date -u +%Y%m%dT%H%M%SZ)"
# Encrypt + copy to off-box object storage (Linode Object Storage / S3-compatible) — Brann chooses bucket.
# Retain N days; test restore before launch (ops-runbook requirement).
```

Restore dry-run:

```bash
mongorestore --uri="$MONGO_URL" --db="$DB_NAME" --drop /path/to/dump/$DB_NAME
# Prefer restore into a staging DB first.
```

---

## 11. Rollback

Matches `docs/cloudflare/ops-runbook.md` rollback section, Linode-shaped:

1. **Record** before each release: git SHA / image tag (`ai-office-backend:<sha>`), Pages deployment ID (if used), dump timestamp.
2. **Backend image:**
   ```bash
   docker stop momoempire-api && docker rm momoempire-api
   docker run -d --name momoempire-api --restart unless-stopped \
     --env-file /opt/momoempire/secrets/backend.env \
     -p 127.0.0.1:8001:8001 \
     ai-office-backend:<previous-sha>
   ```
3. **Git tag:** if using venv path, `git checkout <previous-tag>` and restart unit.
4. **Frontend:** restore prior Cloudflare Pages deployment **or** prior `frontend/build` tarball on Nginx.
5. **DB:** restore last known-good `mongodump` only if schema/data is bad — verify on staging first; avoid schema changes without a tested rollback.
6. **DNS:** only if authorized (ops-runbook); usually image/Pages rollback is enough.
7. Re-check `/api/health` and `/api/health/deployment`, Stripe webhook delivery, one cron smoke POST.

---

## 12. Alignment with ChatGPT Cloudflare ops docs

| Topic | This runbook | `docs/cloudflare/ops-runbook.md` |
| --- | --- | --- |
| Frontend | Pages preferred; VPS Nginx optional | Pages builds React |
| Backend | Linode Docker HTTPS via Nginx | Separate HTTPS Docker service |
| Secrets | Host file 600 / secret store; names from `.env.example` | Approved hosting secret stores |
| Rollback | Previous image tag + DB dump | Preserve last known-good Pages + image |
| Open | Host/domain/plan still Brann’s call | Same open dependencies |

Does **not** contradict runtime-notes / EMP-CF-002: Workers are not the API host.

---

## 13. Open decisions for Brann only

1. **Linode vs other Docker host** (Fly, Railway, Oracle free, etc.)?
2. **Plan size** (Nanode 1 GB vs 2 GB / 4 GB)?
3. **Docker vs venv+systemd**?
4. **Frontend on Cloudflare Pages vs Nginx on the VPS**?
5. **API hostname** and DNS cutover timing?
6. **Atlas M0 vs paid tier** and backup destination (which off-box bucket)?
7. Approval to create the Linode / Atlas project / Certbot certificates?

---

## Appendix A — Verification checklist (docs PR)

- [x] Every cron path in §7 exists in `backend/routers/cron.py` and `.emergent/crons.yml`
- [x] Every env **name** in §4 appears in `backend/.env.example`
- [x] Upload size note matches `knowledge_docs.py` 10MB guard
- [x] Health paths match `server.py` + `health_deploy.py`
- [x] Sample Nginx config syntax-checked (`docs/deploy/nginx-test.conf` via `nginx -t`)

---

## Appendix B — Related docs

- `docs/cloudflare/ops-runbook.md`
- `docs/cloudflare/runtime-notes.md`
- `docs/cloudflare/env-inventory.md`
- EMP-CF-002 report (workspace): `/workspace/momoempire-cf/EMP-CF-002-runtime-target.md`
- `backend/Dockerfile`, `docker-compose.yml`, `.emergent/crons.yml`
- `docs/deploy/nginx-api.example.conf`, `docs/deploy/nginx-test.conf`
