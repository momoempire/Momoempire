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
out["startup_names"] = [getattr(h, "__name__", "?") for h in server.app.router.on_startup]
out["middleware"] = [m.cls.__name__ for m in server.app.user_middleware]
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
    cors_h = {}
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
        # EMP-WL-036: guard errors seen by a browser (Origin set)
        "text_plain_good_origin": {"Content-Type": "text/plain", "Origin": "https://good.example"},
        "text_plain_evil_origin": {"Content-Type": "text/plain", "Origin": "https://evil.example"},
    }.items():
        before = len(FAKE.waitlist.docs)
        rr = c.post("/api/public/waitlist", content=body.replace("xsite", name.replace("_", "")), headers=hdrs)
        g[name] = [rr.status_code, len(FAKE.waitlist.docs) - before]
        cors_h[name] = [rr.headers.get("access-control-allow-origin"), rr.headers.get("access-control-allow-credentials"),
                        rr.headers.get("content-type"), rr.text]
    out["guard"] = g
    out["guard_cors"] = cors_h
    pf = {}
    for name, origin in {"good": "https://good.example", "evil": "https://evil.example"}.items():
        rr = c.options("/api/public/waitlist", headers={"Origin": origin, "Access-Control-Request-Method": "POST",
                                                         "Access-Control-Request-Headers": "content-type"})
        pf[name] = [rr.status_code, rr.headers.get("access-control-allow-origin"),
                    rr.headers.get("access-control-allow-credentials")]
    out["preflight"] = pf
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


def _run(waitlist_only: bool, paths, cors_origins: str | None = None, app_env: str | None = None):
    env = {k: v for k, v in os.environ.items()
           if k not in ("WAITLIST_ONLY", "CORS_ORIGINS", "TURNSTILE_ENABLED", "APP_ENV")}
    env.update({"JWT_SECRET": "x", "MONGO_URL": "mongodb://localhost:1", "DB_NAME": "x",
                "PROBE_PATHS": json.dumps(paths)})
    if waitlist_only:
        env["WAITLIST_ONLY"] = "true"
    if cors_origins is not None:
        env["CORS_ORIGINS"] = cors_origins
    if app_env is not None:
        env["APP_ENV"] = app_env
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
    # Only the waitlist unique-index build may run at startup (with PR #18; none before it).
    assert set(wl["startup_names"]) <= {"_waitlist_startup"}
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
    # EMP-WL-036: registered with install_waitlist_guard (inside CORS), not add_middleware.
    src = (BACKEND / "server.py").read_text()
    assert "install_waitlist_guard(app)" in src and "app.add_middleware(WaitlistPostGuard)" not in src
    assert "install_waitlist_guard(app)" in (BACKEND / "waitlist_mode.py").read_text()


# ---- EMP-WL-036: guard 415/403 replies carry CORS headers for allowed origins only ----
# These hold on #13 alone (inline CORS config in server.py) and with PR #10 (server.py uses
# deploy_security.cors_options); the only difference is production with CORS_ORIGINS unset.
PR10 = "from deploy_security import cors_options" in (BACKEND / "server.py").read_text()


@pytest.fixture(scope="module")
def full_upper():
    return _run(False, [], cors_origins="https://Good.Example/")


@pytest.fixture(scope="module")
def full_prod_unset():
    return _run(False, [], app_env="production")


@pytest.fixture(scope="module")
def wl_prod_unset():
    return _run(True, [], app_env="production")


@pytest.mark.parametrize("run", ["full", "wl", "full_allowlist", "wl_allowlist", "full_prod_unset", "wl_prod_unset"])
def test_guard_runs_inside_cors(request, run):
    mw = request.getfixturevalue(run)["middleware"]
    assert "CORSMiddleware" in mw and "WaitlistPostGuard" in mw
    assert mw.index("WaitlistPostGuard") > mw.index("CORSMiddleware")  # later in the list = inner
    # EMP-WL-052: the credentials-header filter must be OUTSIDE CORS to see its headers.
    assert mw.index("DropOrphanCredentialsHeader") < mw.index("CORSMiddleware")


