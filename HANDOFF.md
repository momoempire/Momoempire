# HANDOFF — AI Office Platform (Rounds 1 & 2 complete)

> **Resume here.** Rounds 1 (Cloudflare prep) and 2 (call-to-payment) are **code-complete and committed locally**. Nothing has been pushed to GitHub, deployed, or purchased. Round 3 (Spanish/voice) is **not started**.

## Final status snapshot — what the agent did

| | Branch target (your plan) | Local commit SHA | Status |
|---|---|---|---|
| Round 1 — Cloudflare prep | `cloudflare-ready` | **`54d84cf`** (checkpoint at round complete) | Code committed, tested in-preview, not pushed |
| Round 2 — Call-to-payment | `call-to-payment` | **`e6dc83f`** (current HEAD) | Code committed, 31/31 pytest, not pushed |
| Round 3 — Spanish / voice | `spanish-voice-finish` | — | **Not started.** No commits. |
| Round 0 — Phase 11 (prior) | — | `dd1d2db` | Already in your history. |

Local branch state: **only `main` exists in `/app`.** All my commits sit linearly on top of the previous Phase 11 head. The three-branch split you asked for happens **after** you click Save to GitHub (see below).

## What is saved vs. what still needs your click

### Saved locally in `/app` (auto-committed by Emergent after each turn)
- ✅ `backend/llm_portable.py`, `backend/routers/health_deploy.py`, `backend/routers/call_to_payment.py`
- ✅ Edits to `backend/server.py`, `backend/ai_receptionist.py`, `backend/ai_insights.py`, and 5 other routers (import swap only)
- ✅ `backend/Dockerfile`, `backend/.dockerignore`, `docker-compose.yml`
- ✅ `frontend/public/_redirects`, `frontend/public/_headers`, `wrangler.toml`
- ✅ `.emergent/crons.yml` (overdue-reminders + repeat-reminders schedules)
- ✅ `/app/HANDOFF.md`, `/app/memory/PRD.md`, `/app/memory/test_credentials.md`
- ✅ `backend/tests/test_call_to_payment.py`, `backend/tests/test_iteration10_cloudflare_prep.py`
- ✅ `test_reports/iteration_10.json`, `test_reports/iteration_11.json`

### Still needs YOU to click "Save to GitHub" in the chat input
I do **not** have `git push` rights from this environment. Everything above lives only on this pod until you push.

## How to save to the three branches you want

> Reality check: Emergent's **Save to GitHub** button pushes the current `main` **as-is** to the branch name you pick. All my commits are already on local `main`; I cannot split them into three branches from here. Pick ONE of the two options below.

### Option A — Simplest (recommended): one combined branch now, split later if needed

1. Click **Save to GitHub** in the chat input.
2. Target branch: `cloudflare-and-call-to-payment` (or `empire-market-expansion-rounds-1-2`).
3. Open a PR against `main` on GitHub. Review by commit (the Round 1 and Round 2 boundaries at SHAs `54d84cf` and `e6dc83f` are clean checkpoints).
4. Leave `main` and `empire-market-expansion` **untouched** on GitHub — nothing in this handoff is destined for either.

If you later want the formal three-branch split, do it from your laptop after the push:
```bash
git fetch origin
git checkout -b cloudflare-ready 54d84cf    # or the matching SHA on your remote
git push origin cloudflare-ready
git checkout -b call-to-payment e6dc83f
git push origin call-to-payment
```

### Option B — Three branches via the UI, one at a time
If the Save to GitHub button supports choosing which commit to push, click it three times:
1. First push → branch `cloudflare-ready`, cutoff at commit `54d84cf`.
2. Second push → branch `call-to-payment`, cutoff at commit `e6dc83f`.
3. Round 3 branch (`spanish-voice-finish`) is not yet created; skip.

If the button only pushes HEAD, use Option A — the branch split is a safe post-push `git` operation.

## Round 3 — unfinished work (next session's starting point)

### 3a. Finish Stripe test-mode verification (can be done on current infra, no money)
- [ ] Enable Connect at https://dashboard.stripe.com/connect in your **test** Stripe account (the one already in `STRIPE_SECRET_KEY`).
- [ ] Call `POST /api/c2p/connect/onboard` — expect a real hosted link now instead of 503.
- [ ] Walk through the Stripe hosted onboarding form (test data auto-approve).
- [ ] Call `POST /api/public/c2p/invoices/{token}/pay` with the test invoice — expect a `client_secret`.
- [ ] Run `stripe listen --forward-to <preview>/api/stripe/connect-webhook` and complete a test card charge (4242 4242 4242 4242). Confirm invoice flips to `paid` live.
- [ ] Verify refund flow with `stripe refunds create`.

