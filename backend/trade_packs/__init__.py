"""Trade-pack infrastructure: load, validate, and list per-trade YAML packs.

Packs live in ``backend/trade_packs/packs/<id>.yaml`` and are written by
other workers — this module only reads them. The schema it validates
against is the canonical one:

    id, trade, tagline,
    terms{worker,worker_plural,shop,job,job_plural,customer,customer_plural},
    colors{accent},
    pricebook[{name,category(service|part|labor|addon),price,cost,duration_min,description}],
    equipment_types[{name,fields[]}],
    appointment_types[{name,duration_min,description}],
    receptionist_script (multiline string),
    triage[{id,question,options[],route}],
    automation_templates[{name,trigger,steps[]}],
    review_templates[{name,channel(sms|email),template}]
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List

import yaml

PACKS_DIR = Path(os.environ.get("TRADE_PACKS_DIR", Path(__file__).resolve().parent / "packs"))

# ---- schema constants ----

TOP_LEVEL_KEYS = [
    "id", "trade", "tagline", "terms", "colors", "pricebook",
    "equipment_types", "appointment_types", "receptionist_script",
    "triage", "automation_templates", "review_templates",
]

TERMS_KEYS = ["worker", "worker_plural", "shop", "job", "job_plural", "customer", "customer_plural"]

PRICEBOOK_CATEGORIES = {"service", "part", "labor", "addon"}

REVIEW_CHANNELS = {"sms", "email"}

# ---- cache ----

_pack_cache: Dict[str, dict] = {}


def _clear_cache() -> None:
    """Test helper: drop every cached pack."""
    _pack_cache.clear()


# ---- loading ----

def _pack_path(pack_id: str) -> Path:
    return PACKS_DIR / f"{pack_id}.yaml"


def load_pack(pack_id: str) -> dict:
    """Load and validate a pack by id. Raises ValueError if the file is
    missing or fails validation."""
    if pack_id in _pack_cache:
        return _pack_cache[pack_id]
    path = _pack_path(pack_id)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except FileNotFoundError:
        raise ValueError(f"Unknown trade pack: {pack_id!r}")
    except yaml.YAMLError as exc:
        # A draft pack with a syntax error is broken data, not a crash:
        # surface it as ValueError so callers (and the /{pack_id} 404 path)
        # stay consistent. Pack files are written by other workers; we only read.
        raise ValueError(f"Trade pack {pack_id!r}: invalid YAML: {exc}")
    if not isinstance(data, dict):
        raise ValueError(f"Trade pack {pack_id!r}: top level must be a mapping")
    validate_pack(data)
    _pack_cache[pack_id] = data
    return data


def list_packs() -> List[dict]:
    """Summaries of every pack in the packs dir, sorted by trade name.

    A pack that fails validation is reported with ``valid: False`` and an
    ``error`` message instead of raising, so one broken file can't hide
    the other 32. Summary shape: {id, trade, tagline, accent} (+valid/error).
    """
    summaries: List[dict] = []
    if not PACKS_DIR.is_dir():
        return summaries
    for path in sorted(PACKS_DIR.glob("*.yaml"), key=lambda p: p.stem):
        pack_id = path.stem
        try:
            data = load_pack(pack_id)
            colors = data.get("colors") or {}
            summaries.append({
                "id": data.get("id", pack_id),
                "trade": data.get("trade", ""),
                "tagline": data.get("tagline", ""),
                "accent": colors.get("accent", "#4F46E5"),
                "valid": True,
            })
        except ValueError as exc:
            summaries.append({
                "id": pack_id, "trade": pack_id, "tagline": "",
                "accent": "#4F46E5", "valid": False, "error": str(exc),
            })
    summaries.sort(key=lambda s: str(s.get("trade", "")).lower())
    return summaries


# ---- validation ----

def _req(data: dict, key: str, pack: str) -> object:
    if not isinstance(data, dict) or key not in data:
        raise ValueError(f"Trade pack {pack!r}: missing required key {key!r}")
    return data[key]


def validate_pack(data: dict) -> dict:
    """Validate a parsed pack dict against the canonical schema. Returns the
    dict unchanged on success; raises ValueError naming the offending field
    on the first failure."""
    pack = str(data.get("id") or "<unknown>")

    for key in TOP_LEVEL_KEYS:
        _req(data, key, pack)

    if not str(data["trade"]).strip():
        raise ValueError(f"Trade pack {pack!r}: 'trade' must be a non-empty string")
    if not str(data["tagline"]).strip():
        raise ValueError(f"Trade pack {pack!r}: 'tagline' must be a non-empty string")

    terms = data["terms"]
    if not isinstance(terms, dict):
        raise ValueError(f"Trade pack {pack!r}: 'terms' must be a mapping")
    for key in TERMS_KEYS:
        if key not in terms or not str(terms[key]).strip():
            raise ValueError(f"Trade pack {pack!r}: missing or empty terms.{key!r}")

    colors = data["colors"]
    if not isinstance(colors, dict) or not str(colors.get("accent") or "").strip():
        raise ValueError(f"Trade pack {pack!r}: missing or empty colors.accent")

    pricebook = data["pricebook"]
    if not isinstance(pricebook, list) or len(pricebook) < 8:
        raise ValueError(
            f"Trade pack {pack!r}: 'pricebook' must be a list with at least 8 items"
        )
    for i, item in enumerate(pricebook):
        where = f"pricebook[{i}]"
        if not isinstance(item, dict):
            raise ValueError(f"Trade pack {pack!r}: {where} must be a mapping")
        for key in ("name", "category", "price"):
            if key not in item:
                raise ValueError(f"Trade pack {pack!r}: {where} missing {key!r}")
        if not str(item["name"]).strip():
            raise ValueError(f"Trade pack {pack!r}: {where}.name must be non-empty")
        if str(item["category"]) not in PRICEBOOK_CATEGORIES:
            raise ValueError(
                f"Trade pack {pack!r}: {where}.category must be one of "
                f"{sorted(PRICEBOOK_CATEGORIES)}"
            )
        try:
            price = float(item["price"])
        except (TypeError, ValueError):
            raise ValueError(f"Trade pack {pack!r}: {where}.price must be numeric")
        if price < 0:
            raise ValueError(f"Trade pack {pack!r}: {where}.price must be >= 0")

    appointment_types = data["appointment_types"]
    if not isinstance(appointment_types, list) or len(appointment_types) < 2:
        raise ValueError(
            f"Trade pack {pack!r}: 'appointment_types' must be a list with at least 2 entries"
        )

    script = data["receptionist_script"]
    if not isinstance(script, str) or not script.strip():
        raise ValueError(f"Trade pack {pack!r}: 'receptionist_script' must be a non-empty string")

    triage = data["triage"]
    if not isinstance(triage, list) or len(triage) < 2:
        raise ValueError(f"Trade pack {pack!r}: 'triage' must be a list with at least 2 entries")

    automations = data["automation_templates"]
    if not isinstance(automations, list) or len(automations) < 2:
        raise ValueError(
            f"Trade pack {pack!r}: 'automation_templates' must be a list with at least 2 entries"
        )

    reviews = data["review_templates"]
    if not isinstance(reviews, list) or len(reviews) < 2:
        raise ValueError(
            f"Trade pack {pack!r}: 'review_templates' must be a list with at least 2 entries"
        )
    for i, r in enumerate(reviews):
        where = f"review_templates[{i}]"
        if not isinstance(r, dict):
            raise ValueError(f"Trade pack {pack!r}: {where} must be a mapping")
        for key in ("name", "channel", "template"):
            if key not in r:
                raise ValueError(f"Trade pack {pack!r}: {where} missing {key!r}")
        if str(r["channel"]) not in REVIEW_CHANNELS:
            raise ValueError(
                f"Trade pack {pack!r}: {where}.channel must be one of {sorted(REVIEW_CHANNELS)}"
            )

    equipment_types = data["equipment_types"]
    if not isinstance(equipment_types, list):
        raise ValueError(f"Trade pack {pack!r}: 'equipment_types' must be a list")

    return data
