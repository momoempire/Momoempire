# The Barber's Ledger — handoff zip (docs only)

`barbers-ledger-handoff.zip` is the handoff package for **The Barber's Ledger**, a separate app.
It is **not** part of Momoempire and **not** part of Zip Focus. Nothing here is built, imported, or deployed by this repo.

Contents: `AUDIT_AND_PLAN.md`, `scripts/backup.sh`, `scripts/restore.sh`, `scripts/verify_restore.sh`.
No secrets or live keys are included (scripts read `MONGO_URL`, `DB_NAME`, `BACKUP_PASSPHRASE` from the environment).
