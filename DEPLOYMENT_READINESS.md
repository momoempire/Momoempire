# Deployment Readiness Audit — Momo Empire (Audit Track F, Directive §7)

- **Repo / base:** `momoempire/Momoempire`, main @ `bee3e86e`
- **Branch:** `audit/deployment-readiness` — read-only audit; this document is the only change. No deploys, no env changes, no production data touched. Secret **names only** throughout.
- **Method:** every claim below was verified in the code at the base commit (file/line references given), cross-checked against the owner's launch checklist (`~/workspace/launch-week-owner-actions-2026-10-20.md`) and the repo's own deploy docs (`docs/deploy/`, `README.md`, `backend/.env.example`, `frontend/.env.example`).
- **Classifications:** Confirmed OK · Confirmed Broken · Needs Testing · Requires Owner Decision.

## Verdict

The codebase is unusually well prepared for a first deploy: a production-grade Dockerfile, a coherent docker-compose, strict fail-closed CORS, body-aware health checks, an admin-only deployment probe, optional services that fail closed when unconfigured, and runbooks that match the code. **No code-level hard blocker to deploying was found.** The launch gate is operator-side, not code-side: no host is provisioned yet, several required env vars are missing from the owner's checklist (Stripe, Twilio, cron), and there is no CI — every deploy and rollback is a manual operator action.

**Finding counts:** Confirmed OK 16 · Confirmed Broken 2 (both minor doc/config hazards) · Needs Testing 4 · Requires Owner Decision 6.

---

## 1. Environment configuration

### 1.1 Backend variables the code actually reads (grep-verified)

**Required — the app cannot serve its purpose without these:**

| Variable | Read at | Consequence if unset |
|---|---|---|
| `MONGO_URL` | `backend/db.py` (`os.environ[...]`) | First DB touch raises `KeyError`; app boots (seeds fail-soft) but every DB-backed route fails; `/api/health` reports `degraded` |
| `DB_NAME` | `backend/db.py` | Same as above |
| `JWT_SECRET` | `backend/security.py` (`os.environ[...]`, no default) | Auth token signing fails hard — no insecure fallback in the main auth path |
| `APP_ENV` | `backend/deploy_security.py`, `backend/sentry_init.py` | Defaults to `development` → CORS permissive mode. **Must be `production` at deploy or the strict CORS posture silently does not apply** |
| `CORS_ORIGINS` | `backend/deploy_security.py` | In production/staging the server still starts but **fails closed**: no cross-origin access at all until a valid exact-origin list is set |
| `TWO_FACTOR_ENCRYPTION_KEY` (or fallback `BOOKKEEPING_LINK_ENCRYPTION_KEY`) | `backend/two_factor.py`, `backend/services/token_vault.py` | 2FA setup fails closed with 503; rest of app runs. Checklist correctly lists it as required |
| `FRONTEND_URL` | multiple routers | Email/redirect links point wrong if unset |

**Required for the features they power (launch-relevant):**

