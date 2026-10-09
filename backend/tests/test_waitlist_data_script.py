"""EMP-WL-015: scripts/waitlist_data.py (export + removal, dry run by default).

Unit tests always run; the database tests need a throwaway MongoDB at WAITLIST_TEST_MONGO_URL
(they create and drop their own database) and are skipped otherwise.
"""
import csv
import io
import os
import stat
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import waitlist_data as wd  # noqa: E402

MONGO = os.environ.get("WAITLIST_TEST_MONGO_URL")


# ---------- unit ----------

def test_csv_safe_neutralises_formulas_and_formats_dates():
    assert wd.csv_safe("=HYPERLINK(\"x\")") == "'=HYPERLINK(\"x\")"
    for s in ("+1", "-1", "@SUM(A1)", "\tx", "\rx"):
        assert wd.csv_safe(s).startswith("'")
    assert wd.csv_safe("Joe's HVAC") == "Joe's HVAC"
    assert wd.csv_safe(None) == ""
    assert wd.csv_safe(datetime(2026, 1, 2, 3, 4, 5)) == "2026-01-02T03:04:05+00:00"


def test_fields_allowlist_never_ip():
    assert wd.parse_fields(None) == wd.DEFAULT_FIELDS
    assert "ip" not in wd.DEFAULT_FIELDS and "ip" not in wd.ALLOWED_FIELDS
    assert wd.parse_fields("email, note") == ("email", "note")
    for bad in ("email,ip", "_id", "id", ","):
        with pytest.raises(ValueError):
            wd.parse_fields(bad)


def test_email_filter_is_exact_and_regex_safe():
    flt = wd.email_filter("  Owner@Example.COM ")
    assert flt["$or"][0] == {"email": "owner@example.com"}
    pat = flt["$or"][1]["email"]
    assert pat.match(" OWNER@example.com ")
    assert not pat.match("xowner@example.com") and not pat.match("owner@example.com.evil")
    assert not wd.email_filter("a.b@x.io")["$or"][1]["email"].match("aXb@x.io")  # '.' escaped
    for bad in ("", "   ", "not-an-email"):
        with pytest.raises(ValueError):
            wd.email_filter(bad)


def test_cli_rejects_bad_fields_before_touching_the_db(monkeypatch, capsys):
    monkeypatch.delenv("MONGO_URL", raising=False)
    assert wd.main(["export", "--fields", "email,ip"]) == 2
    assert "unsupported column" in capsys.readouterr().err


def test_cli_needs_db_env(monkeypatch, capsys):
    monkeypatch.delenv("MONGO_URL", raising=False)
    monkeypatch.delenv("DB_NAME", raising=False)
    assert wd.main(["export"]) == 2
    assert "MONGO_URL and DB_NAME" in capsys.readouterr().err


# ---------- real MongoDB ----------

@pytest.fixture
def db():
    if not MONGO:
        pytest.skip("WAITLIST_TEST_MONGO_URL not set")
    from pymongo import MongoClient
    client = MongoClient(MONGO, serverSelectionTimeoutMS=3000)
    name = f"wl015_{uuid.uuid4().hex[:8]}"
    d = client[name]
    t = datetime(2026, 10, 1, tzinfo=timezone.utc)
    d.waitlist.insert_many([
        {"id": "1", "email": "owner@example.com", "name": "Ann", "business_name": "=cmd|' /C calc'!A0",
         "industry": "HVAC", "note": "secret note", "ip": "203.0.113.9", "created_at": "2026-10-01T00:00:00+00:00"},
        {"id": "2", "email": " Owner@Example.com", "name": "Old row", "ip": "203.0.113.10", "created_at": t},
        {"id": "3", "email": "other@example.com", "name": "Bob", "ip": "203.0.113.11", "created_at": "2026-10-02T00:00:00+00:00"},
    ])
    d.waitlist_duplicates.insert_many([
        {"id": "4", "email": "owner@example.com", "email_original": "OWNER@example.com", "duplicate_of": "1"},
        {"id": "5", "email": "x", "email_original": " owner@EXAMPLE.com "},
        {"id": "6", "email": "other@example.com", "duplicate_of": "3"},
    ])
    d.unrelated.insert_one({"email": "owner@example.com"})
    yield d
    client.drop_database(name)
    client.close()


def _out():
    buf = []
    return buf, buf.append


