"""EMP-WL-002 — WAITLIST_ONLY mode serves only /api/health and POST /api/public/waitlist.

Each mode runs in a clean subprocess (server.py reads WAITLIST_ONLY at import). Fake DB, no network.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]

_PROBE = r'''
import json, sys
calls = {"seed": 0, "plans": 0}

def _match(d, q):
    for k, v in (q or {}).items():
        if isinstance(v, dict) and "$exists" in v:
            if (k in d) != bool(v["$exists"]): return False
        elif d.get(k) != v: return False
    return True

class Res:
    def __init__(self, modified=0, upserted_id=None):
        self.modified_count, self.upserted_id, self.matched_count = modified, upserted_id, modified

class Coll:
    # Covers the Cloudflare-2 waitlist handler (find_one/insert_one) AND PR #11's hardened one
    # (update_one upsert + $exists claim) and the follow-ups' dedupe scan (async-iterable find,
    # delete_one/replace_one), so these tests pass on #13 alone and stacked with #10/#11 (WL-030).
    def __init__(self): self.docs = []
    async def find_one(self, q=None, *a, **k):
        return next((dict(d) for d in self.docs if _match(d, q)), None)
    async def insert_one(self, d): self.docs.append(dict(d))
    async def create_index(self, *a, **k): return None
    async def count_documents(self, q=None, *a, **k): return sum(1 for d in self.docs if _match(d, q))
    async def update_one(self, q, upd, upsert=False, **k):
        for d in self.docs:
            if _match(d, q):
                if "$set" in upd:
                    d.update(upd["$set"]); return Res(modified=1)
                return Res()
        if upsert:
            nd = {x: y for x, y in (q or {}).items() if not isinstance(y, dict)}
            nd.update(upd.get("$setOnInsert") or {}); nd.update(upd.get("$set") or {})
            self.docs.append(nd); return Res(upserted_id=nd.get("id", "x"))
        return Res()
    async def delete_one(self, q):
        for i, d in enumerate(self.docs):
            if _match(d, q): del self.docs[i]; return
    async def replace_one(self, q, doc, upsert=False):
        for i, d in enumerate(self.docs):
            if _match(d, q): self.docs[i] = dict(doc); return
        if upsert: self.docs.append(dict(doc))
    def find(self, q=None, *a, **k):
        docs = [dict(d) for d in self.docs if _match(d, q)]
        class Cur:
            def sort(self, *a, **k): return self
            def limit(self, *a, **k): return self
            async def to_list(self, n=None): return docs
            def __aiter__(self):
                self._it = iter(docs); return self
            async def __anext__(self):
                try: return next(self._it)
                except StopIteration: raise StopAsyncIteration
        return Cur()

class FakeDB:
    def __init__(self): self.waitlist = Coll()
    def __getitem__(self, n): return getattr(self, n)
    async def command(self, *a, **k): return {"ok": 1}
    def __getattr__(self, n):
        if n.startswith("_"): raise AttributeError(n)
        c = Coll(); setattr(self, n, c); return c

FAKE = FakeDB()
import db
db.get_db = lambda: FAKE
async def _noop(): return None
db.close_db = _noop
import seed_data
async def _seed(): calls["seed"] += 1
seed_data.run_all_seeds = _seed
import routers.plans as plans
async def _plans(): calls["plans"] += 1
plans.seed_plans = _plans
import services.email as em
async def _mail(**k): return None
em.send_email = _mail

import server
from fastapi.testclient import TestClient
from fastapi.routing import APIRoute
from starlette.routing import Mount
out = {"startup_handlers": len(server.app.router.on_startup)}
routes = []
for r in server.app.routes:
    if isinstance(r, APIRoute):
        for m in sorted(r.methods): routes.append(m + " " + r.path)
    elif isinstance(r, Mount):
        routes.append("GET " + r.path + "/x")
    elif getattr(r, "path", None):
        routes.append("GET " + r.path)
out["routes"] = sorted(set(routes))
import os
paths = json.loads(os.environ["PROBE_PATHS"])
with TestClient(server.app, raise_server_exceptions=False) as c:   # runs startup/shutdown hooks
    out["health"] = [c.get("/api/health").status_code, c.get("/api/health").json()]
    h = c.head("/api/health")
    out["health_head"] = [h.status_code, h.content.decode()]
    r = c.post("/api/public/waitlist", json={"email": "wl@example.com"})
    out["waitlist"] = [r.status_code, len(FAKE.waitlist.docs)]
    g = {}
    body = '{"email": "xsite@example.com"}'
    for name, hdrs in {
        "text_plain": {"Content-Type": "text/plain"},
        "form": {"Content-Type": "application/x-www-form-urlencoded"},
        "multipart": {"Content-Type": "multipart/form-data; boundary=x"},
        "none": {},
        "json_charset": {"Content-Type": "application/json; charset=utf-8"},
        "json_good_origin": {"Content-Type": "application/json", "Origin": "https://good.example"},
        "json_evil_origin": {"Content-Type": "application/json", "Origin": "https://evil.example"},
        "json_null_origin": {"Content-Type": "application/json", "Origin": "null"},
    }.items():
        before = len(FAKE.waitlist.docs)
        rr = c.post("/api/public/waitlist", content=body.replace("xsite", name.replace("_", "")), headers=hdrs)
        g[name] = [rr.status_code, len(FAKE.waitlist.docs) - before]
    out["guard"] = g
    res = {}
    for spec in paths:
        method, path = spec.split(" ", 1)
        res[spec] = c.request(method, path, json={} if method in ("POST", "PUT", "PATCH") else None).status_code
    out["paths"] = res
out["calls"] = calls
print("RESULT " + json.dumps(out))
'''

BLOCKED = [
    "GET /docs", "GET /redoc", "GET /openapi.json", "GET /api/",
    "POST /api/auth/login", "POST /api/auth/register", "GET /api/auth/me", "POST /api/auth/forgot-password",
    "GET /api/admin/plans", "GET /api/admin/tenants", "GET /api/admin/overview",
    "POST /api/public/demo/start", "POST /api/public/demo/turn",
    "GET /api/plans", "GET /api/industries",                 # used by the /estimate page (PR #12)
    "GET /api/health/deployment",
    "POST /api/cron/followups", "POST /api/cron/overdue-reminders",
    "POST /api/stripe/webhook", "POST /api/stripe/connect-webhook",
    "POST /api/twilio/voice", "POST /api/twilio/sms",
    "GET /api/tenants/me",
]


def _concrete(spec):
    method, path = spec.split(" ", 1)
    return method + " " + re.sub(r"\{[^}]+\}", "x", path)


def _run(waitlist_only: bool, paths, cors_origins: str | None = None):
    env = {k: v for k, v in os.environ.items()
           if k not in ("WAITLIST_ONLY", "CORS_ORIGINS", "TURNSTILE_ENABLED", "APP_ENV")}
    env.update({"JWT_SECRET": "x", "MONGO_URL": "mongodb://localhost:1", "DB_NAME": "x",
                "PROBE_PATHS": json.dumps(paths)})
    if waitlist_only:
        env["WAITLIST_ONLY"] = "true"
    if cors_origins is not None:
        env["CORS_ORIGINS"] = cors_origins
    p = subprocess.run([sys.executable, "-c", _PROBE], cwd=BACKEND, env=env,
                       capture_output=True, text=True, timeout=180)
    assert p.returncode == 0, p.stderr[-3000:]
    line = [l for l in p.stdout.splitlines() if l.startswith("RESULT ")][-1]
    return json.loads(line[len("RESULT "):])


LIVE = {"GET /api/health", "HEAD /api/health", "POST /api/public/waitlist"}


@pytest.fixture(scope="module")
def full():
    return _run(False, BLOCKED)


@pytest.fixture(scope="module")
def wl(full):
    # Probe EVERY route of the full app (not just a sample), plus the curated list.
    every = sorted({_concrete(r) for r in full["routes"]} | set(BLOCKED))
    return _run(True, every)


@pytest.fixture(scope="module")
def wl_allowlist():
    return _run(True, [], cors_origins="https://good.example, https://other.example/")


@pytest.fixture(scope="module")
def full_allowlist():
    return _run(False, [], cors_origins="https://good.example")


def test_health_ok_minimal(wl):
    assert wl["health"] == [200, {"status": "ok"}]


def test_waitlist_post_works(wl):
    assert wl["waitlist"] == [200, 1]


@pytest.mark.parametrize("spec", BLOCKED)
def test_everything_else_404(wl, spec):
    assert wl["paths"][spec] == 404, (spec, wl["paths"][spec])


def test_no_seeding_or_startup_hooks(wl):
    assert wl["startup_handlers"] == 0
    assert wl["calls"] == {"seed": 0, "plans": 0}


def test_normal_mode_unchanged_spot_check(full):
    assert full["startup_handlers"] >= 1
    assert full["calls"]["seed"] == 1 and full["calls"]["plans"] == 1  # startup seeding still runs
    assert full["health"][0] == 200 and full["waitlist"] == [200, 1]
    p = full["paths"]
    assert p["GET /openapi.json"] == 200 and p["GET /docs"] == 200
    assert p["GET /api/"] == 200
    assert p["POST /api/public/demo/start"] == 200
    assert p["GET /api/plans"] == 200
    assert p["GET /api/auth/me"] == 401
    assert p["POST /api/auth/login"] == 422   # route exists (empty body fails validation)
    # Every path blocked in waitlist mode is a real route in normal mode (so the 404s are meaningful).
    assert all(code != 404 for code in p.values()), {k: v for k, v in p.items() if v == 404}


def test_flag_parsing(monkeypatch):
    sys.path.insert(0, str(BACKEND))
    import waitlist_mode
    for v, expected in [("true", True), ("1", True), ("ON", True), ("", False), ("false", False), ("no", False)]:
        monkeypatch.setenv("WAITLIST_ONLY", v)
        assert waitlist_mode.waitlist_only_enabled() is expected
    monkeypatch.delenv("WAITLIST_ONLY")
    assert waitlist_mode.waitlist_only_enabled() is False


def test_env_example_name_only():
    ex = (BACKEND / ".env.example").read_text()
    assert "\nWAITLIST_ONLY=\n" in ex


def test_every_full_app_route_is_404_in_waitlist_mode(wl, full):
    assert len(full["routes"]) > 100  # sanity: the full app really was enumerated
    probed = {k: v for k, v in wl["paths"].items() if k not in LIVE}
    assert len(probed) >= len(full["routes"]) - 3
    leaks = {k: v for k, v in probed.items() if v != 404}
    assert leaks == {}
    assert sorted(wl["routes"]) == ["GET /api/health", "HEAD /api/health", "POST /api/public/waitlist"]


# ---- EMP-WL-032: HEAD /api/health ----
def test_head_health_200_no_body(wl):
    assert wl["health_head"] == [200, ""]


# ---- EMP-WL-029: no cross-site simple-request signups ----
@pytest.mark.parametrize("name", ["text_plain", "form", "multipart", "none"])
def test_non_json_waitlist_post_is_415_and_not_stored(wl, full, name):
    assert wl["guard"][name] == [415, 0]
    assert full["guard"][name] == [415, 0]  # same guard on the full app


def test_json_waitlist_post_accepted_without_allowlist(wl):
    # No CORS_ORIGINS (dev): Origin is not checked; the CORS layer is the only gate. Set CORS_ORIGINS.
    for name in ("json_charset", "json_good_origin", "json_evil_origin"):
        assert wl["guard"][name] == [200, 1], name


@pytest.mark.parametrize("run", ["wl_allowlist", "full_allowlist"])
def test_origin_must_match_cors_allowlist(request, run):
    g = request.getfixturevalue(run)["guard"]
    assert g["json_good_origin"] == [200, 1]
    assert g["json_evil_origin"] == [403, 0]
    assert g["json_null_origin"] == [403, 0]
    assert g["json_charset"] == [200, 1]  # no Origin header (curl / server-to-server)
    assert g["text_plain"] == [415, 0]


def test_guard_allowed_origins_parsing(monkeypatch):
    import sys as _sys
    _sys.path.insert(0, str(BACKEND))
    import waitlist_guard as g
    for raw, want in [("", None), ("*", None), (" ", None),
                      ("https://a.example, https://b.example/", ["https://a.example", "https://b.example"])]:
        monkeypatch.setenv("CORS_ORIGINS", raw)
        assert g.allowed_origins() == want


def test_guard_registered_in_server_and_waitlist_app():
    src = (BACKEND / "server.py").read_text()
    assert "app.add_middleware(WaitlistPostGuard)" in src
    assert "WaitlistPostGuard" in (BACKEND / "waitlist_mode.py").read_text()
