"""Fix #2 — no default admin password; forced strong password at first login.

In-memory fake Mongo (no network). Real bcrypt + real JWT.
"""
from __future__ import annotations

import asyncio
import copy
import os

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "fix002-test-secret")
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "fix002")

import security  # noqa: E402
import seed_data  # noqa: E402
from routers import auth as auth_router  # noqa: E402
from password_policy import password_problems, is_strong_password  # noqa: E402

STRONG = "Tr0ub4dor&Horse!"


# ---------------- fake db ----------------
def _bson(value):
    """Store values like MongoDB does: aware datetimes come back NAIVE UTC (pymongo default,
    tz_aware=False), millisecond precision. EMP-W-CF-026: the old fake kept them aware, which hid
    the reset-password 500."""
    from datetime import datetime, timezone
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value.replace(microsecond=value.microsecond // 1000 * 1000)
    if isinstance(value, dict):
        return {k: _bson(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_bson(v) for v in value]
    return value


def _match(doc, q):
    return all(doc.get(k) == v for k, v in (q or {}).items())


class FakeColl:
    def __init__(self):
        self.docs = []

    async def find_one(self, q=None, proj=None):
        for d in self.docs:
            if _match(d, q):
                out = copy.deepcopy(d)
                if proj:
                    for k, v in proj.items():
                        if v == 0:
                            out.pop(k, None)
                return out
        return None

    def find(self, q=None, proj=None):
        docs = [copy.deepcopy(d) for d in self.docs if _match(d, q)]

        class _Cur:
            async def to_list(self, n=None):
                return docs[: n] if n else docs
        return _Cur()

    async def insert_one(self, doc):
        self.docs.append(_bson(copy.deepcopy(doc)))

    async def update_one(self, q, upd, upsert=False):
        for d in self.docs:
            if _match(d, q):
                d.update(_bson((upd or {}).get("$set") or {}))
                return
        if upsert:
            nd = dict(q); nd.update((upd or {}).get("$set") or {}); self.docs.append(nd)

    async def delete_one(self, q):
        self.docs = [d for d in self.docs if not _match(d, q)]

    async def delete_many(self, q):
        self.docs = [d for d in self.docs if not _match(d, q)]


class FakeDB:
    def __init__(self):
        self._c = {}

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self._c.setdefault(name, FakeColl())


@pytest.fixture
def db(monkeypatch):
    d = FakeDB()
    for mod in (security, seed_data, auth_router):
        monkeypatch.setattr(mod, "get_db", lambda d=d: d)
    monkeypatch.setenv("ADMIN_EMAIL", "ops-admin@example.com")
    return d


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


# ---------------- policy ----------------
def test_policy_rejects_weak_and_default():
    assert password_problems("short1!A")
    assert password_problems("alllowercase123!")
    assert password_problems("NoDigitsHere!!!")
    assert password_problems("NoSymbol123456A")
    assert password_problems("AdminPass123!")  # old default — 13 chars, all classes, still rejected
    assert is_strong_password(STRONG)


# ---------------- seeding ----------------
def test_seed_twice_does_not_change_existing_hash(db, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", STRONG)
    run(seed_data.seed_admin())
    admin = db.users.docs[0]
    h1 = admin["password_hash"]
    assert admin["role"] == "platform_admin"
    assert admin["must_change_password"] is True
    # Restart with a DIFFERENT env password — must not overwrite.
    monkeypatch.setenv("ADMIN_PASSWORD", "An0ther!StrongPass")
    run(seed_data.seed_admin())
    run(seed_data.seed_admin())
    assert len(db.users.docs) == 1
    assert db.users.docs[0]["password_hash"] == h1


def test_unset_admin_email_skips_seed_and_warns(db, monkeypatch, caplog):
    import logging
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    monkeypatch.setenv("ADMIN_PASSWORD", STRONG)
    with caplog.at_level(logging.WARNING, logger="seed"):
        run(seed_data.seed_admin())
    assert db.users.docs == []
    assert any("ADMIN_EMAIL is not set" in r.message for r in caplog.records)


def test_admin_email_from_env_and_no_personal_default(db, monkeypatch):
    from pathlib import Path
    monkeypatch.setenv("ADMIN_EMAIL", "Ops-Admin@Example.com")
    monkeypatch.setenv("ADMIN_PASSWORD", STRONG)
    run(seed_data.seed_admin())
    assert db.users.docs[0]["email"] == "ops-admin@example.com"
    src = Path(seed_data.__file__).read_text()
    assert "@gmail.com" not in src and "ramonajefferson" not in src


def test_existing_admin_email_never_changed(db, monkeypatch):
    run(db.users.insert_one({"id": "a0", "email": "legacy@old.example", "role": "platform_admin",
                             "password_hash": security.hash_password(STRONG)}))
    monkeypatch.setenv("ADMIN_EMAIL", "new@example.com")
    run(seed_data.seed_admin())
    assert len(db.users.docs) == 1 and db.users.docs[0]["email"] == "legacy@old.example"
    assert security.verify_password(STRONG, db.users.docs[0]["password_hash"])  # strong hash untouched


def test_no_default_credentials_in_non_test_source():
    from pathlib import Path
    root = Path(seed_data.__file__).resolve().parent
    for rel in ("seed_data.py", "routers/source_export.py", "routers/auth.py", "server.py"):
        txt = (root / rel).read_text()
        assert "AdminPass123!" not in txt, rel
        assert "ramonajefferson" not in txt, rel


@pytest.mark.parametrize("weak", ["AdminPass123!", "password", "Short1!"])
def test_weak_env_password_rejected(db, monkeypatch, weak):
    monkeypatch.setenv("ADMIN_PASSWORD", weak)
    run(seed_data.seed_admin())
    admin = db.users.docs[0]
    assert not security.verify_password(weak, admin["password_hash"])
    assert admin["must_change_password"] is True


def test_unset_env_password_unusable(db, monkeypatch):
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    run(seed_data.seed_admin())
    admin = db.users.docs[0]
    assert admin["must_change_password"] is True
    assert admin["password_unusable"] is True
    assert not security.verify_password("AdminPass123!", admin["password_hash"])
    assert not security.verify_password("", admin["password_hash"])


@pytest.mark.parametrize("already_flagged", [False, True])
def test_legacy_default_admin_hash_replaced(db, monkeypatch, already_flagged):
    """Existing DB with the old default: hash is replaced with an unusable one (even if an
    earlier build of this PR had already set must_change_password)."""
    legacy_hash = security.hash_password("AdminPass123!")
    run(db.users.insert_one({"id": "a1", "email": "x@y.z", "role": "platform_admin",
                             "password_hash": legacy_hash, "must_change_password": already_flagged}))
    monkeypatch.setenv("ADMIN_PASSWORD", STRONG)
    run(seed_data.seed_admin())
    adm = db.users.docs[0]
    assert len(db.users.docs) == 1
    assert adm["password_hash"] != legacy_hash
    assert not security.verify_password("AdminPass123!", adm["password_hash"])
    assert not security.verify_password(STRONG, adm["password_hash"])  # env pwd not applied to existing admin
    assert adm["must_change_password"] is True and adm["password_unusable"] is True
    assert adm["email"] == "x@y.z"


def test_no_hardcoded_default_password_used_for_seed():
    from pathlib import Path
    src = Path(seed_data.__file__).read_text()
    assert 'os.environ.get("ADMIN_PASSWORD", "AdminPass123!")' not in src


# ---------------- API enforcement ----------------
@pytest.fixture
def client(db):
    app = FastAPI()
    app.include_router(auth_router.router, prefix="/api")

    @app.get("/api/admin/stats")
    async def admin_stats(user: dict = Depends(security.require_platform_admin)):
        return {"ok": True}

    return TestClient(app)


def _seed_flagged_admin(db):
    run(db.users.insert_one({
        "id": "adm", "email": "admin@example.com", "role": "platform_admin", "tenant_id": None,
        "password_hash": security.hash_password("Initial!Pass1234"), "must_change_password": True,
    }))
    return {"Authorization": "Bearer " + security.create_access_token("adm", "admin@example.com", "platform_admin", None)}


def test_login_returns_flag(client, db):
    _seed_flagged_admin(db)
    r = client.post("/api/auth/login", json={"email": "admin@example.com", "password": "Initial!Pass1234"})
    assert r.status_code == 200, r.text
    assert r.json()["must_change_password"] is True
    assert "password_hash" not in r.json()


def test_flag_blocks_admin_endpoints_but_allows_me(client, db):
    h = _seed_flagged_admin(db)
    assert client.get("/api/admin/stats", headers=h).status_code == 403
    assert client.get("/api/auth/me", headers=h).status_code == 200


def test_set_password_enforces_strength_and_clears_flag(client, db):
    h = _seed_flagged_admin(db)
    cur = "Initial!Pass1234"
    weak = client.post("/api/auth/set-password", json={"new_password": "AdminPass123!", "current_password": cur}, headers=h)
    assert weak.status_code == 400
    assert db.users.docs[0]["must_change_password"] is True
    ok = client.post("/api/auth/set-password", json={"new_password": STRONG, "current_password": cur}, headers=h)
    assert ok.status_code == 200, ok.text
    assert db.users.docs[0]["must_change_password"] is False
    assert security.verify_password(STRONG, db.users.docs[0]["password_hash"])
    assert client.get("/api/admin/stats", headers=h).status_code == 200


def test_normal_change_requires_current_password(client, db):
    run(db.users.insert_one({
        "id": "u2", "email": "o@example.com", "role": "owner", "tenant_id": "t1",
        "password_hash": security.hash_password("Old!Password1234"), "must_change_password": False,
    }))
    h = {"Authorization": "Bearer " + security.create_access_token("u2", "o@example.com", "owner", "t1")}
    assert client.post("/api/auth/set-password", json={"new_password": STRONG}, headers=h).status_code == 400
    r = client.post("/api/auth/set-password", json={"new_password": STRONG, "current_password": "Old!Password1234"}, headers=h)
    assert r.status_code == 200


def test_google_session_cookie_path_still_blocked(client, db):
    """Google sign-in authenticates via session_token cookie; must_change still applies."""
    from datetime import datetime, timedelta, timezone
    _seed_flagged_admin(db)
    run(db.user_sessions.insert_one({"user_id": "adm", "session_token": "gsess",
                                     "expires_at": datetime.now(timezone.utc) + timedelta(days=1)}))
    client.cookies.set("session_token", "gsess")
    assert client.get("/api/admin/stats").status_code == 403
    assert client.get("/api/auth/me").status_code == 200


def test_forced_change_still_requires_current_password(client, db):
    h = _seed_flagged_admin(db)
    assert client.post("/api/auth/set-password", json={"new_password": STRONG}, headers=h).status_code == 400
    assert client.post("/api/auth/set-password", json={"new_password": STRONG, "current_password": "Wrong!Pass12345"},
                       headers=h).status_code == 400
    assert db.users.docs[0]["must_change_password"] is True
    assert client.get("/api/admin/stats", headers=h).status_code == 403


def test_unusable_password_must_use_reset_flow(client, db, monkeypatch):
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    run(seed_data.seed_admin())
    adm = db.users.docs[0]
    h = {"Authorization": "Bearer " + security.create_access_token(adm["id"], adm["email"], "platform_admin", None)}
    r = client.post("/api/auth/set-password", json={"new_password": STRONG, "current_password": "anything"}, headers=h)
    assert r.status_code == 403 and "Forgot password" in r.json()["detail"]
    assert client.get("/api/admin/stats", headers=h).status_code == 403
    # Reset-token flow works and clears both flags.
    from datetime import datetime, timedelta, timezone
    # EMP-W-CF-026: a real datetime, stored like MongoDB stores it (comes back naive UTC).
    run(db.password_reset_tokens.insert_one({"token": "rt1", "user_id": adm["id"], "used": False,
                                             "expires_at": datetime.now(timezone.utc) + timedelta(hours=1)}))
    assert db.password_reset_tokens.docs[0]["expires_at"].tzinfo is None
    assert client.post("/api/auth/reset-password", json={"token": "rt1", "new_password": STRONG}).status_code == 200
    adm = db.users.docs[0]
    assert adm["password_unusable"] is False and adm["must_change_password"] is False
    assert client.post("/api/auth/login", json={"email": adm["email"], "password": STRONG}).status_code == 200


def _legacy_admin(db, flagged=False):
    run(db.users.insert_one({"id": "legacy", "email": "admin@legacy.example", "role": "platform_admin",
                             "tenant_id": None, "password_hash": security.hash_password("AdminPass123!"),
                             "must_change_password": flagged}))


def test_watcher_exploit_after_startup_seed_fails(client, db):
    """Watcher's offline exploit on the base: login 200 -> set-password 200 -> admin API 200.
    With startup seeding run, the very first step now fails."""
    _legacy_admin(db, flagged=True)
    run(seed_data.seed_admin())  # startup
    r = client.post("/api/auth/login", json={"email": "admin@legacy.example", "password": "AdminPass123!"})
    assert r.status_code == 401
    # Even with a token obtained earlier (e.g. before restart), nothing works.
    h = {"Authorization": "Bearer " + security.create_access_token("legacy", "admin@legacy.example", "platform_admin", None)}
    for body in ({"new_password": STRONG}, {"new_password": STRONG, "current_password": "AdminPass123!"}):
        assert client.post("/api/auth/set-password", json=body, headers=h).status_code in (400, 403)
    assert client.get("/api/admin/stats", headers=h).status_code == 403


@pytest.mark.parametrize("flagged", [False, True])
def test_watcher_exploit_without_startup_seed_fails(client, db, flagged):
    """Defense in depth: even if the startup seed never ran, the known default cannot yield admin."""
    _legacy_admin(db, flagged=flagged)
    r = client.post("/api/auth/login", json={"email": "admin@legacy.example", "password": "AdminPass123!"})
    assert r.status_code == 401
    adm = db.users.docs[0]
    assert adm["password_unusable"] is True and not security.verify_password("AdminPass123!", adm["password_hash"])
    h = {"Authorization": "Bearer " + security.create_access_token("legacy", "admin@legacy.example", "platform_admin", None)}
    assert client.post("/api/auth/set-password", json={"new_password": STRONG, "current_password": "AdminPass123!"},
                       headers=h).status_code in (400, 403)
    assert client.get("/api/admin/stats", headers=h).status_code == 403


def test_set_password_refuses_known_default_current_for_admin_with_live_token(client, db):
    """Token issued before the fix + hash still default (seed not run): set-password must refuse."""
    _legacy_admin(db, flagged=True)
    h = {"Authorization": "Bearer " + security.create_access_token("legacy", "admin@legacy.example", "platform_admin", None)}
    r = client.post("/api/auth/set-password", json={"new_password": STRONG, "current_password": "AdminPass123!"}, headers=h)
    assert r.status_code == 403
    assert client.get("/api/admin/stats", headers=h).status_code == 403


# ---------------- EMP-W-CF-026: reset-password with real datetimes ----------------
def _reset_token(db, token, expires_at, user_id="adm"):
    run(db.password_reset_tokens.insert_one({"token": token, "user_id": user_id, "used": False,
                                             **({} if expires_at is None else {"expires_at": expires_at})}))


@pytest.mark.parametrize("stored", ["aware", "naive", "iso", "iso_z"])
def test_reset_password_accepts_every_stored_expiry_form(client, db, stored):
    from datetime import datetime, timedelta, timezone
    _seed_flagged_admin(db)
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    value = {"aware": future, "naive": future.replace(tzinfo=None), "iso": future.isoformat(),
             "iso_z": future.replace(tzinfo=None).isoformat() + "Z"}[stored]
    _reset_token(db, "tok-" + stored, value)
    r = client.post("/api/auth/reset-password", json={"token": "tok-" + stored, "new_password": STRONG})
    assert r.status_code == 200, r.text
    assert client.post("/api/auth/reset-password", json={"token": "tok-" + stored, "new_password": STRONG}).status_code == 400


@pytest.mark.parametrize("value", ["past", "missing", "garbage"])
def test_reset_password_expired_or_bad_expiry_is_400_not_500(client, db, value):
    from datetime import datetime, timedelta, timezone
    _seed_flagged_admin(db)
    v = {"past": datetime.now(timezone.utc) - timedelta(minutes=1), "missing": None, "garbage": "not-a-date"}[value]
    _reset_token(db, "old", v)
    r = client.post("/api/auth/reset-password", json={"token": "old", "new_password": STRONG})
    assert r.status_code == 400 and r.json()["detail"] == "Token expired"


def test_forgot_then_reset_end_to_end(client, db):
    _seed_flagged_admin(db)
    adm = db.users.docs[0]
    assert client.post("/api/auth/forgot-password", json={"email": adm["email"]}).status_code == 200
    tok = db.password_reset_tokens.docs[-1]
    assert tok["expires_at"].tzinfo is None  # as MongoDB returns it
    r = client.post("/api/auth/reset-password", json={"token": tok["token"], "new_password": STRONG})
    assert r.status_code == 200, r.text
    assert client.post("/api/auth/login", json={"email": adm["email"], "password": STRONG}).status_code == 200


def test_session_cookie_with_naive_expiry(client, db):
    from datetime import datetime, timedelta, timezone
    run(db.users.insert_one({"id": "u9", "email": "s@example.com", "role": "owner", "tenant_id": "t1",
                             "password_hash": security.hash_password(STRONG), "must_change_password": False}))
    run(db.user_sessions.insert_one({"user_id": "u9", "session_token": "live",
                                     "expires_at": datetime.now(timezone.utc) + timedelta(days=1)}))
    run(db.user_sessions.insert_one({"user_id": "u9", "session_token": "dead",
                                     "expires_at": datetime.now(timezone.utc) - timedelta(days=1)}))
    client.cookies.set("session_token", "live")
    assert client.get("/api/auth/me").status_code == 200
    client.cookies.set("session_token", "dead")
    assert client.get("/api/auth/me").status_code == 401


def test_as_utc_helper():
    from datetime import datetime, timedelta, timezone
    from timeutil import as_utc, is_expired
    aware = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)
    assert as_utc(aware.replace(tzinfo=None)) == aware
    assert as_utc(aware.astimezone(timezone(timedelta(hours=-5)))) == aware
    assert as_utc("2026-10-08T12:00:00Z") == aware and as_utc("2026-10-08T12:00:00") == aware
    assert as_utc("nope") is None and as_utc(None) is None and as_utc(123) is None
    assert is_expired(None) and is_expired("garbage")
    assert not is_expired(datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=5))


# ---------------- EMP-W-CF-027: ADMIN_EMAIL clashes with an existing user ----------------
@pytest.mark.parametrize("role", ["owner", "staff"])
def test_seed_admin_email_clash_skips_and_leaves_user_alone(db, monkeypatch, caplog, role):
    monkeypatch.setenv("ADMIN_PASSWORD", STRONG)
    monkeypatch.setenv("ADMIN_EMAIL", "Taken@Example.com ")
    user = {"id": "u-taken", "email": "taken@example.com", "role": role, "tenant_id": "t1",
            "password_hash": security.hash_password("Their!Own!Pass123"), "must_change_password": False}
    run(db.users.insert_one(user))
    before = copy.deepcopy(db.users.docs)
    with caplog.at_level("WARNING", logger="seed"):
        run(seed_data.seed_admin())
    assert db.users.docs == before  # not promoted, not modified, no second user
    assert not any(d.get("role") == "platform_admin" for d in db.users.docs)
    msg = " ".join(r.getMessage() for r in caplog.records)
    assert "ADMIN_EMAIL matches an existing" in msg and "u-taken" in msg
    assert "taken@example.com" not in msg.lower()  # id only, no address in the log


def test_run_all_seeds_continues_after_admin_seed_problem(db, monkeypatch):
    calls = []

    async def boom():
        raise RuntimeError("admin seed exploded")

    async def rec(name):
        calls.append(name)

    async def noop():
        return None

    monkeypatch.setattr(seed_data, "ensure_indexes", noop)
    monkeypatch.setattr(seed_data, "seed_admin", boom)
    for name in ("seed_industries", "seed_countries", "seed_flags"):
        monkeypatch.setattr(seed_data, name, lambda n=name: rec(n))
    run(seed_data.run_all_seeds())
    assert calls == ["seed_industries", "seed_countries", "seed_flags"]