def test_export_dry_run_writes_nothing(db, tmp_path):
    out = tmp_path / "wl.csv"
    log, echo = _out()
    assert wd.run_export(db, str(out), wd.DEFAULT_FIELDS, apply=False, echo=echo) == 0
    assert not out.exists()
    assert log[0] == "export: 3 waitlist row(s); columns: email, name, business_name, industry, created_at"


def test_export_writes_only_chosen_columns_safely(db, tmp_path):
    out = tmp_path / "wl.csv"
    log, echo = _out()
    assert wd.run_export(db, str(out), wd.DEFAULT_FIELDS, apply=True, echo=echo) == 0
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    rows = list(csv.reader(io.StringIO(out.read_text(encoding="utf-8"))))
    assert rows[0] == list(wd.DEFAULT_FIELDS)
    assert len(rows) == 4
    text = out.read_text(encoding="utf-8")
    assert "203.0.113" not in text and "secret note" not in text  # no ip, no note by default
    ann = next(r for r in rows if r[1] == "Ann")
    assert ann[2].startswith("'=")  # formula neutralised
    old = next(r for r in rows if r[1] == "Old row")
    assert old[4] == "2026-10-01T00:00:00+00:00"  # BSON datetime -> ISO
    assert all("@" not in line for line in log)  # counts only, no emails printed
    # Never overwrites.
    assert wd.run_export(db, str(out), wd.DEFAULT_FIELDS, apply=True, echo=echo) == 2


def test_delete_dry_run_counts_and_keeps_everything(db):
    log, echo = _out()
    assert wd.run_delete(db, "OWNER@example.com\n", apply=False, echo=echo) == 0
    assert log[0] == "delete: matches 2 row(s) in waitlist, 2 in waitlist_duplicates"
    assert db.waitlist.count_documents({}) == 3 and db.waitlist_duplicates.count_documents({}) == 3
    assert all("@" not in line for line in log)


def test_delete_apply_removes_all_copies_and_nothing_else(db):
    log, echo = _out()
    assert wd.run_delete(db, " owner@example.com ", apply=True, echo=echo, confirm=lambda _p: "DELETE\n") == 0
    assert sorted(d["id"] for d in db.waitlist.find()) == ["3"]
    assert sorted(d["id"] for d in db.waitlist_duplicates.find()) == ["6"]
    assert db.unrelated.count_documents({}) == 1  # other collections untouched
    assert log[-1] == "deleted 2 from waitlist, 2 from waitlist_duplicates; remaining matches: 0"
    # Idempotent.
    log2, echo2 = _out()
    assert wd.run_delete(db, "owner@example.com", apply=True, echo=echo2, confirm=_never) == 0
    assert log2[0] == "delete: matches 0 row(s) in waitlist, 0 in waitlist_duplicates"


def _never(prompt):
    raise AssertionError("must not ask for confirmation")


def test_delete_rejects_garbage(db):
    log, echo = _out()
    assert wd.run_delete(db, "\n", apply=True, echo=echo) == 2
    assert db.waitlist.count_documents({}) == 3


def test_cli_end_to_end_reads_email_from_stdin(db, monkeypatch, capsys):
    monkeypatch.setenv("MONGO_URL", MONGO)
    monkeypatch.setenv("DB_NAME", db.name)
    monkeypatch.setattr(sys, "stdin", io.StringIO("other@example.com\n"))
    assert wd.main(["delete", "--apply", "--confirm", "DELETE"]) == 0
    out = capsys.readouterr().out
    assert "deleted 1 from waitlist, 1 from waitlist_duplicates" in out
    assert "other@example.com" not in out


# ---------- EMP-WL-075: typed confirmation before --apply deletes ----------
@pytest.mark.parametrize("answer", ["", "\n", "y", "yes", "delete", "DELETE NOW", None])
def test_delete_apply_needs_the_typed_word(db, answer):
    log, echo = _out()
    prompts = []

    def confirm(prompt):
        prompts.append(prompt)
        return answer
    assert wd.run_delete(db, "owner@example.com", apply=True, echo=echo, confirm=confirm) == 3
    assert prompts == ["Type DELETE to delete these row(s): "]
    assert log[0] == "delete: matches 2 row(s) in waitlist, 2 in waitlist_duplicates"  # counts first
    assert log[-1] == "not confirmed: nothing deleted"
    assert db.waitlist.count_documents({}) == 3 and db.waitlist_duplicates.count_documents({}) == 3


