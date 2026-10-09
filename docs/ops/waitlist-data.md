# Waitlist data: export and removal (EMP-WL-015)

The procedure for getting waitlist signups out of MongoDB, and for removing one person
when they ask. It applies to the **`waitlist`** collection, and to **`waitlist_duplicates`**, the
dedupe archive added by PR #14 (EMP-WL-024), once that exists. No other collection holds
waitlist signups.

> **TODO(Brann):** who may run these, how long exports may be kept, and the retention period
> for waitlist rows are Brann's decisions. The steps below work whatever is decided.

What a waitlist row holds: `id`, `email`, `name`, `business_name`, `industry`, `note`,
`source`, `source_detail`, `estimated_tier`, `ip`, `created_at`. After dedupe a row may also
have `email_original`; archived rows also carry `duplicate_of` and `archived_at`.
`ip` is kept for abuse limits only and is **never exported**.

## Access controls

- **Use dedicated database users rather than the app's credentials.** Use the least privilege for each job:
  - export: a role with only `find` on `<DB_NAME>.waitlist`;
  - removal: `find` and `remove` on `<DB_NAME>.waitlist` and `<DB_NAME>.waitlist_duplicates`.

  On Atlas, create these as custom database roles ("Database Access → Custom Roles"). On
  self-hosted MongoDB, use `db.createRole` (example below) and `db.createUser`.
- Keep the URI out of the command line, because `ps` and shell history can see it. Put it in `backend/.env`
  (the script reads it there) or use `mongoexport --config <file>` with mode 0600.
- Export files contain personal data. Write them to an encrypted, access-controlled location,
  never into the repo, chat, email or a shared drive without Brann's OK. Delete them when the job is
  done. The script creates files with mode 0600 and refuses to overwrite.
- Run removals from a trusted machine that can reach the DB directly. Do not expose a delete
  endpoint for this.

```js
// self-hosted example (mongosh, as an admin), names are placeholders
use <DB_NAME>
db.createRole({ role: "waitlistExport", privileges: [
  { resource: { db: "<DB_NAME>", collection: "waitlist" }, actions: ["find"] } ], roles: [] })
db.createRole({ role: "waitlistRemoval", privileges: [
  { resource: { db: "<DB_NAME>", collection: "waitlist" }, actions: ["find", "remove"] },
  { resource: { db: "<DB_NAME>", collection: "waitlist_duplicates" }, actions: ["find", "remove"] } ], roles: [] })
```

## No PII in logs or terminals

- The script prints **counts only**, never emails or row data. On failure it prints just the error type,
  because driver messages can quote values.
- The email to remove is read from **stdin**, with no echo on a terminal, so it never appears in
  shell history or `ps`. Do the same with mongosh (below).
- Don't paste exports or emails into tickets, chat or the tracker. Refer to a removal by date and
  request ID instead.

## Export (CSV with only the needed columns)

Script (recommended: formula-safe CSV, allow-listed columns, dry run first):

```bash
cd backend
python scripts/waitlist_data.py export                                   # dry run: count + columns
python scripts/waitlist_data.py export --out /secure/waitlist-$(date +%F).csv --apply
python scripts/waitlist_data.py export --fields email,name --out /secure/wl.csv --apply
```

- Default columns: `email,name,business_name,industry,created_at`. Allowed extras are `note`,
  `source`, `source_detail` and `estimated_tier`. `ip`, `id` and `_id` are refused. Export only what
  the job needs.
- Cells starting with `= + - @`, tab or CR get a leading `'`, so a signup can't inject a
  spreadsheet formula. Dates are written as ISO 8601 UTC.

`mongoexport` alternative. It is fine for a quick pull, but it does **not** escape formulas, so
don't open the result directly in a spreadsheet:

```bash
mongoexport --config /secure/mongo-export.yaml --db "$DB_NAME" --collection waitlist \
  --type=csv --fields email,name,business_name,industry,created_at \
  --sort '{created_at: 1}' --out /secure/waitlist.csv
chmod 600 /secure/waitlist.csv
```
(`--config` YAML holds `uri:`; mode 0600.)

## Removal by email (including the dedupe archive)

Emails are matched trimmed and case-insensitively, because rows from before PR #11 were stored as typed. Matching checks
`email`, plus `email_original` on rows that dedupe normalised or archived.

Script:

```bash
cd backend
python scripts/waitlist_data.py delete            # dry run: prompts for the email, prints match counts
python scripts/waitlist_data.py delete --apply    # deletes from waitlist + waitlist_duplicates
```
The `--apply` run prints the deleted counts and `remaining matches: 0`; it exits 1 if anything remains.
To script it without a prompt: `printf '%s\n' "$EMAIL" | python scripts/waitlist_data.py delete --apply`
(set `EMAIL` with `read -rs EMAIL`, not on the command line).

mongosh alternative (the email comes from an env var, so it isn't in the command or history):

```bash
read -rs WL_EMAIL && export WL_EMAIL
mongosh "$MONGO_URL/$DB_NAME" --quiet --eval '
  const e = process.env.WL_EMAIL.trim().toLowerCase();
  const re = new RegExp("^\\s*" + e.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "\\s*$", "i");
  const q = { $or: [ { email: e }, { email: re }, { email_original: re } ] };
  print("waitlist", db.waitlist.countDocuments(q), "archive", db.waitlist_duplicates.countDocuments(q));
  // after checking the counts, run again with the next two lines uncommented:
  // print("deleted", db.waitlist.deleteMany(q).deletedCount, db.waitlist_duplicates.deleteMany(q).deletedCount);
  // print("remaining", db.waitlist.countDocuments(q) + db.waitlist_duplicates.countDocuments(q));
'
unset WL_EMAIL
```

### Copies outside the database (check them when someone asks to be removed)
- **Exports**: any CSV made earlier. Delete or redact it.
- **Database backups / snapshots**: they keep the row until they rotate out. Note the request
  date, and if a backup is ever restored, re-run the removal. TODO(Brann): retention period.
- **Confirmation email provider**: if signup confirmations were sent (see WL-008 / PR #15), the
  provider's logs hold the address. Remove it there by hand if required.
- **App logs**: the waitlist code from PR #14 on logs no emails. Older builds printed the
  confirmation-email error text, which could include the address. Check the logs from that period
  if any are kept.