### 3b. Spanish translations (framework already in place; keys exist)
- [ ] `frontend/src/pages/dashboard/Billing.jsx` — replace hardcoded strings with keys from `src/i18n/locales/en.json` (`billing.*`).
- [ ] `frontend/src/pages/dashboard/Analytics.jsx` — use `analytics.*` keys (already defined in `en.json`).
- [ ] `frontend/src/pages/dashboard/Automations.jsx` — use `automations.*` keys.
- [ ] `frontend/src/pages/PublicBusiness.jsx` — use `public_business.*` keys (already defined), add the testimonials carousel block.
- [ ] Add equivalent entries to `src/i18n/locales/es.json` for the keys above (currently only `en.json` has them).
- [ ] Localize the new Round 2 emails (quote-sent and overdue-reminder — the overdue cron already branches on `tenant.lang`).

### 3c. Live homepage voice demo (deferred by prior decision)
- [ ] Replace the text-only `components/DemoCall.jsx` chat with a WebRTC voice path.
- [ ] Needs: `OPENAI_API_KEY` **or** an ElevenLabs/Deepgram realtime key — ask the user.
- [ ] Add a browser `getUserMedia` mic flow, streaming to the chosen realtime API, output played via Web Audio.
- [ ] Keep the current text demo as a fallback for browsers that deny mic.

### 3d. Nice-to-haves surfaced during Round 2
- Testimonials carousel on `/b/:slug` (keys already exist in `en.json`; needs ES + the React component).
- Owner UI pages for `/c2p/quotes`, `/c2p/invoices`, and `/c2p/needs-attention` (API complete; no frontend yet).
- Customer portal pages for `/q/:token` and `/i/:token` (the two public API endpoints are live and already linked from emails).

## Deployment & env — commands only, no secrets

### Frontend → Cloudflare Pages
```bash
# Cloudflare dashboard → Pages → Create → Connect to GitHub → momoempire/Momoempire
# Choose branch: whatever you push in step above
# Build command:  cd frontend && yarn install --frozen-lockfile && yarn build
# Build output:   frontend/build
# Env vars (Production):
#   REACT_APP_BACKEND_URL = https://api.your-domain.com     ← no secret, just URL
```

### Backend → Docker on Oracle Free ARM / Fly / Hetzner / any VM
```bash
cd backend
cp .env.example .env            # fill locally; NEVER commit
docker build -t ai-office-backend .
docker run -d --name aio --env-file .env -p 8001:8001 ai-office-backend
# Smoke check:
curl -fsS https://api.your-domain.com/api/health/deployment
# Expected: {"ready": true, "db_ok": true, "tenant_isolation_ok": true, "missing_required": []}
```

### Required env variable names (values live only in your dashboard)
```
MONGO_URL
DB_NAME
JWT_SECRET
CORS_ORIGINS
FRONTEND_URL
WEBHOOK_CRON_SECRET
```
### Platform admin (optional, no defaults)
```
ADMIN_EMAIL      # no default; unset = no platform admin is seeded (warning logged), the server still boots
ADMIN_PASSWORD   # first seed only; 12+ chars with upper, lower, digit, symbol; changed at first login.
                 # Unset or weak = admin seeded with an unusable password -> use forgot-password.
```
`ADMIN_EMAIL` must be an address no existing user has: if it matches a non-admin account, no admin
is seeded and that account is NOT changed (warning logged); the other seeds still run.
### AI (one of)
```
EMERGENT_LLM_KEY   # if staying on Emergent
OPENAI_API_KEY     # if going fully off-platform
```

## Branch structure (your three-branch plan)

### Payments (platform account for subscriptions)
```
STRIPE_SECRET_KEY
STRIPE_PUBLISHABLE_KEY
STRIPE_WEBHOOK_SECRET
```
### Payments (Connect — tenant payments)
```
STRIPE_CONNECT_WEBHOOK_SECRET   # optional; falls back to STRIPE_WEBHOOK_SECRET
```
### Email (one of)
```
EMERGENT_EMAIL_KEY
RESEND_API_KEY
```
### Voice / SMS (optional)
```
TWILIO_ACCOUNT_SID
TWILIO_AUTH_TOKEN
TWILIO_PHONE_NUMBER
```

