# Waitlist-only deploy (EMP-WL-002, route A)

**Status:** steps only. Nothing here has been deployed, and no accounts were created. Whether to
launch with this route or the full stack (EMP-WL-003) is **Brann's decision**.

> **Deploy only after PR #11 is merged** (EMP-WL-035). Before #11, the Dockerfile runs uvicorn with
> `--forwarded-allow-ips='*'`, so any client can fake `X-Forwarded-For` and dodge the per-IP rate
> limit, and compose publishes port 8001 on all interfaces. #11 fixes both and adds the honeypot,
> body caps, unique email and Turnstile option. Merge order: #11, then #13 (this route), then the
> follow-ups. PR #10 (strict CORS) is strongly recommended too; see "CORS_ORIGINS" below.

What you get:
- **Frontend:** a static build on Cloudflare Pages with only `/`, `/privacy` and `/terms`. Every
  other path redirects to `/`. Dashboard, admin and auth code are not in the bundle. The landing
  page shows no demo, trial, login or pricing buttons, and makes no `/api/plans` call.
- **Backend:** FastAPI serving only `GET`/`HEAD /api/health` and `POST /api/public/waitlist`.
  Everything else returns 404, including `/docs` and `/openapi.json`. There is no startup seeding.
  The waitlist POST must be `application/json` (415 otherwise). When `CORS_ORIGINS` is set, a
  request that has an `Origin` header must come from one of those origins (403 otherwise). Both
  errors carry the normal CORS headers for allowed origins, so the page can read them, and none
  for other origins (EMP-WL-036).

## Settings

| Where | Variable | Value | Why |
|---|---|---|---|
| Cloudflare Pages build env (Production) | `REACT_APP_WAITLIST_ONLY` | `true` | Builds the waitlist-only app. Read at **build** time, so changing it needs a rebuild. |
| Cloudflare Pages build env (Production) | `REACT_APP_BACKEND_URL` | the backend's HTTPS origin, e.g. `https://api.example.com` | Where the form posts. Build time. |
| Backend env (`backend/.env`) | `WAITLIST_ONLY` | `true` | Minimal API (health + waitlist only). |
| Backend env | `CORS_ORIGINS` | the exact frontend origins, comma-separated, e.g. `https://<project>.pages.dev,https://www.example.com` | **Required**, including for a same-origin setup (see below). Every hostname needs its own entry (Pages URL, apex, www). No paths, https only, **all lower case** (see "Use lower case" below). What happens without it is described under "CORS_ORIGINS" below. |
| Backend env | `APP_ENV` | `production` | With PR #10: strict CORS (https only, explicit origins, fail closed). Without #10 it has no effect on CORS. |
| Backend env | `FORWARDED_ALLOW_IPS` | the proxy's IP as uvicorn sees it (table below) | **Required** (needs #11). Without the right value, every visitor shares the proxy's rate-limit bucket. Never `*`. |
| Backend env | `WAITLIST_CONFIRMATION_EMAIL` | leave **unset** (or `false`) | Needs #15. Unset means **off**: no confirmation email is sent, queued or logged. Only `1`/`true`/`yes`/`on` turns it on. Don't turn it on for launch: the email template still mentions a free trial and links aioffice.io pricing (EMP-WL-004/009, Brann decides). (EMP-WL-045) |
| Backend env | `MONGO_URL`, `DB_NAME` | as for any deploy | Where waitlist signups are stored. `JWT_SECRET` isn't used by the two served routes, but keep the usual value. |

