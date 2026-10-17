"""WS-1 — MX localization pack (Mexico entry playbook §3).

Covers: the ES-MX trade overlay for all 33 trade packs, the canonical
phrase pack, the peninsular-trap purge, MXN formatting, the trade x market
resolver's MX face, and the MX-gated fiscal profile fields (playbook §3.2).
Pure data/logic tests — no DB, no network.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

import locales  # noqa: E402
from locales import (  # noqa: E402
    MXN_FX_RATE,
    all_es_mx_trades,
    es_mx_terms,
    es_mx_trade_overlay,
    find_peninsular_traps,
    format_mxn,
    get_phrase,
    mx_fiscal_applies,
    usd_to_mxn_display,
    validate_mx_fiscal,
    validate_rfc,
)
from market_packs.resolver import resolve_face  # noqa: E402
from models import MxFiscalProfile, Tenant, TenantUpdate  # noqa: E402
from trade_packs import list_packs  # noqa: E402


# ---------- trade overlay ----------

def test_overlay_covers_exactly_the_33_trade_packs():
    pack_ids = {p["id"] for p in list_packs() if p.get("valid", True)}
    overlay_ids = set(all_es_mx_trades())
    assert len(pack_ids) == 33
    assert overlay_ids == pack_ids


def test_overlay_spot_checks():
    assert es_mx_trade_overlay("plumbing")["trade"] == "plomería"
    assert es_mx_trade_overlay("plumbing")["worker"] == "plomero"
    assert es_mx_trade_overlay("pool_service")["trade"] == "mantenimiento de albercas"
    assert es_mx_trade_overlay("fencing_concrete")["trade"] == "cercas y concreto"
    assert "minisplit" in es_mx_trade_overlay("hvac")["notes"]
    assert es_mx_trade_overlay("nope") is None


def test_overlay_customer_strings_have_no_peninsular_traps():
    for pack_id, row in all_es_mx_trades().items():
        for field in ("trade", "worker", "worker_plural"):
            traps = find_peninsular_traps(row.get(field, ""))
            assert traps == [], f"{pack_id}.{field} contains traps: {traps}"


# ---------- phrase pack + terms ----------

def test_terms_are_mexican():
    terms = es_mx_terms()
    assert terms["worker"] == "técnico"
    assert terms["estimate"] == "cotización"
    assert terms["appointment"] == "cita"


def test_phrase_pack_formatting_and_no_traps():
    msg = get_phrase("booking_confirmed", dia="martes 4", hora="10:00", nombre="Luis")
    assert msg == "Su cita quedó agendada para el martes 4 a las 10:00. Su técnico es Luis."
    for key in ("greeting", "invoice_ready", "payment_methods", "review_request"):
        text = get_phrase(key, liga="https://x", total="$100.00 MXN", dia="lunes")
        assert find_peninsular_traps(text) == [], f"phrase {key} has traps"
    with pytest.raises(KeyError):
        get_phrase("does_not_exist")


def test_trap_detector_catches_peninsular_spanish():
    traps = find_peninsular_traps("El fontanero revisará la piscina y el hormigón")
    assert set(traps) == {"fontanero", "piscina", "hormigón"}
    assert find_peninsular_traps("El plomero revisará la alberca y el concreto") == []
    assert find_peninsular_traps("") == []


# ---------- currency ----------

def test_format_mxn():
    assert format_mxn(2944) == "$2,944.00 MXN"
    assert format_mxn(184.5) == "$184.50 MXN"
    assert format_mxn(1000000, decimals=0, disambiguate=False) == "$1,000,000"


def test_usd_to_mxn_display_uses_playbook_fx():
    assert MXN_FX_RATE == 18.4
    # Playbook §3.3 anchors: Pro $159.99 -> ~MX$2,944; Lite $9.99 -> ~MX$184.
    assert usd_to_mxn_display(159.99) == "$2,944 MXN"
    assert usd_to_mxn_display(9.99) == "$184 MXN"


# ---------- resolver MX face ----------

def test_resolver_mx_face_carries_es_mx_overlay():
    face = resolve_face("plumbing", "mx")
    assert face["locale"] == "es-MX"
    assert face["currency"] == "MXN"
    assert face["trade_es_mx"] == "plomería"
    assert face["terms_es_mx"]["worker"] == "plomero"
    assert face["terms_es_mx"]["estimate"] == "cotización"
    assert face["languages"] == ["es", "en"]
    assert len(face["pricebook_display"]) == len(face["pricebook"])
    assert all(d["price_display"].endswith("MXN") for d in face["pricebook_display"])


def test_resolver_non_mx_face_unchanged():
    face = resolve_face("hvac", "de")
    assert "locale" not in face
    assert "trade_es_mx" not in face
    assert "pricebook_display" not in face


# ---------- fiscal profile (playbook §3.2) ----------

def test_rfc_shape_validation():
    assert validate_rfc("ABC010101AB1")        # persona moral, 12
    assert validate_rfc("ABCD010101AB1")       # persona física, 13
    assert validate_rfc("xaxx010101000")        # generic, case-insensitive
    assert not validate_rfc("ABC0101")
    assert not validate_rfc("ABCD010101AB12")
    assert not validate_rfc("")


def test_validate_mx_fiscal():
    assert validate_mx_fiscal(None) == []
    assert validate_mx_fiscal({}) == []
    ok = {"rfc": "ABCD010101AB1", "regimen_fiscal": "626", "cp_fiscal": "06600"}
    assert validate_mx_fiscal(ok) == []
    bad = {"rfc": "nope", "regimen_fiscal": "999", "cp_fiscal": "123"}
    errors = validate_mx_fiscal(bad)
    assert len(errors) == 3


def test_fiscal_profile_is_mx_gated():
    assert mx_fiscal_applies("MX")
    assert mx_fiscal_applies("mx")
    assert not mx_fiscal_applies("US")
    assert not mx_fiscal_applies(None)


def test_tenant_model_fiscal_fields_optional():
    t = Tenant(slug="taller-lopez", name="Taller López")
    assert t.mx_fiscal is None
    t2 = Tenant(slug="climas-mx", name="Climas MX", country="MX",
                mx_fiscal=MxFiscalProfile(rfc="CMX010101AB1", regimen_fiscal="626", cp_fiscal="64000"))
    assert t2.mx_fiscal.rfc == "CMX010101AB1"
    upd = TenantUpdate(mx_fiscal=MxFiscalProfile(rfc="CMX010101AB1"))
    assert upd.mx_fiscal is not None and upd.mx_fiscal.regimen_fiscal == ""