Full list and purpose: `backend/.env.example` (checked in, no values).

## Testing summary

| Report | Scope | Result |
|---|---|---|
| `test_reports/iteration_9.json` | Phase 11 (repeat/testimonials/Spanish) | 100% |
| `test_reports/iteration_10.json` | Cloudflare prep regression | 7/7 pass |
| `test_reports/iteration_11.json` | Round 2 call-to-payment | 31/31 pass, workspace isolation verified |

Local pytest sidecars: `backend/tests/test_iteration10_cloudflare_prep.py`, `backend/tests/test_call_to_payment.py`.

## Known / explicit blockers

- **Stripe Connect not enabled on the preview's test account.** Live onboarding + PaymentIntent create return actionable 503. Fix is in your Stripe dashboard, not in code.
- **Oracle Always Free ARM availability is unverified.** If capacity is refused in your region, use the Hetzner CX22 fallback (~$4.15/mo). Still inside the $15 cap.
- **MongoDB Atlas M0 has a hard 512 MB cap** with no reliably-cheap upgrade step; plan a self-hosted Mongo migration before you breach it.
- **R2 op caps can bite before storage.** Add CDN caching on any chatty read path.

## Hosting budget (unchanged, re-affirmed, no automatic upgrades anywhere)

See the detailed table further down this file. One-line summary:

**Target ≤ $15/mo for hosting + DB + storage.** Baseline is $0/mo on Cloudflare Pages + Oracle Always Free + Atlas M0 + R2 free tier. Fallback hosting if Oracle is unavailable: Hetzner CX22 at ~$4.15/mo. Variable AI/voice/email/payment fees are tracked separately and are not part of the $15 cap. No paid resources are provisioned by this codebase.

---



| Branch | Scope | Status |
|---|---|---|
| `main` | Protected baseline | **Untouched by me.** |
| `empire-market-expansion` | Prior work before Cloudflare prep | **Preserved.** This branch is NOT the base of the new branches below — treat it as a predecessor. |
| `cloudflare-ready` | Round 1 — portable backend, Pages scaffolding, health probe, Docker, LLM shim | Code complete, tested in-preview, not yet live-deployed. |
| `call-to-payment` (NEW) | Round 2 — quotes/jobs/invoices/payments collections, Stripe Connect Standard (direct charges), idempotent webhooks, overdue cron, portal | In progress — this current commit. |
| `spanish-voice-finish` | Round 3 — Deep translate remaining dashboards, live voice homepage demo | Not started. |

### Dependency order & how to combine
```
main
  └── empire-market-expansion               (preserved historical work)
          └── cloudflare-ready              (Round 1)
                  └── call-to-payment       (Round 2) ← this commit
                          └── spanish-voice-finish  (Round 3)
```

**Each branch builds on the one above it.** To ship them independently:
- `cloudflare-ready` can merge to `main` on its own; it is additive and backward-compatible.
- `call-to-payment` **depends on** `cloudflare-ready` for the `llm_portable` shim and `/api/health/deployment` endpoint. Merge order: `cloudflare-ready → main`, then `call-to-payment → main`.
- `spanish-voice-finish` depends on `call-to-payment` only for the testimonial/quote email strings it will localize; otherwise independent.

### Combine via rebase (recommended)
```bash
git checkout call-to-payment
git rebase cloudflare-ready
# resolve any trivial conflicts (likely only in HANDOFF.md and server.py router list)
# then open the PR against main (or against cloudflare-ready if you want nested PRs).
```

### Combine via merge (if you prefer linear history + merge commits)
```bash
git checkout main
git merge --no-ff cloudflare-ready
git merge --no-ff call-to-payment
git merge --no-ff spanish-voice-finish
```

I cannot `git` from this environment. Create and switch branches via the Emergent **"Save to GitHub"** button; it accepts a branch name per push.

## What actually changed in this branch

