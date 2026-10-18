# Backup & Recovery — Momo platform

Status: written by the Track D security audit, 2026-10-17. Anything marked
**Needs Testing** has not been exercised. Anything marked **Assumed** is a
platform-level expectation that is NOT evidenced anywhere in this repo and
must be verified by the operator in the hosting/DB consoles before launch.

## What exists today (verified in repo)

- **No application-level backup exists.** A full-text sweep of code, config
  and docs (2026-10-17) found no backup job, no `mongodump`/`mongorestore`
  schedule, no retention policy, and no restore runbook. The only
  `mongodump` mention (HANDOFF.md) describes a one-time migration cutover,
  not a recurring backup.
- HANDOFF.md itself lists "one full backup+restore cycle has been
  validated" as an outstanding condition for the hosting plan — i.e. **a
  restore has never been exercised (Needs Testing).**
- The application's data lives in a single MongoDB database (`DB_NAME`),
  reached via `MONGO_URL`. There is no secondary store to reconcile
  against; Stripe is the external system of record for payments and can be
  used to sanity-check financial data after a restore.

## Platform-level backups (Assumed — verify before launch)

Backups, if any, come entirely from the infrastructure providers:

| Layer | Assumed mechanism | Must verify |
|---|---|---|
| MongoDB (Atlas) | Atlas automated snapshots / continuous backup, depending on cluster tier. Free/shared tiers (M0/M2/M5) have NO point-in-time restore and limited/no snapshot restore guarantees. | Cluster tier; whether snapshots are enabled; actual snapshot frequency; retention window; whether point-in-time recovery exists on this tier. |
| App hosting (Render or other) | Stateless service — no data to back up beyond env vars and the repo. | Env-var inventory is reproducible from the deploy checklist; no persistent disk holds business data. |
| Code | This GitHub repo is the source of truth. | — |

**No schedule or retention figure is stated here on purpose**: none is
documented in the repo, and inventing one would be worse than the gap.
The operator must record the verified values in the table above (provider
console → cluster → Backup) as a launch-checklist item.

## Restore procedure (draft — Needs Testing)

1. Provision/select the target cluster; do NOT restore over the production
   database until the restored copy is verified.
2. From the provider console, restore the chosen snapshot / point-in-time
   into a **separate** database or cluster.
3. Point a staging deployment at the restored database (`MONGO_URL`,
   `DB_NAME`) with production integrations disabled (no Twilio/Stripe live
   keys in the staging env).
4. Verify: tenant count, user count, invoice count and invoice totals vs.
   Stripe's records for the same window; spot-check one tenant's customers,
   appointments and integration settings (note: integration secrets and 2FA
   secrets are Fernet-encrypted with operator env keys — the restored data
   is only readable if `TWO_FACTOR_ENCRYPTION_KEY` /
   `BOOKKEEPING_LINK_ENCRYPTION_KEY` are the SAME keys as at backup time;
   losing those keys makes encrypted fields unrecoverable even with a
   perfect database restore).
5. Record: backup timestamp used, restore duration, verification results,
   and the effective data-loss window (time between last backup and the
   incident). This record is the recovery drill; repeat at least quarterly
   and after any provider/tier change.
6. Only after verification, cut production over to the restored database.

## Operator decisions required

- MongoDB tier: does the launch cluster tier provide point-in-time
  recovery? If not, what data-loss window is acceptable?
- Retention: how many days of snapshots to keep (cost vs. safety).
- Key escrow: where are the Fernet encryption keys backed up (separately
  from the database and from the hosting env), and who can access them?
- Drill cadence: who runs the restore drill, and how often?

Until the drill in step 5 has been run once and recorded, the platform's
recovery capability is **unproven (Needs Testing)** — plan the launch
accordingly.
