"""Instant-quote estimator: optional lead fields on PR #11's hardened POST /api/public/waitlist.

Rewritten for the stacked merge with #11 (EMP-W-CF-028): same handler, same limiter, same
honeypot/Turnstile/no-enumeration rules. No network: fake DB, email sender mocked.
"""
from __future__ import annotations

import copy
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "est-test")
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "est")

from routers import marketing as mk  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
WL = "/api/public/waitlist"


def _match(doc, q):
    for k, v in (q or {}).items():
        if isinstance(v, dict) and "$exists" in v:
            if (k in doc) != bool(v["$exists"]):
                return False
        elif isinstance(v, dict) and "$in" in v:  # Mongo: None in $in also matches a missing field
            if doc.get(k) not in v["$in"]:
                return False
        elif doc.get(k) != v:
            return False
    return True


class _Res:
    def __init__(self, modified=0, upserted_id=None):
        self.modified_count, self.upserted_id = modified, upserted_id


class _Cursor:
    def __init__(self, docs):
        self._it = iter(docs)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class FakeWaitlist:
    # find/find_one/delete_one/replace_one are used by the dedupe-before-index step in the stacked
    # follow-ups branch (fix/waitlist-followups), so these tests pass with or without it.
    def __init__(self):
        self.docs = []

    async def create_index(self, *a, **k):
        return None

    def find(self, q=None, projection=None):
        return _Cursor([copy.deepcopy(d) for d in self.docs if _match(d, q)])

    async def find_one(self, q):
        return next((copy.deepcopy(d) for d in self.docs if _match(d, q)), None)

    async def delete_one(self, q):
        for i, d in enumerate(self.docs):
            if _match(d, q):
                del self.docs[i]
                return

    async def replace_one(self, q, doc, upsert=False):
        for i, d in enumerate(self.docs):
            if _match(d, q):
                self.docs[i] = copy.deepcopy(doc)
                return
        if upsert:
            self.docs.append(copy.deepcopy(doc))

    async def update_one(self, q, upd, upsert=False):
        for d in self.docs:
            if _match(d, q):
                if "$set" in upd:
                    d.update(copy.deepcopy(upd["$set"]))
                    return _Res(modified=1)
                return _Res()
        if upsert:
            nd = {k: v for k, v in q.items() if not isinstance(v, dict)}
            nd.update(copy.deepcopy(upd.get("$setOnInsert") or {}))
            self.docs.append(nd)
            return _Res(upserted_id=nd.get("id", "x"))
        return _Res()


class FakeDB:
    def __init__(self):
        self.waitlist = FakeWaitlist()
        self.waitlist_duplicates = FakeWaitlist()

    def __getitem__(self, name):
        return getattr(self, name)


@pytest.fixture
def client(monkeypatch):
    db = FakeDB()
    sent = []

    async def fake_send(email):
        sent.append(email)

    monkeypatch.setattr(mk, "get_db", lambda: db)
    monkeypatch.setattr(mk, "_send_waitlist_confirmation", fake_send)
    monkeypatch.setenv("WAITLIST_CONFIRMATION_EMAIL", "true")  # WL-008: opt in for these email tests
    monkeypatch.setattr(mk, "_WAITLIST_INDEX_READY", False)
    monkeypatch.delenv("TURNSTILE_ENABLED", raising=False)
    for lim in (mk._DEMO_LIMIT, mk._WAITLIST_IP_MINUTE, mk._WAITLIST_IP_HOUR):
        lim.reset()
    app = FastAPI()
    app.include_router(mk.router, prefix="/api")
    return TestClient(app), db, sent


def _est(**kw):
    b = {"email": "a@example.com", "industry": "HVAC", "note": "estimator; tier=growth",
         "source_detail": "estimator", "estimated_tier": "growth", "website": ""}
    b.update(kw)
    return b


