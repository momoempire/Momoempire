"""Waitlist export and removal (EMP-WL-015). See docs/ops/waitlist-data.md.

DRY RUN BY DEFAULT: nothing is written or deleted unless you pass --apply.

    cd backend
    python scripts/waitlist_data.py export                       # dry run: row count + columns
    python scripts/waitlist_data.py export --out /secure/wl.csv --apply
    python scripts/waitlist_data.py delete                       # dry run; email read from stdin
    python scripts/waitlist_data.py delete --apply               # counts, then asks you to type DELETE

- Scope: only the `waitlist` collection (export) and `waitlist` + `waitlist_duplicates`
  (delete; the archive collection exists once PR #14's dedupe has run). Nothing else is touched.
- Export writes only the chosen columns (default: email, name, business_name, industry,
  created_at; never ip), escapes spreadsheet formulas, creates the file with mode 0600 and
  refuses to overwrite an existing file.
- Delete matches the email trimmed and case-insensitively (older rows were stored as typed),
  in `email` and in the archived `email_original`. The email is read from stdin (not argv) so it
  does not land in shell history or the process list.
- Output is counts only: no email addresses or other row data are printed or logged.
- Credentials (EMP-WL-074): MONGO_URL and DB_NAME come from the environment or from
  --env-file PATH (a 0600 file holding the DEDICATED least-privilege user's URI). backend/.env
  (the app's own credentials) is never read.
- Delete --apply (EMP-WL-075) shows the counts, then asks you to type DELETE on the terminal.
  Without a terminal, pass --confirm DELETE. TODO(Brann): production check and audit record.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

WAITLIST = "waitlist"
ARCHIVE = "waitlist_duplicates"  # PR #14 (EMP-WL-024) dedupe archive
DEFAULT_FIELDS = ("email", "name", "business_name", "industry", "created_at")
# Columns an export may include. `ip` is deliberately absent; `id`/`_id` are internal.
ALLOWED_FIELDS = DEFAULT_FIELDS + ("note", "source", "source_detail", "estimated_tier")
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def normalize_email(raw: str) -> str:
    return (raw or "").strip().lower()


def email_filter(email: str) -> dict:
    """Rows whose email (or archived original) equals `email` after trim + lowercase."""
    norm = normalize_email(email)
    if not norm or "@" not in norm:
        raise ValueError("not an email address")
    pattern = re.compile(r"^\s*" + re.escape(norm) + r"\s*$", re.IGNORECASE)
    return {"$or": [{"email": norm}, {"email": pattern}, {"email_original": pattern}]}


def csv_safe(value) -> str:
    """Stringify and neutralise spreadsheet formulas (CSV injection)."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        value = value.astimezone(timezone.utc).isoformat()
    s = str(value)
    if s.startswith(_FORMULA_START):
        s = "'" + s
    return s


def parse_fields(spec: str | None) -> tuple[str, ...]:
    if not spec:
        return DEFAULT_FIELDS
    fields = tuple(f.strip() for f in spec.split(",") if f.strip())
    bad = [f for f in fields if f not in ALLOWED_FIELDS]
    if bad or not fields:
        raise ValueError(f"unsupported column(s): {', '.join(bad) or '(none)'}; allowed: {', '.join(ALLOWED_FIELDS)}")
    return fields


def export_rows(db, fields: tuple[str, ...]):
    projection = {f: 1 for f in fields}
    projection["_id"] = 0
    return db[WAITLIST].find({}, projection).sort("created_at", 1)


def write_csv(path: Path, rows, fields: tuple[str, ...]) -> int:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)  # never overwrite
    n = 0
    with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(fields)
        for row in rows:
            w.writerow([csv_safe(row.get(f)) for f in fields])
            n += 1
    return n


def run_export(db, out: str | None, fields: tuple[str, ...], apply: bool, echo=print) -> int:
    count = db[WAITLIST].count_documents({})
    echo(f"export: {count} waitlist row(s); columns: {', '.join(fields)}")
    if not apply:
        echo("dry run: no file written (pass --out PATH --apply to write the CSV)")
        return 0
    if not out:
        echo("--out PATH is required with --apply")
        return 2
    path = Path(out)
    if path.exists():
        echo("refusing to overwrite an existing file; choose a new path")
        return 2
    n = write_csv(path, export_rows(db, fields), fields)
    echo(f"wrote {n} row(s) to the output file (mode 0600). Delete it when you are done.")
    return 0