| Variable(s) | Powers |
|---|---|
| `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_CONNECT_WEBHOOK_SECRET`, `STRIPE_PUBLISHABLE_KEY` | Subscription billing, checkout, payment webhooks (`backend/routers/payments.py`, `call_to_payment.py`). Webhook signature verification reads the secrets per-request |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER` | Platform SMS/voice (`backend/services/twilio.py`, `services/outbound_voice.py`, `routers/phase9.py`). Per-tenant credentials live in tenant config and take precedence |
| `TWILIO_PARENT_SID`, `TWILIO_PARENT_TOKEN` | Subaccount auto-provisioning for new shops (AI onboarding) |
| `PUBLIC_BACKEND_URL` | Twilio webhook signature validation + absolute unsubscribe links; effectively required once Twilio webhooks are live (refused outside development when no token is configured) |
| `RESEND_API_KEY`, `RESEND_FROM_EMAIL` | Transactional + marketing email |
| `AI_API_KEY` **or** `OPENAI_API_KEY` **or** `GEMINI_API_KEY` (one is enough) | All AI features via `backend/llm_portable.py` (native OpenAI/Gemini only; `EMERGENT_LLM_KEY` survives only as a legacy alias for `AI_API_KEY` — no Emergent SDK dependency) |
| `WEBHOOK_CRON_SECRET` | Bearer secret for all `/api/cron/*` endpoints (`backend/routers/cron.py`) — without it (and a scheduler, §8) reminders, follow-ups, digests and nightly overage billing **never fire** |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD` | First-seed platform admin; if empty, no platform admin is seeded (password, when used, must be 12+ chars with upper/lower/digit/symbol) |

**Recommended / optional (all default to a safe off or fallback):**

`REDIS_URL` (distributed rate limiting; in-memory fallback otherwise, `backend/rate_limit.py`) · `SENTRY_DSN`, `SENTRY_TRACES_SAMPLE_RATE`, `SENTRY_PROFILES_SAMPLE_RATE` (no-op when unset, §6) · `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI` (Google sign-in) · `CNAME_TARGET_HOST` (custom domains; endpoints return 503 when unset) · `TURNSTILE_ENABLED` + `TURNSTILE_SECRET_KEY` (bot check; enabling without the secret stops the backend at startup — intentional) · `SERVICE_HUB_ENABLED`, `SERVICE_HUB_URL`, `SERVICE_HUB_API_KEY`, `SERVICE_HUB_TIMEOUT_SECONDS` (own support-hub adapter, **off by default** — checklist correctly says leave unset) · `EMBEDDINGS_PROVIDER`, `EMBEDDINGS_MODEL` (off by default) · `PUBLIC_VOICE_DEMO_*` (off by default) · `WAITLIST_ONLY`, `WAITLIST_DB_TIMEOUT_MS`, `WAITLIST_CONFIRMATION_EMAIL` (waitlist-mode deploy variant) · `FORWARDED_ALLOW_IPS` (set by docker-compose from the network gateway; only hand-set for non-compose deploys) · `WEB_CONCURRENCY` (must stay `1`, §10) · `BRAND_NAME`, `BRAND_SLOGAN`, `CONTACT_EMAIL`, `SITE_URL`, `EMAIL_FROM_NAME`, `EMAIL_REPLY_TO` (branding strings; placeholder defaults) · `MARKETING_EMAIL_PROVIDER`, `MARKETING_SMS_PROVIDER` (default `log` = zero network) · `VIDEO_ESTIMATES_STORAGE_DIR`, `VIDEO_ESTIMATES_TOKEN_TTL_DAYS` · `NICHE`, `TRADE_PACKS_DIR`, `MARKET_PACKS_DIR` (pack resolution overrides) · `VOICE_PROVIDER`, `OPENAI_REALTIME_MODEL`, `OPENAI_REALTIME_VOICE`.

**Test-only (never set in production):** `TEST_ADMIN_EMAIL/PASSWORD`, `TEST_OWNER_EMAIL/PASSWORD`, `*_TEST_MONGO_URL` (×6), `TEST_CORS_ORIGIN`, `TEST_EXPECTED_LLM_BACKEND`, `TWILIO_TEST_AUTH_TOKEN`.

### 1.2 Frontend build-time variables (Cloudflare Pages build env)

`REACT_APP_BACKEND_URL` (**required — see finding F-17**), `REACT_APP_TURNSTILE_ENABLED` + `REACT_APP_TURNSTILE_SITE_KEY` (pair with backend Turnstile), `REACT_APP_SITE_URL`, `REACT_APP_SITE_NAME`, `REACT_APP_SITE_DESCRIPTION`, `REACT_APP_OG_IMAGE_URL` (site-meta; without `REACT_APP_SITE_URL` no sitemap is built), `REACT_APP_BRAND_NAME`, `REACT_APP_BRAND_SLOGAN`, `REACT_APP_CONTACT_EMAIL`, `REACT_APP_LEGAL_LAST_UPDATED`, `REACT_APP_WAITLIST_ONLY`, `REACT_APP_SENTRY_DSN`, `REACT_APP_NICHE`, and `GENERATE_SOURCEMAP` (docs require `false` in production so source maps don't ship).

### 1.3 Cross-check against the owner's launch checklist

- **Checklist → code: no phantom entries.** Every variable the checklist names (`TWO_FACTOR_ENCRYPTION_KEY`, `MONGO_URL`, `DB_NAME`, `JWT_SECRET`, `APP_ENV`, `CORS_ORIGINS`, `GOOGLE_CLIENT_ID/SECRET`, `RESEND_API_KEY`, `RESEND_FROM_EMAIL`, `AI_API_KEY`, `CNAME_TARGET_HOST`, `REDIS_URL`, `SENTRY_DSN`, `SERVICE_HUB_*`) is genuinely read by the code. ✅
- **Code → checklist gaps (checklist should be amended before deploy day):**
  1. **Stripe**: `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_CONNECT_WEBHOOK_SECRET`, `STRIPE_PUBLISHABLE_KEY` — absent from the checklist entirely; paid checkout/webhooks cannot work without them.
  2. **Twilio**: the checklist covers buying the number + A2P filing but never names `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER` (or `TWILIO_PARENT_SID/TOKEN` for subaccount provisioning).
  3. **Cron**: `WEBHOOK_CRON_SECRET` **and an external scheduler** (see §8) — absent; without them automations silently never run.
  4. **Admin seed**: `ADMIN_EMAIL`, `ADMIN_PASSWORD` — absent; a fresh DB otherwise has no platform admin.
  5. **URL wiring**: `FRONTEND_URL`, `PUBLIC_BACKEND_URL` — absent.
  6. **Frontend build env**: the checklist never mentions the Pages build variables (`REACT_APP_BACKEND_URL`, `GENERATE_SOURCEMAP=false`, site-meta vars) at all.
  7. Minor: `GOOGLE_REDIRECT_URI`, and `FORWARDED_ALLOW_IPS` if the deploy doesn't use the shipped compose file.

---

## 2. Hosting path

- **Shape matches the target**: frontend → Cloudflare Pages (`wrangler.toml`: project `empire-ai-office`, output `frontend/build`, build `yarn install --frozen-lockfile && yarn build`; `frontend/public/_redirects` provides the SPA fallback; node `20.x` + yarn 1.22 pinned via `package.json`/preflight). Backend → Docker on any VM (`backend/Dockerfile`, `docker-compose.yml`). No Render/fly config exists for this repo — deploys are manual by design.
- **Dockerfile (Confirmed OK):** `python:3.11-slim`, pinned `requirements.txt`, non-root user (uid 10001), `PORT=8001`, body-aware `HEALTHCHECK`, `--workers 1` with `--proxy-headers` and an explicit `--forwarded-allow-ips` (never `*`).
- **Compose (Confirmed OK):** backend published on `127.0.0.1` only (reverse proxy / Cloudflare Tunnel in front), local `mongo:7` marked optional with Atlas recommended for prod, healthcheck requires `{"status":"ok"}` (a `degraded` body counts as unhealthy), fixed subnet so `FORWARDED_ALLOW_IPS` can't drift.
- **API routing assumption:** the SPA calls `REACT_APP_BACKEND_URL` + `/api` cross-origin with credentials (`frontend/src/lib/api.js`) — coherent with the strict CORS allow-list + `SameSite=None; Secure` cookies (§4).
- A manual build preflight exists (`frontend/cloudflare-build-preflight.sh`: frozen lockfile, `CI=true yarn build`, brand-leak and legacy-domain checks on the output).

## 3. Database

- `backend/db.py`: lazy Motor singleton; `MONGO_URL`/`DB_NAME` read via `os.environ[...]` (hard failure on first use if unset, but see boot behavior below). TLS is inherited from the connection string (Atlas `mongodb+srv://` implies TLS) — **the operator must use the SRV form; nothing in code forces TLS for a hand-written `mongodb://` string** (note for the deploy checklist).
- Main client uses **driver defaults**: no explicit pool sizing (Motor default max pool 100) and the default ~30 s server-selection timeout. A dedicated waitlist client (`get_waitlist_db()`) sets 5 s (configurable 500 ms–30 s) timeouts so the public signup fails fast with 503.
- **Boot with DB unreachable (Confirmed OK):** startup seeding runs inside try/except and only logs (`server.py` startup handler); the process still serves, and `/api/health` reports `degraded`. The flip side is finding F-20: non-waitlist requests during an outage can block up to the driver's 30 s selection timeout.

## 4. SSL/HTTPS, CORS, headers, cookies

- **CORS (Confirmed OK):** `backend/deploy_security.py` — exact-origin allow-list from `CORS_ORIGINS`, strict origin grammar (scheme://host[:port], no wildcards/userinfo/paths), HTTPS mandatory in staging/production, **fails closed** (empty allow-list + logged error) when missing/invalid in strict envs, no regex fallback. `APP_ENV` must be set to `production` for this posture to engage (§1.1).
- **Frontend headers (Confirmed OK):** `frontend/public/_headers` ships HSTS (`max-age=31536000; includeSubDomains; preload`), a full CSP (self + Stripe + Turnstile + Sentry CDN), `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`, `Permissions-Policy`, and immutable caching for `/static/*`.
- **Cookies (Confirmed OK):** session/access/refresh/portal cookies are `HttpOnly; Secure; SameSite=None` (`backend/security.py`, `routers/auth.py`, `routers/invitations.py`, `routers/portal.py`). `SameSite=None` is required by the cross-origin Pages↔API split and is compensated by the CSRF origin middleware (`backend/csrf_guard.py`, installed in `server.py`) plus the waitlist origin guard. Track D owns the deeper auth review; deployment-wise the configuration is coherent. HTTPS termination is the proxy's job (Nginx/Certbot or Cloudflare Tunnel per `docs/deploy/linode-runbook.md` + `nginx-api.example.conf`).

## 5. Health checks

- `GET /api/health` — **public, no auth**. Pings Mongo and checks the waitlist unique-email index; returns `{"status":"ok"}` or `{"status":"degraded"}`. Caveat = finding F-19: the HTTP status is **200 in both cases**; the Docker/compose healthchecks parse the body (correct), but a status-code-only external monitor would miss a DB outage.
- `GET /api/health/deployment` — **platform-admin only** (`backend/routers/health_deploy.py`). DB write/read round-trip, tenant-isolation smoke check, env-presence map (names only, never values), detected LLM backend, collection counts, and a computed `ready` boolean. This is the right post-deploy verification call and the runbooks already use it.
- `GET /api/` — trivial service/version liveness.

## 6. Error monitoring

- `backend/sentry_init.py` (Confirmed OK): **no-op unless `SENTRY_DSN` is set**; init failure logs a warning and the app continues (fail-open). `before_send` scrubs password/secret/token/cookie-shaped fields and drops events containing bearer tokens or Stripe-key patterns; `send_default_pii=False`. Frontend mirrors via `REACT_APP_SENTRY_DSN`. Without Sentry the fallback is stdout logging (`logging.basicConfig`, INFO) — acceptable at launch scale, but there is no log aggregation unless the host provides it.

## 7. Rollback procedure (concrete, for this stack)

There is **no automated deploy pipeline** for this repo (no CI, no autoDeploy config), so rollback is an operator procedure:

1. **Frontend (Cloudflare Pages):** Pages retains previous deployments — in the Pages dashboard, roll back to the prior production deployment (platform feature; **verify at first deploy**, no deployment exists yet to confirm against). Equivalent fallback: rebuild and redeploy the prior commit with the documented build command.
2. **Backend (Docker on the VM):** before replacing the running container, tag the current image (`docker tag ai-office-backend:latest ai-office-backend:prev`). Rollback = stop the new container, start `ai-office-backend:prev` with the **same `--env-file`**, then verify `GET /api/health` = `ok` and `GET /api/health/deployment` (admin) = `ready: true`.
3. **Source-level:** `git revert` the offending commit on `main` (do not rewrite history), rebuild from the reverted tree, redeploy as above.
4. **Database:** there is no migration framework — Mongo is schemaless and startup work is idempotent index creation + insert-only seeds, so a code rollback needs **no DB rollback**. Exception to remember: insert-only plan seeding means a rollback does not undo seeded plan rows (cross-reference Track D's pricing findings).
5. **Env:** `.env` exists only on the host; keep a dated copy in the host's secret store before any edit so env changes roll back with the image.
6. **Cautionary precedent:** on the sibling books project the GitHub→Render autoDeploy webhook proved unreliable and deploys became manual-with-verification. This stack is manual from the start — **treat "deploy succeeded" as unproven until the deployment probe says `ready`**.

## 8. Automated testing / CI

- **No CI exists** (Confirmed): no `.github/workflows/` and no other CI config in the repo. Nothing gates a deploy on tests; the only gate is the manual `frontend/cloudflare-build-preflight.sh` plus operator discipline.
- Test inventory: backend pytest suite (baseline on main ≈ **1,522 passed**), frontend Jest suites, root `tests/` package.
- **Spot verification run for this audit** (deploy-relevant files, `backend/tests/test_docker_healthcheck.py`, `test_fix_cors_health.py`, `test_waitlist_mode_index_health.py`): **65 passed, 0 failed**. Consistent with the stated baseline; no deploy-area regressions found.
- **Crons are external** (Confirmed OK design): `/api/cron/*` endpoints ack fast and background the work, authorized by `WEBHOOK_CRON_SECRET` bearer; the full schedule (follow-ups every 15 min, reminders hourly, overage nightly 02:30 UTC, digest Mondays, callbacks every 5 min, …) is defined in `docs/cron-schedule.yml` for any scheduler — VM crontab/systemd per the runbook. Nothing fires unless the operator wires a scheduler (finding F-24).

## 9. Mobile responsiveness (code-level spot check)

- `frontend/public/index.html` carries `<meta name="viewport" content="width=device-width, initial-scale=1" />`. ✅
- Tailwind responsive prefixes present on the key surfaces: `Landing.jsx` 33 responsive-prefixed classes, `Pricing.jsx` 9, dashboard pages use them throughout (e.g. `AICollections.jsx`). `IndustryLanding.jsx` is a thin wrapper that renders the same `Landing` template with per-industry config — so all 33 industry pages inherit the landing page's responsive behavior rather than duplicating it. **Confirmed OK** at code level; a real-device pass remains part of first-deploy QA (Needs Testing in the generic sense, not a defect).

## 10. Resource usage

- **Single worker by design (Confirmed OK, hard constraint):** in-process rate limiters (waitlist/demo) and the in-memory rate-limit fallback are per-process; Dockerfile and compose both pin `WEB_CONCURRENCY=1`, and the app logs an error at startup if worker count > 1 (`backend/routers/marketing.py`). **Do not scale by adding workers/replicas** — scale the box, or set `REDIS_URL` and revisit. This caps the launch shape at one modest VM, which suits the cost target.
- In-memory limiter is bounded (`MAX_KEYS = 50_000` with pruning, `backend/rate_limit.py`).
- Video-estimate bytes are stored on **local disk** via a storage stub (`backend/services/video_storage.py`; S3/R2 "later") — disk growth lives on the VM and is another reason the backend is single-host today.
- **Image size risk (Needs Testing, F-21):** the Dockerfile comment claims "~140 MB final image", but `requirements.txt` includes pandas, numpy, boto3, huggingface_hub, tokenizers, grpcio, three Google SDK stacks **and dev tools** (black, flake8, mypy, pytest, isort). The real image is very likely several times the claim. Build and measure before committing to a 1 GB VM; the repo runbook already warns 1 GB is tight and calls **2 GB the safer pilot floor**.

---

## 11. Cost audit (§7)

Every external service the codebase integrates, verified from code/config. Prices are stated **only** where the repo or owner docs source them; anything else is marked *verify at deploy* — nothing here is invented.

| Service | Role | Class | Cost basis |
|---|---|---|---|
| Cloudflare Pages | Frontend hosting | Required | Free plan is sufficient per repo docs (`docs/deploy/cloudflare-launch-test.md` cites free-plan limits) — **$0** |
| VM host (Linode 2 GB recommended; Oracle Cloud Free ARM named as alternative in `docker-compose.yml`) | Backend hosting | Required | **~$12/mo** for Linode 2 GB (in-repo estimate, sourced 2026-10-08, `docs/deploy/linode-runbook.md`); $0 on Oracle Free tier |
| MongoDB Atlas M0 | Database | Required | **$0** free tier (512 MB storage) per repo runbook; paid tier when outgrown — *verify at deploy* |
| Twilio | SMS + voice | Required for comms features | Usage-based + per-number monthly (~$1.15/number per owner's filing packet) + one-time A2P brand/campaign vetting per `~/workspace/twilio-a2p-filing-packet.md` |
| Stripe | Payments/subscriptions | Required for paid plans | Transaction-fee based; no platform monthly fee assumed — *verify at deploy* |
| OpenAI **or** Google Gemini | AI (chat, receptionist, vision) | Required for AI features (one provider suffices) | Usage-based; `.env.example` notes a no-card Google AI Studio key option |
| Resend | Email | Required for email flows | *Verify at deploy* (plan-dependent) |
| Redis | Distributed rate limiting | Optional (in-memory fallback built in) | $0 if self-hosted on the VM or skipped at launch scale |
| Sentry | Error monitoring | Optional (no-op when unset) | Free tier exists — *verify at deploy* |
| Cloudflare Turnstile | Bot protection | Optional, off by default | Free (Cloudflare) |
| Service Hub (own product) | Support sync adapter | Optional, **off by default** | No external fee |
| Wisetack | Customer financing | Optional, per-tenant | Shops connect their **own** merchant accounts; platform default is demo mode |
| OpenAI embeddings | Knowledge-base search | Optional, **off by default** (`.env.example` marks it approval-gated) | Usage-based if enabled |
| OpenAI Realtime voice demo | Public landing demo | Optional, **off by default** | Usage-based per minute if enabled |
| Google OAuth | Sign-in | Optional | No fee |
| S3/R2 object storage | Future video storage | Not active (local disk today; `boto3` is in requirements but unused by app code) | $0 now; future *verify at deploy* |

**Silent-charge hygiene (Confirmed OK):** nothing in the codebase auto-enables a paid service. Every paid integration is key-gated and defaults to off, empty, demo, or log-only (`MARKETING_*_PROVIDER=log` default; embeddings and voice demo explicitly commented as spend-approval-gated). The recurring-charge risks are all **usage-based and operator-visible**: AI minutes/tokens, Twilio messages/minutes/numbers, Stripe transaction fees.

**Cost verdict:** The owner's ≤ $15/month infrastructure target **holds for fixed infrastructure**: Cloudflare Pages ($0) + Atlas M0 ($0) + one 2 GB VM (~$12/mo per the repo's own sourced estimate — or $0 on Oracle Cloud Free) lands at **$0–12/month**, inside target with headroom. The target breaks only if "infrastructure" is stretched to include the usage-based layer — Twilio, AI provider usage, and Stripe fees — which are not fixed costs, scale with paying customers, and are properly cost-of-revenue rather than hosting. Two honest caveats: Atlas M0's 512 MB will eventually force a paid database tier as tenant data grows (price *verify at deploy*), and a 1 GB VM is below the comfortable floor for this image — the 2 GB tier in the runbook is the realistic minimum, so budget the $12, not $5.

---

## 12. Findings register

**Confirmed OK**

- **F-01** Dockerfile is production-grade: non-root, pinned deps, body-aware healthcheck, single-worker + proxy-header discipline.
- **F-02** docker-compose is coherent and safe by default: loopback-only backend, Atlas recommended over local Mongo, healthcheck treats `degraded` as unhealthy.
- **F-03** Cloudflare Pages path is complete: `wrangler.toml`, SPA `_redirects`, reproducible frozen-lockfile build, manual preflight with brand/domain guards.
- **F-04** CORS is strict and fails closed in production/staging (`deploy_security.py`); no wildcard/regex fallback with credentials.
- **F-05** Frontend ships HSTS + CSP + anti-framing/nosniff headers via `_headers`.
- **F-06** Auth cookies are `HttpOnly; Secure; SameSite=None` with a CSRF origin middleware compensating for the cross-origin split.
- **F-07** `JWT_SECRET` is hard-required (`os.environ[...]`, no default) in the main auth path.
- **F-08** Sentry integration is a true no-op without a DSN, scrubs PII/secrets, and fails open.
- **F-09** Health surface is fit for purpose: public `/api/health` (DB ping + index check) and an admin-only `/api/health/deployment` probe (DB round-trip, isolation smoke, env presence by name, `ready` verdict).
- **F-10** Boot resilience: the app starts and serves with the DB unreachable; health reports `degraded` instead of the process dying.
- **F-11** Rate limiting has a Redis path and a bounded in-memory fallback; the single-worker constraint is documented in Dockerfile, compose, and code, with a startup warning.
- **F-12** Cron design is scheduler-agnostic with shared-secret endpoints and a fully documented schedule (`docs/cron-schedule.yml`).
- **F-13** AI providers are native-only (OpenAI/Gemini); the Emergent key name is a legacy alias, not a dependency.
- **F-14** Optional/paid features fail closed when unconfigured (custom domains 503, Turnstile startup stop, embeddings/voice-demo/Wisetack-demo/Service-Hub all default off).
- **F-15** Mobile posture: viewport meta present; landing, pricing, dashboard, and all 33 industry pages (via the shared `Landing` template) use Tailwind responsive layouts.
- **F-16** Checklist accuracy (forward direction): every env var on the owner's launch checklist is genuinely read by the code — no phantom entries.

**Confirmed Broken (minor; documentation/config hazards — no code changes made, this track is read-only)**

- **F-17** Frontend API base has **no fallback and no build-time guard**: `frontend/src/lib/api.js` builds `API_BASE` directly from `REACT_APP_BACKEND_URL`; if the Pages build env omits it, the build still "succeeds" and ships a bundle whose API base is literally `undefined/api` (verified by inspection of `api.js`: the value is interpolated with no default and no guard). The preflight script doesn't check for it. *Fix when authorized: fail the build/preflight when the var is unset.*
- **F-18** **Twilio env-name mismatch in operator docs:** `README.md` and `backend/.env.example` tell the operator to set `TWILIO_PHONE_NUMBER`, but the code reads **only** `TWILIO_FROM_NUMBER`. An operator following the docs would set a variable nothing reads and platform SMS/voice would be silently unconfigured. *Fix when authorized: align README + `.env.example` to `TWILIO_FROM_NUMBER`.*

**Needs Testing**

- **F-19** `/api/health` returns HTTP 200 with a `degraded` body when the DB is down. Docker/compose handle this correctly (body-aware); any external uptime monitor must be configured to check the body, not the status code. Verify monitor config at deploy.
- **F-20** Main Mongo client uses driver-default timeouts (~30 s server selection); only the waitlist path has tight deadlines. Behavior of ordinary requests during a DB outage (long hangs vs. fast errors) is unverified — test by deploying with an unreachable `MONGO_URL`.
- **F-21** Real Docker image size vs. the "~140 MB" comment in `backend/Dockerfile`, given the heavy `requirements.txt` (pandas/numpy/boto3/huggingface_hub/tokenizers + dev tools). Build, measure, and slim (or split dev requirements) before finalizing the VM tier.
- **F-22** Production Pages build from this exact commit with the full production env set (Turnstile pair, `GENERATE_SOURCEMAP=false`, site-meta vars) has not been run as a set; baseline frontend build is green with the standard set.

**Requires Owner Decision**

- **F-23** **Host selection & provisioning:** nothing is deployed. Repo runbooks offer Linode 2 GB (~$12/mo) vs. Oracle Cloud Free ARM ($0) vs. others. Owner picks; this is the launch gate.
- **F-24** **Amend the launch checklist** with the §1.3 gaps (Stripe ×4, Twilio env names ×5, `WEBHOOK_CRON_SECRET` + scheduler, `ADMIN_EMAIL/PASSWORD`, `FRONTEND_URL`, `PUBLIC_BACKEND_URL`, Pages build env) before deploy day.
- **F-25** **Cron scheduler ownership:** decide what fires `/api/cron/*` (VM crontab/systemd per runbook, or a hosted scheduler) — until something does, reminders, follow-ups, digests, and nightly overage billing silently never run.
- **F-26** **Atlas growth path:** M0 free (512 MB) is the documented prod start; approve the paid-tier trigger point (storage/tenant threshold) in advance — price *verify at deploy*.
- **F-27** **No CI:** decide whether to add a minimal GitHub Actions gate (backend pytest + frontend build) before launch or accept manual preflight discipline for the launch window.
- **F-28** **Minor, referred to Track D's scope:** `backend/services/marketing_automation.py` signs unsubscribe tokens with a hard-coded dev fallback secret if `JWT_SECRET` is unset. Moot in any correct production config (`JWT_SECRET` is required), but removing the fallback is an owner/security call; no change made here.

---

*Prepared by Audit Track F (deployment readiness). Sibling tracks: A — books integration credentials + idempotency; B — Momo integration client + seat caps; C — niche packs + landing; D — security + pricing. This PR is for owner review; do not merge without owner approval.*
