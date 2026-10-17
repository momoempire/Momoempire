"""ES-MX localization pack — Mexico entry playbook §3 (2026-10-16), WS-1.

Data-first localization for the MX market:

- ``es_mx_trades.yaml``: per-trade-pack ES-MX trade names + worker nouns for
  all 33 trade packs (playbook §3.1), surfaced on the trade x market face
  when market = "mx" (see market_packs.resolver).
- ``es_mx_phrases.yaml``: canonical customer-facing phrase pack + role terms
  for the AI front office (playbook §3.1).
- Peninsular-Spanish trap purge list (playbook §3.1): words that must never
  reach a Mexican customer (fontanería, piscina, hormigón, coger, ...).
- MXN currency formatting + a display-only USD->MXN conversion.
- MX fiscal helpers (playbook §3.2): RFC shape validation and the régimen
  fiscal catalogue, used by the optional ``Tenant.mx_fiscal`` profile.

FX ASSUMPTION (playbook header, planning only — not a price decision):
MX$18.4 ~= US$1. Display conversions round to whole pesos.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional

import yaml

_DIR = Path(__file__).resolve().parent

# Playbook FX assumption: MX$18.4 ~= US$1 (planning/display only).
MXN_FX_RATE = 18.4

# ---- peninsular trap purge list (playbook §3.1) ----
# Word -> ES-MX replacement. Matching is whole-word, case-insensitive.
PENINSULAR_TRAPS: Dict[str, str] = {
    "fontanería": "plomería",
    "fontanero": "plomero",
    "piscina": "alberca",
    "hormigón": "concreto",
    "presupuesto": "cotización",
    "coger": "tomar",
    "ordenador": "computadora",
    "móvil": "celular",
    "coche": "carro",
    "conducir": "manejar",
    "manitas": "técnico de mantenimiento",
    "domótica": "casa inteligente",
    "vale": "",
}

# ---- data loading (cached) ----

_cache: Dict[str, dict] = {}


def _load_yaml(name: str) -> dict:
    if name not in _cache:
        with open(_DIR / name, "r", encoding="utf-8") as fh:
            _cache[name] = yaml.safe_load(fh) or {}
    return _cache[name]


def _clear_cache() -> None:
    """Test helper."""
    _cache.clear()


def es_mx_trade_overlay(pack_id: str) -> Optional[dict]:
    """ES-MX overlay for one trade pack: {trade, worker, worker_plural, ...}.

    Returns None for an unknown pack id (callers fall back to the pack's
    own English content, matching the resolver's serve-as-is rule)."""
    trades = (_load_yaml("es_mx_trades.yaml") or {}).get("trades") or {}
    row = trades.get(pack_id)
    return dict(row) if isinstance(row, dict) else None


def all_es_mx_trades() -> Dict[str, dict]:
    """Every ES-MX trade overlay, keyed by pack id."""
    trades = (_load_yaml("es_mx_trades.yaml") or {}).get("trades") or {}
    return {k: dict(v) for k, v in trades.items() if isinstance(v, dict)}


def es_mx_terms() -> Dict[str, str]:
    """Role terms in ES-MX (playbook §3.1): técnico, cotización, cita, ..."""
    return dict((_load_yaml("es_mx_phrases.yaml") or {}).get("terms") or {})


def get_phrase(key: str, **fmt) -> str:
    """Canonical ES-MX customer-facing phrase, optionally .format()-ed.

    Raises KeyError for an unknown phrase key — a missing canonical string
    is a data bug, not something to paper over at send time."""
    phrases = (_load_yaml("es_mx_phrases.yaml") or {}).get("phrases") or {}
    if key not in phrases:
        raise KeyError(f"Unknown es-MX phrase: {key!r}")
    text = str(phrases[key])
    return text.format(**fmt) if fmt else text


def find_peninsular_traps(text: str) -> List[str]:
    """Return the trap words present in ``text`` (whole-word, any case).

    Used as a launch-gate check on customer-facing ES strings (playbook
    §4.5: zero peninsular-trap strings in MX customer flows)."""
    if not text:
        return []
    found: List[str] = []
    for trap in PENINSULAR_TRAPS:
        if re.search(rf"(?<!\w){re.escape(trap)}(?!\w)", text, flags=re.IGNORECASE):
            found.append(trap)
    return found


# ---- currency ----

def format_mxn(amount_mxn: float, *, decimals: int = 2, disambiguate: bool = True) -> str:
    """Format an MXN amount es-MX style: $2,944.00 MXN.

    es-MX uses comma thousands separators and a dot decimal point, like
    en-US; the trailing "MXN" disambiguates the $ sign (playbook §3.1)."""
    body = f"${amount_mxn:,.{decimals}f}"
    return f"{body} MXN" if disambiguate else body


def usd_to_mxn_display(usd_amount: float, *, fx_rate: float = MXN_FX_RATE) -> str:
    """Display-only USD->MXN conversion at the playbook FX assumption,
    rounded to whole pesos. Never use for charging — MX prices are anchored
    natively in MXN (playbook §3.3, services/stripe_mx.py)."""
    return format_mxn(round(usd_amount * fx_rate), decimals=0)


# ---- MX fiscal helpers (playbook §3.2) ----

# SAT régimen fiscal catalogue (the codes trades actually meet; playbook §3.2).
REGIMENES_FISCALES: Dict[str, str] = {
    "601": "General de Ley Personas Morales",
    "603": "Personas Morales con Fines no Lucrativos",
    "605": "Sueldos y Salarios e Ingresos Asimilados a Salarios",
    "606": "Arrendamiento",
    "607": "Régimen de Enajenación o Adquisición de Bienes",
    "608": "Demás ingresos",
    "610": "Residentes en el Extranjero sin Establecimiento Permanente en México",
    "611": "Ingresos por Dividendos (socios y accionistas)",
    "612": "Personas Físicas con Actividades Empresariales y Profesionales",
    "614": "Ingresos por Intereses",
    "615": "Régimen de los Ingresos por Obtención de Préstamos",
    "616": "Sin Obligaciones Fiscales",
    "620": "Sociedades Cooperativas de Producción",
    "621": "Incorporación Fiscal",
    "622": "Actividades Agrícolas, Ganaderas, Silvícolas y Pesqueras",
    "623": "Opcional para Grupos de Sociedades",
    "624": "Coordinados",
    "625": "RESICO — Personas Morales",
    "626": "RESICO — Personas Físicas",
}

# Generic RFCs (público en general / extranjero) — valid receptors, never an emisor.
GENERIC_RFCS = {"XAXX010101000", "XEXX010101000"}

_RFC_RE = re.compile(r"^[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}$")


def validate_rfc(rfc: str) -> bool:
    """Shape-check an RFC: 12 chars (persona moral) or 13 (persona física),
    letters + 6-digit date + 3-char homoclave. Shape only — existence and
    name matching are validated by the PAC/SAT at stamping time."""
    if not rfc:
        return False
    return bool(_RFC_RE.match(str(rfc).strip().upper()))


def mx_fiscal_applies(country: str | None) -> bool:
    """The fiscal profile is MX-market-gated: it only applies to tenants
    whose country is MX."""
    return str(country or "").strip().upper() == "MX"


def validate_mx_fiscal(profile: dict | None) -> List[str]:
    """Validate an ``MxFiscalProfile``-shaped dict; returns error strings
    (empty list = valid). Empty/absent fields are allowed — the profile is
    optional until the shop onboards Books-MX (playbook §3.2)."""
    errors: List[str] = []
    if not profile:
        return errors
    rfc = str(profile.get("rfc") or "").strip()
    if rfc and not validate_rfc(rfc):
        errors.append("rfc: invalid RFC shape (12-13 chars: letters, 6-digit date, homoclave)")
    regimen = str(profile.get("regimen_fiscal") or "").strip()
    if regimen and regimen not in REGIMENES_FISCALES:
        errors.append(f"regimen_fiscal: unknown SAT catalogue code {regimen!r}")
    cp = str(profile.get("cp_fiscal") or "").strip()
    if cp and not (len(cp) == 5 and cp.isdigit()):
        errors.append("cp_fiscal: must be a 5-digit Mexican postal code")
    return errors