def run_delete(db, email: str, apply: bool, echo=print, confirm=None) -> int:
    try:
        flt = email_filter(email)
    except ValueError:
        echo("no valid email address given")
        return 2
    counts = {c: db[c].count_documents(flt) for c in (WAITLIST, ARCHIVE)}
    echo(f"delete: matches {counts[WAITLIST]} row(s) in {WAITLIST}, {counts[ARCHIVE]} in {ARCHIVE}")
    if not apply:
        echo("dry run: nothing deleted (pass --apply to delete)")
        return 0
    if counts[WAITLIST] + counts[ARCHIVE] == 0:
        echo("nothing to delete")
        return 0
    # EMP-WL-075: cheap safeguard, a typed confirmation after the counts are on screen.
    # TODO(Brann): production check (e.g. APP_ENV) and an audit record (who/when/request ID,
    # no email) are not decided yet; add them here.
    answer = (confirm or _confirm_from_tty)(f"Type {CONFIRM_WORD} to delete these row(s): ")
    if (answer or "").strip() != CONFIRM_WORD:
        echo("not confirmed: nothing deleted")
        return 3
    deleted = {c: db[c].delete_many(flt).deleted_count for c in (WAITLIST, ARCHIVE)}
    left = sum(db[c].count_documents(flt) for c in (WAITLIST, ARCHIVE))
    echo(f"deleted {deleted[WAITLIST]} from {WAITLIST}, {deleted[ARCHIVE]} from {ARCHIVE}; remaining matches: {left}")
    return 0 if left == 0 else 1


CONFIRM_WORD = "DELETE"


def _confirm_from_tty(prompt: str) -> str:
    """Read the confirmation from the terminal, not stdin (stdin may carry the email)."""
    try:
        # Unbuffered binary: a text "r+" open fails on a terminal (not seekable).
        with open("/dev/tty", "rb+", buffering=0) as tty:
            tty.write(prompt.encode())
            return tty.readline().decode("utf-8", "replace")
    except OSError:
        print(f"no terminal for the confirmation; pass --confirm {CONFIRM_WORD}", file=sys.stderr)
        return ""


def _load_env_file(path: str) -> str | None:
    """EMP-WL-074: load MONGO_URL / DB_NAME from an operator file (the dedicated user's URI).
    Returns an error message, or None. The file must not be readable by group/others."""
    p = Path(path)
    try:
        mode = p.stat().st_mode
    except OSError:
        return "--env-file: file not found"
    if mode & 0o077:
        return "--env-file: must be mode 0600 (chmod 600 FILE)"
    from dotenv import dotenv_values
    for key, value in dotenv_values(p).items():
        if key in ("MONGO_URL", "DB_NAME") and value:
            os.environ[key] = value
    return None


def _read_email(stdin=None) -> str:
    stdin = stdin or sys.stdin
    if stdin.isatty():
        import getpass
        return getpass.getpass("Email to remove (not echoed): ")
    return stdin.readline()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Waitlist export / removal (dry run unless --apply).")
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export", help="CSV of the waitlist with only the chosen columns")
    e.add_argument("--out", help="output CSV path (must not exist)")
    e.add_argument("--fields", help=f"comma-separated columns (default {','.join(DEFAULT_FIELDS)})")
    e.add_argument("--apply", action="store_true", help="actually write the file")
    d = sub.add_parser("delete", help="remove one person by email (read from stdin)")
    d.add_argument("--apply", action="store_true", help="actually delete (asks you to type DELETE)")
    d.add_argument("--confirm", help=f"non-interactive confirmation: must be exactly {CONFIRM_WORD}")
    for sp in (e, d):
        sp.add_argument("--env-file", help="0600 file with MONGO_URL/DB_NAME of the dedicated DB user")
    args = p.parse_args(argv)

    if args.cmd == "export":
        try:
            fields = parse_fields(args.fields)
        except ValueError as ex:
            print(str(ex), file=sys.stderr)
            return 2

    if args.env_file:
        err = _load_env_file(args.env_file)
        if err:
            print(err, file=sys.stderr)
            return 2
    if not os.environ.get("MONGO_URL") or not os.environ.get("DB_NAME"):
        print("MONGO_URL and DB_NAME must be set (environment or --env-file; backend/.env is never read)",
              file=sys.stderr)
        return 2
    from pymongo import MongoClient

    client = MongoClient(os.environ["MONGO_URL"], serverSelectionTimeoutMS=5000)
    try:
        db = client[os.environ["DB_NAME"]]
        if args.cmd == "export":
            return run_export(db, args.out, fields, args.apply)
        confirm = (lambda _prompt: args.confirm) if args.confirm is not None else None
        return run_delete(db, _read_email(), args.apply, confirm=confirm)
    except Exception as ex:  # noqa: BLE001 - type only: driver messages can quote row data
        print(f"failed ({type(ex).__name__})", file=sys.stderr)
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
