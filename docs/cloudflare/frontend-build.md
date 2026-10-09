# Cloudflare-2 frontend build and preview

Frontend is Create React App via CRACO. The build script is `craco build`, output is `frontend/build`, and Cloudflare Pages serves that directory. The backend API origin is compiled into `REACT_APP_BACKEND_URL` during build.

A frontend lockfile was not found. Generate and commit one before enforcing frozen installs. The two Emergent-hosted development tarballs were subsequently removed from package.json; dev-only guarded references remain in craco.config.js. A fresh build and preview deploy have not been executed. Do not enable automatic deployments or paid CI without approval.

## Portability progress (2026-10-08, later review)
- Removed the external Emergent runtime script and embedded Emergent PostHog tracking from `frontend/public/index.html`; updated title and description.
- Historical note (2026-10-08): removal of two Emergent-hosted development dependencies was not yet committed; this was resolved on 2026-10-09, but a fully portable install is **not yet proven**.
- `frontend/yarn.lock` and `frontend/package-lock.json` are absent. Do **not** use `yarn install --frozen-lockfile` until a generated lockfile is reviewed and committed.
- Safe preflight on a local/staging clone: `cd frontend && yarn install --non-interactive && yarn build`. Commit the resulting lockfile only after dependency review. This is an instruction, not a claimed successful build.
- Production deployment remains disabled pending explicit approval, an actual clean build, and approved backend URL.

## 2026-10-09 configuration check
- Updated root `wrangler.toml` guidance to use `yarn install --non-interactive` instead of `--frozen-lockfile` until a reviewed `frontend/yarn.lock` is committed (commit `ee115ee2d6554797a23c388435f9ca441dc5695e`).
- Historical observation: `frontend/package.json` referenced two Emergent-hosted development tarballs at the time of this check; they were removed later on 2026-10-09.
- A fresh dependency installation, compiled build, and Cloudflare preview deployment have **not** been executed; Task EMP-CF-003 remains in progress.

## 2026-10-09 dependency portability update
- Removed the two optional Emergent-hosted development tarball dependencies (`@emergentbase/overlay` and `@emergentbase/visual-edits`) from `frontend/package.json` on Cloudflare-2 (commit `20ba54bbb20fae23bfce9a1c2c2e0db0b436d04d`). CRACO loads these only through development-mode optional `require` paths, which already handle missing modules; this change does not remove application routes.
- A dependency lockfile is still absent and a clean install / `yarn build` has not been executed. Task EMP-CF-003 stays in progress pending actual build validation; do not claim deploy readiness or enable production deployment.

## Current verification gap (2026-10-09)
- `packageManager` specifies Yarn 1.22.22. Neither a reviewed lockfile nor an executed clean dependency install/build is available.
- On a safe isolated checkout of Cloudflare-2, generate and review `frontend/yarn.lock`, then run `yarn install --frozen-lockfile --non-interactive` and `yarn build`; record actual output and commit only the reviewed lockfile.
- Keep automatic production deployments disabled until the build, API origin, security, and staging checks have been verified.
