# Waitlist-only deploy (EMP-WL-002, route A)

**Status:** steps only. Nothing here has been deployed, and no accounts were created. Whether to
launch with this route or the full stack (EMP-WL-003) is **Brann's decision**.

What you get:
- **Frontend:** a static build on Cloudflare Pages with only `/`, `/privacy` and `/terms`. Every
  other path redirects to `/`. Dashboard, admin and auth code are not in the bundle. The landing
  page shows no demo, trial, login or pricing buttons, and makes no `/api/plans` call.
- **Backend:** FastAPI serving only `GET`/`HEAD /api/health` and `POST /api/public/waitlist`.
  Everything else returns 404, including `/docs` and `/openapi.json`. There is no startup seeding.
  The waitlist POST must be `application/json` (415 otherwise). When `CORS_ORIGINS` is set, it must
  come from one of those origins (403 otherwise).

## Settings

| Where | Variable | Value | Why |
|---|---|---|---|
| Cloudflare Pages build env (Production) | `REACT_APP_WAITLIST_ONLY` | `true` | Builds the waitlist-only app. Read at **build** time, so changing it needs a rebuild. |
| Cloudflare Pages build env (Production) | `REACT_APP_BACKEND_URL` | the backend's HTTPS origin, e.g. `https://api.example.com` | Where the form posts. Build time. |
| Backend env (`backend/.env`) | `WAITLIST_ONLY` | `true` | Minimal API (health + waitlist only). |
| Backend env | `CORS_ORIGINS` | the exact frontend origins, comma-separated, e.g. `https://<project>.pages.dev,https://www.example.com` | **Required.** Without it, the form breaks: the browser can't call the API cross-origin once PR #10 is merged (startup fails in production), and the API is wide open before then. Every hostname needs its own entry (Pages URL, apex, www). No paths, no trailing slash, https only. |
| Backend env | `APP_ENV` | `production` | Strict CORS checks (https and explicit origins; enforced once PR #10 is merged). |
| Backend env | `MONGO_URL`, `DB_NAME` | as for any deploy | Where waitlist signups are stored. `JWT_SECRET` isn't used by the two served routes, but keep the usual value. |

Optional, only once PR #11 is merged: `FORWARDED_ALLOW_IPS` (see `client-ip-and-proxies.md`), plus
`TURNSTILE_ENABLED`, `TURNSTILE_SECRET_KEY` and the frontend's `REACT_APP_TURNSTILE_SITE_KEY`.

## Steps
1. **Backend host.** Start the backend with the backend variables above (Docker: `docker compose
   up -d backend` picks up `backend/.env` through `env_file`; compose needs no change). Check the
   startup log for `WAITLIST_ONLY is on: serving only /api/health and POST /api/public/waitlist`.
2. **Backend checks** (replace the host):
   - `curl -i https://API/api/health` gives 200 `{"status":"ok"}`. `curl -I` (HEAD) gives 200.
   - `curl -i https://API/docs` and `curl -i https://API/api/auth/me` give 404.
   - `curl -i -X POST https://API/api/public/waitlist -H 'Content-Type: text/plain' -d '{}'` gives 415.
   - `curl -i -X OPTIONS https://API/api/public/waitlist -H 'Origin: https://evil.example' -H 'Access-Control-Request-Method: POST'`
     should **not** return `access-control-allow-origin: https://evil.example`.
3. **Cloudflare Pages project** (existing build settings from `docs/cloudflare/frontend-build.md`:
   root `frontend`, command `yarn build` or `npx craco build`, output `build`). Add the two
   `REACT_APP_*` variables to the **Production** environment and build. The SPA fallback (all paths
   serve `index.html`) is Pages' default for a project with no `404.html`.
4. **Frontend checks:**
   - `/` shows the landing page and the waitlist form.
   - `/login`, `/admin` and `/pricing` land on `/`.
   - There are no Live demo, Try it free, Log in or Pricing buttons.
   - The browser devtools Network tab shows no `/api/plans` call.
   - Submit a test address and check it's stored once (`db.waitlist`).
5. **Rollback:** unset `REACT_APP_WAITLIST_ONLY` (rebuild) and `WAITLIST_ONLY` (restart) to get the
   full app back. Only do that if Brann picks route B, after PRs #7, #8 and #10 are merged.

## Known gaps (not fixed by this PR)
- **Landing copy and CTA** (EMP-WL-001): `TODO(WL-001, Brann)` slots mark where waitlist-mode copy
  goes.
- **Contact addresses and brand** (EMP-WL-004/005), **mobile overflow** (EMP-WL-006),
  **confirmation email** (EMP-WL-008/009), **consent** (EMP-WL-010) and **claims** (EMP-WL-011).
- **Cost (estimate):** $0 on Cloudflare Pages' free plan (developers.cloudflare.com/pages/platform/limits).
  The backend host and domain aren't priced here; Brann decides.
