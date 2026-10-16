# Cloudflare Pages + Docker operations plan
Date: 2026-10-08. Planning only; no DNS, hosting, or billing changes.

## Topology
- Frontend: Cloudflare Pages builds the React app from frontend/ and serves frontend/build.
- Backend: separate HTTPS Docker service exposes FastAPI; Cloudflare Pages does not run this Python backend.
- Database: MongoDB with separate staging and production credentials.
- Browser API URL: REACT_APP_BACKEND_URL points to the approved HTTPS backend origin.

## Prelaunch
1. Record current DNS, TTLs, certificate coverage, and rollback target.
2. Verify staging backend health, database access, and the explicit frontend CORS origin.
3. Set service credentials only in approved hosting secret stores.
4. Check deployment logs for startup errors and remove sensitive logging.
5. Verify a tested backup and restore for the database, including recovery time.
6. Define alerts for API availability, error rates, payment webhook failures, and storage capacity.
7. Confirm owners for incident response, monitoring, and rollback.
8. Get explicit approval before changing DNS or enabling production billing.

## Rollback
- Preserve the last known-good frontend deployment and backend image identifier.
- If release fails, restore the prior Pages deployment and backend image.
- Avoid schema changes without an independently verified rollback or backup.
- Restore DNS only if necessary and authorized; verify API and user flows after rollback.

## Open dependencies
Production host and domain selection, credentials, database backup destination, real health checks, and alert destination are not yet verified. No live failover or restore test performed.

## Follow-up verification (2026-10-08)
- Run the Cloudflare frontend build on an isolated staging runner.
- Confirm backend readiness on a staging host.
- Capture successful rollback and database restore evidence before release.
- Require an explicit go/no-go decision before changing production settings.

## 2026-10-09 verification notes (non-production)
- The Dockerfile checks both status=ok and db=ok. Compose still checks only HTTP success and must be aligned before launch.
- The public deployment diagnostics endpoint needs authorization before staging or production exposure.
- An isolated test pass is not a staging, payment, restore, or rollback test.
- Do not change DNS, billing, secrets, or production services without explicit approval.
