"""De-duplicate the public waitlist by normalized email (EMP-WL-012 / EMP-WL-024).

DRY RUN BY DEFAULT: prints counts and changes nothing. Pass --apply to make the changes.

    cd backend && python scripts/dedupe_waitlist.py            # dry run
    cd backend && python scripts/dedupe_waitlist.py --apply    # archive duplicates + build index

What --apply does (same code the app runs once at startup, see routers/marketing.py):
- keeps the EARLIEST row per trimmed + lowercased email;
- copies each later duplicate in full into `waitlist_duplicates` (duplicate_of, archived_at),
  then removes it from `waitlist`; nothing is merged into the kept row;
- normalizes the kept row's email (original kept in `email_original`);
- builds the unique index on `waitlist.email`.
Idempotent: a second run finds nothing to do. Reads MONGO_URL and DB_NAME (and backend/.env if
python-dotenv is installed). Prints counts only, never email addresses.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(BACKEND / ".env", override=False)
    except ImportError:
        pass
    os.environ.setdefault("JWT_SECRET", "dedupe-script")  # imported modules expect it; unused here


async def main(apply: bool) -> int:
    _load_env()
    if not os.environ.get("MONGO_URL") or not os.environ.get("DB_NAME"):
        print("MONGO_URL and DB_NAME must be set", file=sys.stderr)
        return 2
    from motor.motor_asyncio import AsyncIOMotorClient
    from routers.marketing import build_waitlist_unique_index, dedupe_waitlist

    client = AsyncIOMotorClient(os.environ["MONGO_URL"], serverSelectionTimeoutMS=5000)
    db = client[os.environ["DB_NAME"]]
    try:
        stats = await dedupe_waitlist(db, apply=apply)
        index_ok = None
        if apply:
            try:
                await build_waitlist_unique_index(db)  # same index spec as the app (EMP-WL-040)
                index_ok = True
            except Exception as e:  # noqa: BLE001
                # Type only: the driver's E11000 text includes the duplicate email (no PII in output).
                print(f"unique index build failed ({type(e).__name__}). If an older non-unique "
                      "'email_1' index exists, drop it and run again.", file=sys.stderr)
                index_ok = False
    finally:
        client.close()
    mode = "APPLIED" if apply else "DRY RUN (no changes; pass --apply)"
    print(f"{mode}: scanned={stats['scanned']} duplicate_groups={stats['duplicate_groups']} "
          f"to_archive={stats['archived']} to_normalize={stats['normalized']}"
          + ("" if index_ok is None else f" unique_index={'ok' if index_ok else 'FAILED'}"))
    return 0 if index_ok in (None, True) else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="make the changes (default: dry run)")
    sys.exit(asyncio.run(main(ap.parse_args().apply)))