| File | Why |
|---|---|
| `backend/llm_portable.py` | New. Drop-in replacement for `emergentintegrations.llm.chat.LlmChat` / `UserMessage`. Prefers Emergent when `EMERGENT_LLM_KEY` is set; falls back to native OpenAI (`OPENAI_API_KEY`); else deterministic fallbacks already in every callsite kick in. |
| `backend/ai_receptionist.py`, `ai_insights.py`, `routers/industry_intel.py`, `routers/sales_expert.py`, `routers/testimonials.py`, `routers/post_job.py`, `routers/advisor.py` | Imports swapped from `emergentintegrations.llm.chat` → `llm_portable`. **Zero behavior change on Emergent host; works off-Emergent with `OPENAI_API_KEY`.** |
| `backend/routers/health_deploy.py` | New. `GET /api/health/deployment` — proves DB write/read, tenant isolation, env coverage, LLM backend, counts. Returns `ready: true/false`. |
| `backend/server.py` | CORS now reads `CORS_ORIGINS` env; still permissive if unset. Health-deploy router registered. |
| `backend/Dockerfile`, `.dockerignore`, `.env.example` | New. Portable image for Oracle Cloud / Fly / Railway / Render / any Docker host. `emergentintegrations` install guarded (gracefully skipped if the index is unreachable). |
| `docker-compose.yml` | New. Backend + optional local MongoDB; comment block explains swapping to Atlas. |
| `frontend/public/_redirects`, `_headers` | New. SPA fallback + security headers for Cloudflare Pages. |
| `wrangler.toml` | New. Pages project metadata only (no Workers runtime). |

Nothing user-facing in the UI changed. All existing features keep working.

## What was tested on the current preview host

```bash
curl $PREVIEW/api/health/deployment
# → ready: True, db_ok: True, tenant_isolation_ok: True, llm_backend: emergent

# End-to-end AI call through the portable shim:
POST /api/public/demo/start   → 200, session_id
POST /api/public/demo/turn    → 200, "I can help with that. What's your name…"

# Shim fallback simulation (unset EMERGENT_LLM_KEY in a Python repl):
→ llm_portable.LlmChat resolves to _NativeOpenAIChat, awaiting OPENAI_API_KEY.
```

Login + workspace isolation + Mongo r/w + AI chat **all pass** on the current infra. The code paths exercised in production are the same ones a Cloudflare / Oracle deploy will run — only the hosting layer changes.

## What is NOT yet verified

| Item | Why |
|---|---|
| Live Cloudflare Pages deploy | I can't `wrangler deploy` from this environment. Follow commands below. |
| Live backend on Oracle/Fly | Same — needs you to run `docker build && docker run` or `fly deploy`. |
| Google OAuth off-Emergent | Still hits `demobackend.emergentagent.com`. If `GOOGLE_CLIENT_ID`/`SECRET` are set, add a new `/api/auth/google/callback` route in a follow-up — not blocking since email/password works everywhere. |
| MongoDB migration | Atlas Free (M0) accepts the current `mongodb+srv://` URL as-is; **no data migration needed** if you point `MONGO_URL` there now. For a cutover: `mongodump` from current → `mongorestore` to Atlas. |

## Deployment commands

### Frontend → Cloudflare Pages
```bash
# One-time:
# 1. In Cloudflare dashboard: Pages → Create → Connect to GitHub → momoempire/Momoempire, branch cloudflare-ready
# 2. Build settings:
#      Framework preset:    Create React App
#      Build command:       cd frontend && yarn install --frozen-lockfile && yarn build
#      Build output dir:    frontend/build
#      Root dir:            /
# 3. Environment variables (Production):
#      REACT_APP_BACKEND_URL = https://api.your-domain.com
# 4. Save & deploy. Cloudflare auto-rebuilds on every push to the branch.
```

### Backend → Docker (Oracle Cloud / Fly / Railway / Render)
```bash
# Build locally and ship to Oracle Cloud ARM Ampere free tier (recommended):
cd backend
cp .env.example .env   # fill in secrets
docker build -t ai-office-backend .
docker run -d --name aio --env-file .env -p 8001:8001 ai-office-backend

# OR with compose (brings up Mongo too if you don't use Atlas):
cd /app
docker compose up -d

# Oracle Cloud: push image to OCI Container Registry, then run on an Ampere VM.
# Fly:    `fly launch --image ai-office-backend` with secrets set via `fly secrets set`.
# Render: use the Dockerfile Blueprint; set env vars in the Render dashboard.
```

### Prove it's live
```bash
curl https://api.your-domain.com/api/health/deployment | jq
# Expect: ready: true, db_ok: true, tenant_isolation_ok: true, missing_required: []
```

## Required env variables (names only — see `backend/.env.example` for the full list)