def test_delete_dry_run_never_asks(db):
    log, echo = _out()
    assert wd.run_delete(db, "owner@example.com", apply=False, echo=echo, confirm=_never) == 0


def test_cli_apply_without_terminal_or_confirm_deletes_nothing(db, monkeypatch, capsys):
    monkeypatch.setenv("MONGO_URL", MONGO)
    monkeypatch.setenv("DB_NAME", db.name)
    monkeypatch.setattr(sys, "stdin", io.StringIO("other@example.com\n"))
    monkeypatch.setattr(wd, "_confirm_from_tty", lambda prompt: "")  # no terminal
    assert wd.main(["delete", "--apply"]) == 3
    assert "not confirmed" in capsys.readouterr().out
    assert db.waitlist.count_documents({}) == 3


def test_cli_wrong_confirm_value_deletes_nothing(db, monkeypatch):
    monkeypatch.setenv("MONGO_URL", MONGO)
    monkeypatch.setenv("DB_NAME", db.name)
    monkeypatch.setattr(sys, "stdin", io.StringIO("other@example.com\n"))
    assert wd.main(["delete", "--apply", "--confirm", "yes"]) == 3
    assert db.waitlist.count_documents({}) == 3


# ---------- EMP-WL-074: credentials from env or a 0600 file, never backend/.env ----------
def test_never_reads_backend_env(monkeypatch, capsys):
    monkeypatch.delenv("MONGO_URL", raising=False)
    monkeypatch.delenv("DB_NAME", raising=False)
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: pytest.fail("backend/.env must not be loaded"))
    assert wd.main(["export"]) == 2
    assert "backend/.env is never read" in capsys.readouterr().err
    src = (Path(wd.__file__)).read_text()
    assert "load_dotenv" not in src and 'BACKEND / ".env"' not in src


def test_env_file_must_be_private(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("MONGO_URL", raising=False)
    f = tmp_path / "wl.env"
    f.write_text("MONGO_URL=mongodb://127.0.0.1:9\nDB_NAME=x\n")
    f.chmod(0o644)
    assert wd.main(["export", "--env-file", str(f)]) == 2
    assert "0600" in capsys.readouterr().err
    assert wd.main(["export", "--env-file", str(tmp_path / "missing.env")]) == 2


def test_env_file_supplies_the_dedicated_uri(db, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("MONGO_URL", raising=False)
    monkeypatch.delenv("DB_NAME", raising=False)
    f = tmp_path / "wl.env"
    f.write_text(f"MONGO_URL={MONGO}\nDB_NAME={db.name}\n")
    f.chmod(0o600)
    assert wd.main(["export", "--env-file", str(f)]) == 0
    out = capsys.readouterr().out
    assert "export: 3 waitlist row(s)" in out and MONGO not in out


def test_runbook_mongosh_example_keeps_secrets_off_the_command_line():
    doc = (Path(wd.__file__).resolve().parents[2] / "docs" / "ops" / "waitlist-data.md").read_text()
    assert '"$MONGO_URL' not in doc and "$MONGO_URL/" not in doc
    assert "Put it in `backend/.env`" not in doc
    for line in doc.splitlines():
        if line.lstrip().startswith("mongosh "):
            assert "mongodb://" not in line and "mongodb+srv://" not in line and "$" not in line, line


def test_confirmation_is_read_from_a_real_terminal():
    """Regression: a text-mode "r+" open of /dev/tty fails (not seekable), which made every
    interactive confirmation fail. Run the real function under a pseudo-terminal."""
    pty = pytest.importorskip("pty")
    import select
    import time
    script = Path(wd.__file__).resolve()
    pid, fd = pty.fork()
    if pid == 0:  # child
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("wd_child", script)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            got = mod._confirm_from_tty("Type DELETE: ")
            os._exit(0 if got.strip() == "DELETE" else 1)
        except BaseException:
            os._exit(2)
    out, sent, t0 = b"", False, time.time()
    while time.time() - t0 < 10:
        r, _, _ = select.select([fd], [], [], 0.5)
        if r:
            try:
                chunk = os.read(fd, 1024)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
            if not sent and b"Type DELETE" in out:
                os.write(fd, b"DELETE\r")
                sent = True
    _, status = os.waitpid(pid, 0)
    assert sent, out
    assert os.waitstatus_to_exitcode(status) == 0, out
