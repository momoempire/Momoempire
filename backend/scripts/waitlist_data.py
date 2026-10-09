"""Waitlist export and removal (EMP-WL-015). See docs/ops/waitlist-data.md.

DRY RUN BY DEFAULT: nothing is written or deleted unless you pass --apply.

    cd backend
    python scripts/waitlist_data.py export                       # dry run: row count + columns
    python scripts/waitlist_data.py export --out /secure/wl.csv --apply
    python scripts/waitlist_data.py delete                       # dry run; email read from stdin
    python scripts/waitlist_data.py delete --apply               # deletes after the dry-run counts

- Scope: only the `waitlist` collection (export) and `waitlist` + `waitlist_duplicates`
  (delete; the archive collection exists once PR #14's dedupe has run). Nothing else is touched.
- Export writes only the chosen columns (default: email, name, business_name, industry,
  created_at; never ip), escapes spreadsheet formulas, creates the file with mode 0600 and
  refuses to overwrite an existing file.
- Delete matches the email trimmed and case-insensitively (older rows were stored as typed),
  in `email` and in the archived `email_original`. The email is read from stdin (not argv) so it
  does not land in shell history or the process list.
- Output is counts only: no email addresses or other row data are printed or logged.
Reads MONGO_URL and DB_NAME (and backend/.env if python-dotenv is installed).
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


def run_delete(db, email: str, apply: bool, echo=print) -> int:
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
    deleted = {c: db[c].delete_many(flt).deleted_count for c in (WAITLIST, ARCHIVE)}
    left = sum(db[c].count_documents(flt) for c in (WAITLIST, ARCHIVE))
    echo(f"deleted {deleted[WAITLIST]} from {WAITLIST}, {deleted[ARCHIVE]} from {ARCHIVE}; remaining matches: {left}")
    return 0 if left == 0 else 1


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(BACKEND / ".env", override=False)
    except ImportError:
        pass


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
    d.add_argument("--apply", action="store_true", help="actually delete")
    args = p.parse_args(argv)

    if args.cmd == "export":
        try:
            fields = parse_fields(args.fields)
        except ValueError as ex:
            print(str(ex), file=sys.stderr)
            return 2

    _load_env()
    if not os.environ.get("MONGO_URL") or not os.environ.get("DB_NAME"):
        print("MONGO_URL and DB_NAME must be set", file=sys.stderr)
        return 2
    from pymongo import MongoClient

    client = MongoClient(os.environ["MONGO_URL"], serverSelectionTimeoutMS=5000)
    try:
        db = client[os.environ["DB_NAME"]]
        if args.cmd == "export":
            return run_export(db, args.out, fields, args.apply)
        return run_delete(db, _read_email(), args.apply)
    except Exception as ex:  # noqa: BLE001 - type only: driver messages can quote row data
        print(f"failed ({type(ex).__name__})", file=sys.stderr)
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
