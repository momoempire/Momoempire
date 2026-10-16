# Cloudflare preview (test) deploy of the waitlist-only site

**Status:** instructions only. Nothing here has been deployed and no Cloudflare account was used or
created. These steps make a **preview** (test) deployment, not production. Whether and when to run
them is **Brann's decision**.

Markers:
- **[BRANN]**: needs Brann's explicit approval before doing it.
- **[SIGNUP]**: needs an account signup or sign-in that only Brann can do or authorize.
- All cost and time figures are **estimates**, each with a source.

Companion docs: `waitlist-only.md` (settings and checks; this doc reuses them, with the
differences for preview noted below), `site-metadata.md`, `client-ip-and-proxies.md`,
`../cloudflare/frontend-build.md`.

## 0. Before you start
1. Confirm Cloudflare-2 is at the reviewed tip and its CI/local tests pass (backend pytest,
   frontend Jest, both builds). No account needed.
2. **[BRANN]** Approve running a preview deploy at all, and which backend it talks to (step 2).
3. **[SIGNUP]** A Cloudflare account (free plan is enough for Pages). Brann signs up or signs in;
   no bot creates it. **Cost (estimate): $0** on the Free plan, limits 500 builds/month, 1
   concurrent build, 20,000 files, 25 MiB per file, unlimited preview deployments. Source:
   https://developers.cloudflare.com/pages/platform/limits/
4. **[SIGNUP]** GitHub access for Cloudflare's Git integration (the Cloudflare GitHub app on the
   `momoempire/Momoempire` repo), **or** skip Git and use Direct Upload with Wrangler (step 4B).
   Installing the GitHub app grants repo access, so it needs Brann's OK.

## 1. Build locally first (no account)
```
cd frontend
yarn install --non-interactive
REACT_APP_WAITLIST_ONLY=true GENERATE_SOURCEMAP=false \
REACT_APP_BACKEND_URL=https://<test-api-host> yarn build
```
Check `frontend/build` has no `.map` files and `grep -r sourceMappingURL build/static/js` is empty.
**Time (estimate):** a few minutes for install plus 1-3 min for the build on a normal laptop
(source: our own box runs, see the EMP tracker test rows; not a Cloudflare figure).

## 2. Test backend
A preview frontend needs an API to post signups to. Options (Brann picks, **[BRANN]**):
- **A. No backend.** Deploy the frontend only; the form will show an error on submit. Good enough
  to check routes, buttons, metadata and source maps (step 6), not signups.
- **B. A test backend** with `WAITLIST_ONLY=true` and a **separate test database** (never the
  production DB). Hosting it is a purchase/signup decision **[BRANN] [SIGNUP]**; not priced here.
  Set `CORS_ORIGINS` to the exact preview origin(s), lower case, e.g.
  `https://<branch>.<project>.pages.dev` (each preview alias is its own origin), plus
  `APP_ENV=production`, the right `FORWARDED_ALLOW_IPS` and leave `WAITLIST_CONFIRMATION_EMAIL`
  unset, exactly as in `waitlist-only.md` "Settings". Run the backend checks from
  `waitlist-only.md` step 2 against it.

## 3. Create the Pages project **[BRANN] [SIGNUP]**
In the Cloudflare dashboard: Workers & Pages, Create, Pages.
- Git route: connect `momoempire/Momoempire`, production branch **not** `Cloudflare-2` yet (use a
  branch that will never be pushed, or keep `Cloudflare-2` but disable production auto-deploys,
  see below). Root directory `frontend`, build command `yarn install --non-interactive && yarn
  build`, output `build` (matches `wrangler.toml`: `pages_build_output_dir = ./frontend/build`
  from the repo root).
- **Turn off automatic production deployments** and, if wanted, limit preview builds to one test
  branch (Settings, Builds, Branch control). Source:
  https://developers.cloudflare.com/pages/configuration/branch-build-controls/

## 4. Variables for the **Preview** environment
`waitlist-only.md` says to put the Pages variables in **Production**. For a test deploy put them in
the **Preview** environment instead (Settings, Variables and Secrets, Preview):

| Variable | Value |
|---|---|
| `REACT_APP_WAITLIST_ONLY` | `true` |
| `REACT_APP_BACKEND_URL` | the test backend origin from step 2 (or any placeholder https URL for option A) |
| `GENERATE_SOURCEMAP` | `false` |
| `REACT_APP_SITE_NAME`, `_DESCRIPTION`, `_SITE_URL`, `_OG_IMAGE_URL` | test values or unset (placeholder title); URLs must be absolute https or the build fails |
| `REACT_APP_TURNSTILE_ENABLED` | leave unset/false unless the test backend has Turnstile on (EMP-WL-091, **[BRANN]**) |

These are build-time variables, so changing one needs a new build.

### 4A. Git preview build **[BRANN]**
Push a test branch (e.g. `cf-preview-test`) from Cloudflare-2. Pages builds it as a preview at
`https://<branch>.<project>.pages.dev` plus a unique per-deploy URL. Source:
https://developers.cloudflare.com/pages/configuration/preview-deployments/
**Time (estimate):** a few minutes per build; Pages builds time out at 20 min. Source:
https://developers.cloudflare.com/pages/platform/limits/

### 4B. Direct Upload instead of Git **[BRANN] [SIGNUP]**
Build locally with the variables in step 1, then
`npx wrangler pages deploy frontend/build --project-name <project> --branch cf-preview-test`.
Any `--branch` other than the production branch makes a preview deployment. `wrangler login`
opens a browser sign-in only Brann can approve. Source:
https://developers.cloudflare.com/pages/get-started/direct-upload/
Note: a project made by Direct Upload cannot later be switched to Git integration (same source).

## 5. Restrict who can see the preview (optional) **[BRANN]**
Preview URLs are public by default. Cloudflare Access can put them behind a login (Settings,
General, "Enable access policy"). Access is free for up to 50 users (estimate, plan pricing can
change). Source: https://www.cloudflare.com/plans/zero-trust-services/ and
https://developers.cloudflare.com/pages/configuration/preview-deployments/

## 6. Preview checks (no extra account)
Run `waitlist-only.md` step 4 against the preview URL:
- `/` shows the landing page and form; `/login`, `/admin`, `/pricing` land on `/`.
- No Live demo, Try it free, Log in or Pricing buttons; no `/api/plans` request.
- No `.map` files in the build output and no `sourceMappingURL` in `static/js/main.*.js`.
- `<title>` and `og:*` show the test `REACT_APP_SITE_*` values.
- Option B only: submit one test address (an address you own, e.g. `+test` alias) and check it is
  stored once in the **test** DB; a request from a non-listed Origin gets 403.
- Search engines: preview `*.pages.dev` URLs get `X-Robots-Tag: noindex` from Pages by default
  (source: https://developers.cloudflare.com/pages/configuration/preview-deployments/).

## 7. Clean up **[BRANN]**
Delete the preview deployment(s) or the whole test project in the dashboard when done, drop the
test DB and stop the test backend. Deleting anything in the account needs Brann's OK.

## Not covered
- Production deploy, custom domain, DNS: separate, all **[BRANN]**.
- Backend host cost and domain cost: not estimated here; Brann decides.
