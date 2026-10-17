"""WS-2 — WhatsApp channel (Mexico entry playbook §2).

Covers: addressing, the template registry, keyword classification, the
24-hour window (pure + ledger-backed), the opt-in ledger, send paths
(window refusal, opt-in refusal, demo degradation), channel preference,
and the Twilio webhook's WhatsApp inbound branch (normalisation,
opt-out short-circuit, conversation channel). Fake in-memory mongo, same
pattern as test_inbox.py — no live DB, no network, no Twilio account.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("JWT_SECRET", "whatsapp-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "whatsapp_test")

import pytest  # noqa: E402

import services.whatsapp as wa  # noqa: E402


# ---------- fake mongo ----------

def _match(doc, query):
    for key, want in query.items():
        cur = doc
        for part in key.split("."):
            if not isinstance(cur, dict):
                return False
            cur = cur.get(part)
        if isinstance(want, dict) and "$ne" in want:
            if cur == want["$ne"]:
                return False
        elif cur != want:
            return False
    return True


class FakeCursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, key, direction=1):
        self.docs = sorted(self.docs, key=lambda d: str(d.get(key, "")), reverse=(direction < 0))
        return self

    async def to_list(self, n):
        return [dict(d) for d in self.docs[:n]]


class FakeCollection:
    def __init__(self):
        self.docs = []

    def find(self, query=None, proj=None):
        return FakeCursor([d for d in self.docs if _match(d, query or {})])

    async def find_one(self, query, proj=None):
        for d in self.docs:
            if _match(d, query):
                return dict(d)
        return None

    async def insert_one(self, doc):
        self.docs.append(dict(doc))

        class R:  # minimal insert result
            inserted_id = doc.get("id")
        return R()

    async def update_one(self, query, update, upsert=False):
        for d in self.docs:
            if _match(d, query):
                d.update(update.get("$set", {}))

                class R:
                    matched_count = 1
                return R()
        if upsert:
            doc = {k: v for k, v in query.items() if "." not in k}
            doc.update(update.get("$set", {}))
            self.docs.append(doc)

        class R:
            matched_count = 0
        return R()


class FakeDB:
    def __init__(self):
        self._cols = {}

    def __getattr__(self, name):
        return self._cols.setdefault(name, FakeCollection())


@pytest.fixture()
def fake_db(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(wa, "get_db", lambda: db)
    import services.twilio as twilio_mod
    monkeypatch.setattr(twilio_mod, "get_db", lambda: db)
    return db


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


TENANT = "tenant-mx-1"
PHONE = "+5215500000001"


async def _seed_whatsapp_cfg(db, with_sender=True):
    await db.integrations.insert_one({
        "tenant_id": TENANT, "key": "whatsapp",
        "config": {"waba_id": "WABA_TEST", "sender_number": "+5215500000099" if with_sender else "",
                   "display_name": "Climas MX", "templates": {}},
    })


# ---------- pure helpers ----------

def test_addressing():
    assert wa.is_whatsapp_address("whatsapp:+52155")
    assert not wa.is_whatsapp_address("+52155")
    assert wa.parse_whatsapp_address("whatsapp:+52155") == "+52155"
    assert wa.parse_whatsapp_address("+52155") == "+52155"
    assert wa.to_whatsapp_address("+52155") == "whatsapp:+52155"
    assert wa.to_whatsapp_address("whatsapp:+52155") == "whatsapp:+52155"


def test_template_registry_matches_playbook_catalogue():
    names = set(wa.list_templates())
    assert {"cita_confirmada", "recordatorio_cita", "tecnico_en_camino", "trabajo_terminado",
            "factura_lista", "pago_recibido", "saldo_pendiente", "codigo_verificacion",
            "promo_temporada", "reactivacion"} <= names
    assert wa.get_template("recordatorio_cita")["category"] == "utility"
    assert wa.get_template("codigo_verificacion")["category"] == "authentication"
    assert wa.get_template("promo_temporada")["category"] == "marketing"
    assert set(wa.list_templates("utility")) >= {"cita_confirmada", "factura_lista"}
    with pytest.raises(ValueError):
        wa.get_template("no_existe")


def test_keyword_classification():
    assert wa.classify_keyword("STOP") == "opt_out"
    assert wa.classify_keyword("baja") == "opt_out"
    assert wa.classify_keyword("ALTA") == "opt_in"
    assert wa.classify_keyword("Quiero agendar una cita") is None
    assert wa.classify_keyword("") is None


def test_window_math():
    now = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
    recent = (now - timedelta(hours=23)).isoformat()
    stale = (now - timedelta(hours=25)).isoformat()
    assert wa.window_open_at(recent, now) is True
    assert wa.window_open_at(stale, now) is False
    assert wa.window_open_at(None, now) is False
    assert wa.window_open_at("not-a-date", now) is False


# ---------- ledger + window (fake DB) ----------

def test_opt_in_ledger_roundtrip(fake_db):
    async def go():
        assert await wa.is_opted_in(TENANT, PHONE) is False
        await wa.record_opt_in(TENANT, PHONE, source="booking_form")
        assert await wa.is_opted_in(TENANT, PHONE, category="utility") is True
        assert await wa.is_opted_in(TENANT, PHONE, category="marketing") is False
        await wa.record_opt_out(TENANT, PHONE, keyword="BAJA")
        assert await wa.is_opted_in(TENANT, PHONE, category="utility") is False
        # tenant isolation
        assert await wa.is_opted_in("other-tenant", PHONE, category="utility") is False
    run(go())


def test_window_opens_on_inbound(fake_db):
    async def go():
        assert await wa.window_open(TENANT, PHONE) is False
        await wa.note_inbound(TENANT, PHONE)
        assert await wa.window_open(TENANT, PHONE) is True
        old = datetime.now(timezone.utc) - timedelta(hours=30)
        await wa.note_inbound(TENANT, PHONE, at=old)
        assert await wa.window_open(TENANT, PHONE) is False
    run(go())


# ---------- send paths ----------

def test_freeform_refused_outside_window(fake_db):
    async def go():
        await _seed_whatsapp_cfg(fake_db)
        res = await wa.send_whatsapp(tenant_id=TENANT, to=PHONE, body="Hola")
        assert res["status"] == "window_closed"
        assert res["sid"] is None
    run(go())


def test_freeform_demo_send_inside_window(fake_db):
    async def go():
        await _seed_whatsapp_cfg(fake_db)
        await wa.note_inbound(TENANT, PHONE)
        res = await wa.send_whatsapp(tenant_id=TENANT, to=PHONE, body="Claro, le ayudo.")
        # No live creds in tests -> demo degradation, same posture as SMS.
        assert res["demo"] is True and res["status"] == "demo"
    run(go())


def test_template_requires_opt_in_then_demo_sends(fake_db):
    async def go():
        await _seed_whatsapp_cfg(fake_db)
        res = await wa.send_whatsapp_template(tenant_id=TENANT, to=PHONE,
                                              template="recordatorio_cita",
                                              variables={"dia": "mañana", "hora": "10:00"})
        assert res["status"] == "opt_in_required"
        await wa.record_opt_in(TENANT, PHONE, source="widget")
        res = await wa.send_whatsapp_template(tenant_id=TENANT, to=PHONE,
                                              template="recordatorio_cita",
                                              variables={"dia": "mañana", "hora": "10:00"})
        assert res["demo"] is True and res["template"] == "recordatorio_cita"
    run(go())


def test_preferred_channel_mx_primary(fake_db):
    async def go():
        await _seed_whatsapp_cfg(fake_db)
        tenant_mx = {"id": TENANT, "country": "MX"}
        tenant_us = {"id": TENANT, "country": "US"}
        assert await wa.preferred_channel(TENANT, tenant_mx, PHONE) == "whatsapp"
        # US tenant without opt-in stays on SMS.
        assert await wa.preferred_channel(TENANT, tenant_us, PHONE) == "sms"
        await wa.record_opt_in(TENANT, PHONE, source="booking_form")
        assert await wa.preferred_channel(TENANT, tenant_us, PHONE) == "whatsapp"
    run(go())


def test_preferred_channel_no_sender_is_sms(fake_db):
    async def go():
        await _seed_whatsapp_cfg(fake_db, with_sender=False)
        assert await wa.preferred_channel(TENANT, {"country": "MX"}, PHONE) == "sms"
    run(go())


def test_status_callback_recorded(fake_db):
    async def go():
        doc = await wa.record_message_status(TENANT, {
            "MessageSid": "SM123", "MessageStatus": "read",
            "From": "whatsapp:+5215500000099", "To": "whatsapp:+5215500000001",
        })
        assert doc["channel"] == "whatsapp" and doc["status"] == "read"
        assert doc["from"] == "+5215500000099"
        assert len(fake_db.message_status_events.docs) == 1
    run(go())


# ---------- webhook inbound branch ----------

@pytest.fixture()
def webhook_client(monkeypatch, fake_db):
    import routers.twilio_webhook as wh

    monkeypatch.setattr(wh, "get_db", lambda: fake_db)

    async def fake_reply(*args, **kwargs):
        return {"reply": "Claro, con gusto le ayudo.", "action": None}
    monkeypatch.setattr(wh, "receptionist_reply", fake_reply)

    async def fake_usage(*args, **kwargs):
        return None
    monkeypatch.setattr(wh, "record_usage", fake_usage)

    # The confirmation shortcut is imported lazily inside the handler; stub
    # it so no real DB is consulted for YES/RESCHEDULE/CANCEL handling.
    import routers.phase9 as phase9_mod

    async def fake_confirmation(*args, **kwargs):
        return None
    monkeypatch.setattr(phase9_mod, "handle_confirmation_reply", fake_confirmation)

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(wh.router, prefix="/api")
    app.dependency_overrides[wh.require_twilio_signature] = lambda: None
    return TestClient(app), fake_db


def _seed_tenant(db):
    return asyncio.run(_seed(db))


async def _seed(db):
    await db.tenants.insert_one({"id": TENANT, "name": "Climas MX", "country": "MX",
                                 "industry_slug": "", "ai_employee": {}})
    await db.integrations.insert_one({
        "tenant_id": TENANT, "key": "whatsapp",
        "config": {"waba_id": "WABA_TEST", "sender_number": "+5215500000099",
                   "display_name": "Climas MX", "templates": {}},
    })


def test_webhook_whatsapp_inbound_creates_whatsapp_conversation(webhook_client):
    client, db = webhook_client
    _seed_tenant(db)
    resp = client.post("/api/twilio/sms", data={
        "From": "whatsapp:+5215500000001", "To": "whatsapp:+5215500000099",
        "Body": "Quiero agendar una cita",
    })
    assert resp.status_code == 200
    assert "Claro" in resp.text
    convs = db.conversations.docs
    assert len(convs) == 1 and convs[0]["channel"] == "whatsapp"
    assert convs[0]["caller_phone"] == "+5215500000001"
    # Window opened by the inbound.
    assert len(db.whatsapp_windows.docs) == 1


def test_webhook_whatsapp_opt_out_short_circuits(webhook_client):
    client, db = webhook_client
    _seed_tenant(db)
    resp = client.post("/api/twilio/sms", data={
        "From": "whatsapp:+5215500000001", "To": "whatsapp:+5215500000099", "Body": "BAJA",
    })
    assert resp.status_code == 200
    assert "ya no le enviaremos" in resp.text
    assert db.conversations.docs == []  # no conversation opened for an opt-out
    optins = db.whatsapp_optins.docs
    assert len(optins) == 1 and optins[0]["status"] == "opted_out"


def test_webhook_sms_still_works(webhook_client):
    client, db = webhook_client
    _seed_tenant(db)
    resp = client.post("/api/twilio/sms", data={
        "From": "+15550000001", "To": "+5215500000099", "Body": "hello",
    })
    assert resp.status_code == 200
    convs = db.conversations.docs
    assert len(convs) == 1 and convs[0]["channel"] == "sms"