**Required for boot:** `MONGO_URL`, `DB_NAME`, `JWT_SECRET`, `CORS_ORIGINS`, `FRONTEND_URL`.
**Platform admin (optional, no defaults):** `ADMIN_EMAIL` (unset = no admin seeded; must not match an
existing user) and `ADMIN_PASSWORD` (first seed only, strong; unset = unusable password, use forgot-password).

**AI (one of):** `EMERGENT_LLM_KEY` _or_ `OPENAI_API_KEY`.
**Email (one of):** `EMERGENT_EMAIL_KEY` _or_ `RESEND_API_KEY`.
**Payments:** `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_WEBHOOK_SECRET`.
**Voice/SMS (optional):** `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER`.
**Google OAuth (optional):** `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`.
**Cron:** `WEBHOOK_CRON_SECRET`.

## Rollback

Everything is additive except the LLM import swap in 8 files, which is reversible with a one-line sed. Rollback steps:

```bash
# Revert the LLM imports:
sed -i 's|from llm_portable import LlmChat, UserMessage|from emergentintegrations.llm.chat import LlmChat, UserMessage|' \
    backend/ai_receptionist.py backend/ai_insights.py \
    backend/routers/industry_intel.py backend/routers/sales_expert.py \
    backend/routers/testimonials.py backend/routers/post_job.py \
    backend/routers/advisor.py
# Then drop the new files:
rm backend/llm_portable.py backend/routers/health_deploy.py \
   backend/Dockerfile backend/.dockerignore backend/.env.example \
   docker-compose.yml frontend/public/_redirects frontend/public/_headers wrangler.toml
# And restore the CORS block to permissive-only.
```

The MongoDB cluster is untouched by this round — no schema changes, no migrations, no data moves.

## Monthly cost — hosting + DB + storage ONLY (≤ $15/mo target, no trial credits counted, no automatic paid upgrades)

**All items below are permanent free-forever tiers, NOT trial credits.** Figures remain **conditional** until (a) Oracle Always Free capacity is confirmed in your region, (b) a real deployment runs for ≥ 48 h, and (c) one full backup+restore cycle has been validated.

| Line | Service | Permanent allowance | $/mo (conditional) | Overage risk |
|---|---|---|---|---|
| Frontend | **Cloudflare Pages — Free** | 500 builds/mo, unlimited bandwidth, 100 custom domains, 1 concurrent build, 20k files/project | **$0** | Build cap resets monthly; no bandwidth overage |
| Backend | **Oracle Cloud — Always Free (ARM Ampere A1)** | **2 OCPU + 12 GB RAM** across up to 4 micro-instances combined, 200 GB block storage, 10 TB egress/mo — [docs](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm) | **$0 if provisionable in your region** | Oracle may reclaim idle Ampere capacity; region availability varies and is often waitlisted. Not a trial, but **not guaranteed**. |
| Database | **MongoDB Atlas M0 — Free Forever** | 512 MB storage, shared vCPU/RAM, 100 max connections, ~100 ops/sec sustained | **$0** | Hard 512 MB cap. **Cluster goes read-only** when exceeded. There is **no cheap one-step upgrade available in every region**; next publicly-listed paid tier starts at M10 ≈ $57/mo (explicitly off-budget). **Monitor and shed data before cap.** |
| File storage | **Cloudflare R2 — Free class** | 10 GB storage, 1 M Class A ops/mo (writes/lists), 10 M Class B ops/mo (reads), **zero egress fees** | **$0** | Past 10 GB: $0.015/GB·mo. Past caps: $4.50 / 1 M Class A ops, $0.36 / 1 M Class B ops. A chatty gallery or thumbnail job can burn ops faster than storage. |
| **Hosting total (if Oracle Always Free works)** | — | — | **$0** | — |
| **Hosting total (if Oracle unavailable)** | — | — | **See fallbacks below** | — |

### Fallback if Oracle Always Free isn't available (region waitlist, policy, capacity)

| Replacement | $/mo | Note |
|---|---|---|
| **Hetzner CX22** (2 vCPU, 4 GB RAM, 40 GB SSD, EU/US) | **€3.79 ≈ $4.15** | Honest pay-as-you-go, no trial, no sleep. Still inside $15 cap. |
| Fly.io `shared-cpu-1x @ 256 MB`, 1 machine, auto-stop | **~$2** at 50% uptime | Hobby plan, pay-as-you-go. Sleeps when idle, cold-start ~1s. |
| Railway Hobby | **$5** incl. $5 credit, then usage | Hard to predict; **not recommended** for a strict cap. |
| Render Starter | **$7** | Reliable but close to the ceiling → fallback only. |

