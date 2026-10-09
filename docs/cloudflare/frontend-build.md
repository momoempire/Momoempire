# Cloudflare-2 frontend build and preview

Frontend is Create React App via CRACO. The build script is `craco build`, output is `frontend/build`, and Cloudflare Pages serves that directory. The backend API origin is compiled into `REACT_APP_BACKEND_URL` during build.

A frontend lockfile was not found. Generate and commit one before enforcing frozen installs. Two platform-specific frontend development dependencies remain and must be reviewed for portability. A fresh build and preview deploy have not been executed. Do not enable automatic deployments or paid CI without approval.

## Portability progress (2026-10-08, later review)
- Removed the external Emergent runtime script and embedded Emergent PostHog tracking from `frontend/public/index.html`; updated title and description.
- `frontend/package.json` still includes two Emergent-hosted development dependencies; removal could not be committed in this run, so a fully portable install is **not yet proven**.
- `frontend/yarn.lock` and `frontend/package-lock.json` are absent. Do **not** use `yarn install --frozen-lockfile` until a generated lockfile is reviewed and committed.
- Safe preflight on a local/staging clone: `cd frontend && yarn install --non-interactive && yarn build`. Commit the resulting lockfile only after dependency review. This is an instruction, not a claimed successful build.
- Production deployment remains disabled pending explicit approval, an actual clean build, and approved backend URL.

## 2026-10-09 configuration check
- Updated root `wrangler.toml` guidance to use `yarn install --non-interactive` instead of `--frozen-lockfile` until a reviewed `frontend/yarn.lock` is committed (commit `ee115ee2d6554797a23c388435f9ca441dc5695e`).
- Confirmed `frontend/package.json` still references two Emergent-hosted development tarballs. Review/remove them after checking source usage.
- A fresh dependency installation, compiled build, and Cloudflare preview deployment have **not** been executed; Task EMP-CF-003 remains in progress.