def test_estimator_lead_gets_single_website_form_source(client):
    c, db, sent = client
    r = c.post(WL, json=_est())
    assert r.status_code == 200 and r.json() == {"ok": True, "status": "received"}
    d = db.waitlist.docs[0]
    assert (d["source"], d["source_detail"], d["estimated_tier"]) == ("website form", "estimator", "growth")
    assert sent == ["a@example.com"]  # same single waitlist confirmation as any new signup


def test_landing_signup_unchanged(client):
    c, db, _ = client
    assert c.post(WL, json={"email": "b@example.com"}).status_code == 200
    d = db.waitlist.docs[0]
    assert d["source"] == "landing" and d["source_detail"] == "" and d["estimated_tier"] == ""


@pytest.mark.parametrize("body", [
    {"source_detail": "ads"},                 # only "estimator" allowed (no new sources)
    {"estimated_tier": "x" * 41},
    {"estimated_tier": "Growth <script>"},
])
def test_invalid_estimator_fields_422(client, body):
    c, db, _ = client
    assert c.post(WL, json=_est(**body)).status_code == 422
    assert db.waitlist.docs == []


def test_client_cannot_set_source(client):
    c, db, _ = client
    assert c.post(WL, json={"email": "c@example.com", "source": "estimator"}).status_code == 200
    assert db.waitlist.docs[0]["source"] == "landing"


def test_repeat_signup_never_overwrites_existing_values(client):
    """EMP-W-CF-031 (replaces #12's 'refresh the estimate' rule): same response, no resend, and the
    first signup's values stay. Someone who knows the address can't change that person's row."""
    c, db, sent = client
    r1 = c.post(WL, json=_est())
    r2 = c.post(WL, json=_est(email="A@Example.com", estimated_tier="ai_office", note="estimator; tier=ai_office",
                              name="Changed"))
    assert r1.status_code == r2.status_code == 200 and r1.content == r2.content
    assert len(db.waitlist.docs) == 1
    d = db.waitlist.docs[0]
    assert d["estimated_tier"] == "growth" and d["note"] == "estimator; tier=growth"
    assert d["name"] == "" and d["source"] == "website form" and d["source_detail"] == "estimator"
    assert "estimator_detail" not in d and "estimator_updated_at" not in d
    assert sent == ["a@example.com"]  # no second email


def test_repeat_signup_only_fills_blanks(client):
    c, db, sent = client
    c.post(WL, json={"email": "a@example.com"})  # landing signup: no tier, no detail, no note
    c.post(WL, json=_est(email="a@example.com", estimated_tier="growth", note="estimator; tier=growth"))
    d = db.waitlist.docs[0]
    assert (d["estimated_tier"], d["source_detail"], d["note"]) == ("growth", "estimator", "estimator; tier=growth")
    assert d["source"] == "landing"  # the lead's source is never rewritten
    c.post(WL, json=_est(email="a@example.com", estimated_tier="high_volume", note="other"))
    d = db.waitlist.docs[0]
    assert (d["estimated_tier"], d["note"]) == ("growth", "estimator; tier=growth")
    assert sent == ["a@example.com"]


def test_repeat_signup_fills_missing_legacy_fields(client):
    c, db, sent = client
    db.waitlist.docs.append({"id": "old", "email": "legacy@example.com"})  # pre-#12 row: fields absent
    c.post(WL, json=_est(email="legacy@example.com"))
    d = db.waitlist.docs[0]
    assert d["estimated_tier"] == "growth" and d["source_detail"] == "estimator"
    assert sent == []


def test_repeat_landing_signup_does_not_touch_estimate(client):
    c, db, _ = client
    c.post(WL, json=_est())
    c.post(WL, json={"email": "a@example.com", "note": "landing again"})
    d = db.waitlist.docs[0]
    assert d["estimated_tier"] == "growth" and d["note"] == "estimator; tier=growth"


@pytest.mark.parametrize("raw,stored", [
    ("<script>alert(1)</script>Call me", "alert(1)Call me"),
    ("<img src=x onerror=alert(1)>hi", "hi"),
    ("a < b > c", "a  b  c"),
    ("line1\nline2\tok\x00\x07", "line1\nline2\tok"),
    ("plain note; tier=growth", "plain note; tier=growth"),
])
def test_note_is_stored_as_plain_text(client, raw, stored):
    c, db, _ = client
    assert c.post(WL, json=_est(note=raw)).status_code == 200
    assert db.waitlist.docs[0]["note"] == stored
    assert "<" not in db.waitlist.docs[0]["note"]