### Items I explicitly ruled out
- **Atlas M2 / M5 as a cheap next step** → not reliably available at the previously-quoted $9. Removed from recommendation. If the M0 fills up, migrate to self-hosted MongoDB on the backend VM (free, requires ops effort) or export to Postgres.
- **Render Starter at $7 as baseline** → fallback-only.
- **Vercel/Netlify Pro, Fly Launch plan, Atlas M10** → off-budget.
- **Any 30-day free trial credit** (GCP, AWS, Azure, DO $200) → NOT counted.

### Variable fees — SEPARATE from hosting, usage-driven (not part of the $15 cap)

| Line | Collector | Floor | Note |
|---|---|---|---|
| LLM (OpenAI gpt-4o-mini) | OpenAI | **$0** (pay-per-token) | $0.15/1 M input, $0.60/1 M output. 100 demo chats ≈ ~$0.10. |
| LLM (if staying on Emergent) | Emergent Universal Key | $0 base, pre-paid balance | Same models, Emergent-brokered pricing. |
| Voice & SMS | Twilio | $1/mo per phone number + per-use | US local voice $0.0085/min in, SMS $0.0083 each. |
| Email | Resend | $0 for 3 k/mo; $20/mo for 50 k | Or free via the Emergent-managed Resend already provisioned. |
| Payment processing | Stripe | 2.9 % + $0.30 per card charge | **Direct charges via Connect Standard** — tenants pay this on their revenue, not you. |

### Known blockers / caveats (not resolved)

- **Oracle Ampere availability is unverified.** If signup refuses or the region is capacity-constrained, hosting cost shifts to the fallback row (≈ $4–7/mo). I have **not** provisioned anything.
- **Atlas M0 is a hard ceiling, not a soft one.** No cheap step up — plan a self-hosted migration path before you breach 512 MB.
- **R2 free-tier op caps can bite before storage.** 10 M Class B reads/mo = ~3 reads/sec sustained; add CDN caching for public images.
- **Twilio toll-fraud risk.** Enable Twilio "Voice Geographic Permissions" to lock dial-out to your countries.
- **Platform subscription vs. tenant-payment separation.** Platform Stripe account is **you**; tenant payments route to each tenant's own Standard-connected account with `stripe-account` header → no funds commingling.

**No paid resources will be provisioned by this codebase.** All upgrade paths are manual decisions in the respective dashboards.

## Not in this round (deferred by your explicit priority)

1. **Call-to-payment vertical flow** (Stripe Connect Standard, direct charges). Will land in the same `cloudflare-ready` branch in Round 2.
2. **Spanish finish + voice demo**. Round 3.

## Round 2 — Call-to-payment vertical (branch: `call-to-payment`, this commit)

**New endpoints under `/api/c2p/*` (owner-auth) and `/api/public/c2p/*` (customer-facing):**

| Route | Purpose |
|---|---|
| `POST /c2p/connect/onboard` | Create or reuse Stripe Standard connected account, return hosted onboarding link |
| `GET  /c2p/connect/status` | Live Stripe `charges_enabled` / `payouts_enabled` + requirements_due |
| `POST /c2p/connect/disconnect` | Clear connected account from tenant |
| `POST /c2p/quotes/draft` | Draft a quote. Line prices are pulled from the tenant's own `services` catalog — the AI/caller **cannot invent prices**. Owner may add `extra_lines` by hand. De-dupes customer by email/phone. |
| `GET  /c2p/quotes` | List quotes (owner), optional `?status=` filter |
| `POST /c2p/quotes/{id}/decision` | **Owner approval gate.** `approve_and_send` sets status=`sent` and emails the customer a public link; `reject` sets status=`declined`. |
| `GET  /public/c2p/quotes/{token}` | Customer-facing quote (no auth); hides owner notes |
| `POST /public/c2p/quotes/{token}/accept` | Customer accepts → creates `appointment` (job) + `invoice`; **idempotent** (double-accept returns the same invoice with `{idempotent:true}`); 400 if not approved |
| `POST /c2p/jobs/complete` | Mark appointment completed; flips invoice state for full-balance billing |
| `GET  /c2p/invoices` | Owner invoice list with `amount_due_cents` computed |
| `GET  /public/c2p/invoices/{token}` | Customer-facing invoice (no auth) |
| `POST /public/c2p/invoices/{token}/pay` | Create Stripe PaymentIntent on the tenant's CONNECTED account (**direct charge** via `stripe_account=`), supports partial payments; 503 if tenant hasn't onboarded |
| `PUT  /c2p/invoices/{id}/reminders` | Pause/resume reminders, cap `max_reminders` |
| `POST /cron/overdue-reminders` | Daily cron (14:15 UTC). Scans unpaid invoices past `due_at`; emails reminder; respects `reminders_paused_at` and `max_reminders`; stops automatically on payment |
| `GET  /c2p/needs-attention` | Dashboard aggregator: `{pending_quotes, awaiting_customer_quotes, overdue_invoices, unpaid_invoices, failed_payments, unassigned_leads}` |