@pytest.mark.parametrize("run", ["full_allowlist", "wl_allowlist"])
def test_guard_errors_readable_by_allowed_origin_only(request, run):
    r = request.getfixturevalue(run)
    g, h = r["guard"], r["guard_cors"]
    # Allowed origin, non-JSON: 415 WITH the CORS headers, so the page can read the reason.
    assert g["text_plain_good_origin"] == [415, 0]
    acao, acac, ctype, text = h["text_plain_good_origin"]
    assert acao == "https://good.example" and acac == "true"
    assert ctype == "application/json" and "application/json" in text
    # Disallowed origin: 403 (JSON) / 415 (non-JSON) and NO Access-Control-Allow-Origin.
    assert g["json_evil_origin"] == [403, 0] and h["json_evil_origin"][0] is None
    assert g["text_plain_evil_origin"] == [415, 0] and h["text_plain_evil_origin"][0] is None
    assert g["json_null_origin"] == [403, 0] and h["json_null_origin"][0] is None
    # Success for the allowed origin still has it; no Origin header => no CORS header.
    assert h["json_good_origin"][0] == "https://good.example"
    assert h["text_plain"][0] is None
    # EMP-WL-052: no Access-Control-Allow-Credentials without Allow-Origin (disallowed origins).
    for name in ("json_evil_origin", "text_plain_evil_origin", "json_null_origin", "text_plain"):
        assert h[name][1] is None, name
    assert h["json_good_origin"][1] == "true"  # allowed origin keeps it
    pf = r["preflight"]
    assert pf["good"] == [200, "https://good.example", "true"]
    assert pf["evil"][0] == 400 and pf["evil"][1] is None and pf["evil"][2] is None


@pytest.mark.parametrize("run", ["full", "wl"])
def test_dev_without_allowlist_guard_errors_have_cors_headers(request, run):
    # Development, CORS_ORIGINS unset: any origin may read (#13 alone: echoed origin; with #10: "*").
    h = request.getfixturevalue(run)["guard_cors"]
    assert h["text_plain_good_origin"][0] in ("https://good.example", "*")
    assert h["text_plain_evil_origin"][0] in ("https://evil.example", "*")


@pytest.mark.parametrize("run", ["full_prod_unset", "wl_prod_unset"])
def test_production_without_cors_origins_starts(request, run):
    # The server STARTS either way. Without #10 the inline config still lets any origin read; with
    # #10 it fails closed: no Access-Control-Allow-Origin at all, so browsers can't read any reply.
    r = request.getfixturevalue(run)
    assert r["health"][0] == 200
    acao = r["guard_cors"]["text_plain_good_origin"][0]
    if PR10:
        assert acao is None
    else:
        assert acao == "https://good.example"
    assert r["guard"]["json_evil_origin"][0] == 200  # no allow-list => the guard doesn't check Origin


def test_allowlist_entry_case_and_trailing_slash_ignored(full_upper):
    g = full_upper["guard"]
    assert g["json_good_origin"] == [200, 1]  # CORS_ORIGINS="https://Good.Example/"
    assert g["json_evil_origin"] == [403, 0]


# ---- EMP-WL-035: deploy doc states the real behaviour ----
def test_deploy_doc_fixes():
    doc = (BACKEND.parent / "docs" / "deploy" / "waitlist-only.md").read_text()
    assert "startup fails" not in doc and "The server **starts**" in doc
    assert "Deploy only after PR #11 is merged" in doc
    assert "`FORWARDED_ALLOW_IPS` | the proxy's IP" in doc and "**Required** (needs #11)" in doc
    for value in ("172.30.0.1", "127.0.0.1", "set_real_ip_from"):
        assert value in doc
    assert "Same-origin setups still need it" in doc
    # EMP-WL-052 / WL-045
    assert "**Use lower case (EMP-WL-052).**" in doc and "all lower case" in doc
    assert "`WAITLIST_CONFIRMATION_EMAIL` | leave **unset**" in doc
    assert "**confirmation email** (EMP-WL-008/009)" not in doc
    ex = (BACKEND / ".env.example").read_text()
    assert "enforces this at startup" not in ex and "still starts but fails closed" in ex