def test_other_text_fields_sanitized_and_repeat_script_note_not_stored(client):
    c, db, _ = client
    c.post(WL, json={"email": "x@example.com", "name": "<b>Pat</b>", "business_name": "<i>Pat's</i> HVAC",
                     "industry": "<script>x</script>HVAC"})
    d = db.waitlist.docs[0]
    assert (d["name"], d["business_name"], d["industry"]) == ("Pat", "Pat's HVAC", "xHVAC")
    c.post(WL, json={"email": "x@example.com", "note": "<script>steal()</script>"})
    assert db.waitlist.docs[0]["note"] == "steal()"  # was blank, so filled, but as plain text


def test_no_frontend_renders_waitlist_rows_as_html():
    # No admin/export view reads the waitlist today. If one is added, it must escape on output
    # (React text nodes do; dangerouslySetInnerHTML or raw CSV/HTML exports must not be used).
    src = Path(__file__).resolve().parents[2] / "frontend" / "src"
    offenders = [p for p in src.rglob("*.js*") if "dangerouslySetInnerHTML" in p.read_text(errors="ignore")
                 and "waitlist" in p.read_text(errors="ignore").lower()]
    assert offenders == []


def test_estimator_honeypot_and_hardening_apply(client):
    c, db, sent = client
    bot = c.post(WL, json=_est(website="http://spam.example"))
    assert bot.status_code == 200 and bot.json() == {"ok": True, "status": "received"}
    assert db.waitlist.docs == [] and sent == []
    assert c.post(WL, json=_est(note="x" * 2001)).status_code == 422  # #11's field caps


def test_estimator_turnstile_token_is_checked(client, monkeypatch):
    c, db, _ = client
    monkeypatch.setenv("TURNSTILE_ENABLED", "true")

    async def fake_verify(token, ip):
        return token == "good"

    monkeypatch.setattr(mk, "verify_turnstile", fake_verify)
    assert c.post(WL, json=_est()).status_code == 400
    assert c.post(WL, json=_est(turnstile_token="good")).status_code == 200
    assert db.waitlist.docs[0]["source_detail"] == "estimator"


def test_estimator_shares_waitlist_limiter(client):
    c, _, _ = client
    codes = [c.post(WL, json=_est(email=f"e{i}@example.com")).status_code for i in range(6)]
    assert codes == [200] * 5 + [429]


# ---------- exactly one waitlist route (EMP-W-CF-028) ----------
def _waitlist_routes(routes):
    return [r for r in routes if isinstance(r, APIRoute) and r.path.endswith("/waitlist")]


def test_marketing_router_has_exactly_one_waitlist_route():
    found = _waitlist_routes(mk.router.routes)
    assert len(found) == 1, [(r.path, r.endpoint.__name__) for r in found]
    assert found[0].methods == {"POST"} and found[0].endpoint is mk.waitlist


def test_server_app_has_exactly_one_waitlist_route_and_it_is_hardened():
    # Fresh interpreter: server.py builds the real app at import. No DB connection is made.
    code = (
        "import server, json\n"
        "from fastapi.routing import APIRoute\n"
        "rs=[r for r in server.app.routes if isinstance(r, APIRoute) and 'waitlist' in r.path]\n"
        "print(json.dumps([[r.path, sorted(r.methods), r.endpoint.__module__, r.endpoint.__name__] for r in rs]))\n"
    )
    env = {**os.environ, "JWT_SECRET": "x", "MONGO_URL": "mongodb://localhost:1", "DB_NAME": "t"}
    out = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    import json
    routes = json.loads(out.stdout.strip().splitlines()[-1])
    assert routes == [["/api/public/waitlist", ["POST"], "routers.marketing", "waitlist"]]