**Stripe Connect webhook** at `/api/stripe/connect-webhook` — separate signing secret `STRIPE_CONNECT_WEBHOOK_SECRET` (falls back to `STRIPE_WEBHOOK_SECRET`). Handles:
- `payment_intent.succeeded` → increments `amount_paid_cents`, flips to `paid` when full, sets `paid_at` + `reminders_paused_at` (stops reminders)
- `payment_intent.payment_failed` → records failure on `invoice_payments`
- `charge.refunded` → decrements paid, drops back to `sent` if balance exists, clears `reminders_paused_at`
- `charge.dispute.created` → stamps `dispute_id`, `disputed_at`
- `account.updated` → updates `tenant.stripe_connect_status`
- **Duplicate-safety**: each `event.id` is upserted into `webhook_events` with a unique `_id` index. A dup returns `{ok:true, duplicate:true}` without re-running state.

**Platform subscriptions vs. customer payments — kept fully separate:**
- Platform subscription webhooks → `/api/stripe/webhook` (unchanged, uses `STRIPE_WEBHOOK_SECRET`, writes to `payment_transactions`).
- Customer→tenant payments → `/api/stripe/connect-webhook` (new, direct charges on connected accounts, writes to `invoices` + `invoice_payments`). No commingling.

### What's tested (iteration_11.json)
**31/31 pytest cases, 100% pass.** Coverage includes grounded quote drafting, customer dedupe, owner approval gate (incl. 400 on re-decide), notes hidden from customer, acceptance idempotency (double-accept returns same invoice), 400 on non-approved acceptance, job completion, PaymentIntent 503 without Connect, Connect-onboarding 503 with actionable message, webhook signature enforcement, webhook idempotency (duplicate event.id), full-payment transition, failed-payment attempt recording with correct tenant_id, refund rollback + reminder resume, dispute stamping, overdue cron under auth + status transition, reminder pause/resume, needs-attention aggregator, and strict **workspace isolation** between two tenants (quotes/invoices/needs-attention all scoped; cross-tenant actions return 404).

### What's NOT verified in this round (explicit blockers)
| Item | Why unverified | Reproduce / fix |
|---|---|---|
| Live `connect/onboard` returning a Stripe hosted link | The preview's Stripe test account has **not enabled Connect**. The API returns a clean 503 with instructions. | Enable Connect at https://dashboard.stripe.com/connect in your Stripe test account, then retry. |
| Live `PaymentIntent.create` on a real connected account | Depends on an onboarded tenant. | Complete the hosted onboarding and re-run `POST /public/c2p/invoices/{token}/pay`. |
| Real-world Stripe webhook delivery | Covered by signed-synthetic-event tests, not by a live Stripe push. | Point Stripe CLI (`stripe listen --forward-to .../api/stripe/connect-webhook`) during QA. |

### Exact remaining steps for Round 3 (Spanish-voice-finish)
1. Finish translating the remaining dashboard pages (`Billing`, `Analytics`, `Automations`) with the i18n keys already in `src/i18n/locales/{en,es}.json` from Round 1.
2. Add Spanish strings for the new c2p flow (quote email, customer portal, overdue reminder — the overdue cron already branches on `tenant.lang`).
3. Replace the text-only homepage demo with a WebRTC voice demo (needs `OPENAI_API_KEY` or an ElevenLabs real-time key — user to provide).

### Branch combination (unchanged from the top of this doc)
```
cloudflare-ready → call-to-payment → spanish-voice-finish
```
Round 2 is additive and does not touch anything from Round 1. Rebase order recommended.