Optional (needs #11): `TURNSTILE_ENABLED`, `TURNSTILE_SECRET_KEY` and the frontend's
`REACT_APP_TURNSTILE_SITE_KEY`.

### CORS_ORIGINS: what actually happens (EMP-WL-035)
The server **starts** in every case below; nothing here stops startup.

| `CORS_ORIGINS` | Without PR #10 (#13 + #11 only) | With PR #10 |
|---|---|---|
| Set to the frontend origins | Only those origins can read responses; the guard returns 403 for any other `Origin`. The form works. | Same. Entries are validated (https in production); invalid ones are skipped with an error log. |
| **Unset**, `APP_ENV=production` | **Wide open.** Any origin can read responses (credentials included), and the guard checks only Content-Type, so any website can submit JSON signups from a visitor's browser. | **Fails closed.** An error is logged at startup and no origin gets CORS headers, so the browser blocks the form's cross-origin JSON POST at preflight. Non-browser clients (curl) still get through, bounded by #11's rate limit. |
| Unset, development | Any origin can read (echoed origin, credentials allowed). | Any origin can read (`*`, no credentials). |

So: always set `CORS_ORIGINS`. Without #10, a missing value is silent and unsafe; with #10 it is
logged but the form stops working.

**Use lower case (EMP-WL-052).** Write every entry in lower case, e.g. `https://www.example.com`,
not `https://WWW.Example.com`. Browsers always send a lower-case `Origin`. Without PR #10, a
mixed-case entry passes the waitlist guard (it compares case-insensitively) but Starlette's CORS
compares exactly, so the browser blocks the form. PR #10 lower-cases entries for you; lower case
works either way. Responses to a disallowed origin carry no `Access-Control-Allow-Origin` and,
since the waitlist follow-ups PR, no `Access-Control-Allow-Credentials` either.

**Same-origin setups still need it.** If the frontend and the API share one origin (for example
Nginx serves the static build and proxies `/api` on `https://www.example.com`), the browser still
sends `Origin: https://www.example.com` on the waitlist POST. Once `CORS_ORIGINS` is set, the guard
rejects any Origin not on the list with 403, so that origin must be listed too:
`CORS_ORIGINS=https://www.example.com` (add the apex or other hostnames the page is served from).

### FORWARDED_ALLOW_IPS: which value (EMP-WL-035)
Uvicorn (0.25, exact IPs only, no CIDR) uses `X-Forwarded-For` only from these peers. Details and
the Nginx block: `client-ip-and-proxies.md` (from #11).

| Setup | Value |
|---|---|
| `docker compose` (this repo) with Nginx or cloudflared on the host | The compose network gateway, `172.30.0.1` by default. With #11 alone, set `FORWARDED_ALLOW_IPS=172.30.0.1` in `backend/.env`. With the follow-ups PR (#14), compose sets it from `AIO_COMPOSE_GATEWAY` automatically. |
| Uvicorn directly on the host behind host Nginx (or cloudflared) | `127.0.0.1` (the default when empty). |
| Cloudflare in front | Cloudflare's ranges are **not** set here (uvicorn 0.25 can't take CIDR ranges). Put them in Nginx (`set_real_ip_from` + `real_ip_header CF-Connecting-IP`), and set this to Nginx's address from the two rows above. Cloudflare straight to uvicorn is not supported. |

## Steps
1. **Backend host.** Start the backend with the backend variables above (Docker: `docker compose
   up -d backend` picks up `backend/.env` through `env_file`; compose needs no change). Check the
   startup log for `WAITLIST_ONLY is on: serving only /api/health and POST /api/public/waitlist`.
2. **Backend checks** (replace the host):
   - `curl -i https://API/api/health` gives 200 `{"status":"ok"}`. `curl -I` (HEAD) gives 200.
     `{"status":"degraded"}` means the database ping failed or (with #18) the unique email index
     couldn't be built: see `client-ip-and-proxies.md`, "Unique email index and the dedupe script".
     With #18 the waitlist app builds that index at startup and, with `APP_ENV=production`, refuses
     to start if it can't.
   - `curl -i https://API/docs` and `curl -i https://API/api/auth/me` give 404.
   - `curl -i -X POST https://API/api/public/waitlist -H 'Content-Type: text/plain' -d '{}'` gives 415.
   - `curl -i -X OPTIONS https://API/api/public/waitlist -H 'Origin: https://evil.example' -H 'Access-Control-Request-Method: POST'`
     should **not** return `access-control-allow-origin: https://evil.example`.
   - `curl -i -X POST https://API/api/public/waitlist -H 'Origin: https://evil.example' -H 'Content-Type: application/json' -d '{}'`
     gives 403 with no `access-control-allow-origin` header.
   - `curl -i -X POST https://API/api/public/waitlist -H 'Origin: https://<your frontend origin>' -H 'Content-Type: text/plain' -d '{}'`
     gives 415 **with** `access-control-allow-origin: https://<your frontend origin>`.
   - Send one real signup through the proxy and check the stored waitlist `ip` is your own IP, not
     the proxy's (confirms `FORWARDED_ALLOW_IPS`).
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
  **consent** (EMP-WL-010) and **claims** (EMP-WL-011).
- **Confirmation email:** off by default with #15 (`WAITLIST_CONFIRMATION_EMAIL`, see Settings).
  Whether to send one, and its wording, is EMP-WL-009 (Brann decides).
- **Cost (estimate):** $0 on Cloudflare Pages' free plan (developers.cloudflare.com/pages/platform/limits).
  The backend host and domain aren't priced here; Brann decides.
