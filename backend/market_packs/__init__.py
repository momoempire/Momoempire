"""Market-pack infrastructure: load, validate, and list per-market YAML packs.

Packs live in ``backend/market_packs/packs/<id>.yaml``. The schema is
deliberately lean (per user instruction — "just colors, languages and
country flag")::

    id, name, flag, extended_network,
    colors{accent},
    languages[]            # AI receptionist languages for the market

``media`` is optional and defaults to absent: include a ``media:`` key only
where a market genuinely needs pics/media. Trade x market composition lives
in :mod:`market_packs.resolver`.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List

import yaml

PACKS_DIR = Path(os.environ.get("MARKET_PACKS_DIR", Path(__file__).resolve().parent / "packs"))

# ---- schema constants ----

TOP_LEVEL_KEYS = ["id", "name", "flag", "extended_network", "colors", "languages"]

# ---- cache ----

_pack_cache: Dict[str, dict] = {}


def _clear_cache() -> None:
    """Test helper: drop every cached pack."""
    _pack_cache.clear()


# ---- loading ----

def _pack_path(pack_id: str) -> Path:
    return PACKS_DIR / f"{pack_id}.yaml"


def load_pack(pack_id: str) -> dict:
    """Load and validate a market pack by id. Raises ValueError if the file
    is missing or fails validation."""
    if pack_id in _pack_cache:
        return _pack_cache[pack_id]
    path = _pack_path(pack_id)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except FileNotFoundError:
        raise ValueError(f"Unknown market pack: {pack_id!r}")
    except yaml.YAMLError as exc:
        raise ValueError(f"Market pack {pack_id!r}: invalid YAML: {exc}")
    if not isinstance(data, dict):
        raise ValueError(f"Market pack {pack_id!r}: top level must be a mapping")
    validate_pack(data)
    _pack_cache[pack_id] = data
    return data


def list_packs() -> List[dict]:
    """Summaries of every market pack, sorted by name. A pack that fails
    validation is reported with ``valid: False`` instead of raising."""
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
                "name": data.get("name", ""),
                "flag": data.get("flag", ""),
                "accent": colors.get("accent", "#4F46E5"),
                "languages": data.get("languages", []),
                "extended_network": bool(data.get("extended_network", False)),
                "valid": True,
            })
        except ValueError as exc:
            summaries.append({
                "id": pack_id, "name": pack_id, "flag": "", "accent": "#4F46E5",
                "languages": [], "extended_network": False,
                "valid": False, "error": str(exc),
            })
    summaries.sort(key=lambda s: str(s.get("name", "")).lower())
    return summaries


# ---- validation ----

def _req(data: dict, key: str, pack: str) -> object:
    if not isinstance(data, dict) or key not in data:
        raise ValueError(f"Market pack {pack!r}: missing required key {key!r}")
    return data[key]


def validate_pack(data: dict) -> dict:
    """Validate a parsed market pack against the lean schema. Returns the
    dict unchanged on success; raises ValueError on the first failure."""
    pack = str(data.get("id") or "<unknown>")

    for key in TOP_LEVEL_KEYS:
        _req(data, key, pack)

    if not str(data["id"]).strip():
        raise ValueError(f"Market pack {pack!r}: 'id' must be a non-empty string")
    if not str(data["name"]).strip():
        raise ValueError(f"Market pack {pack!r}: 'name' must be a non-empty string")
    if not str(data["flag"]).strip():
        raise ValueError(f"Market pack {pack!r}: 'flag' must be a non-empty string")

    colors = data["colors"]
    if not isinstance(colors, dict) or not str(colors.get("accent") or "").strip():
        raise ValueError(f"Market pack {pack!r}: missing or empty colors.accent")

    languages = data["languages"]
    if not isinstance(languages, list) or not languages:
        raise ValueError(f"Market pack {pack!r}: 'languages' must be a non-empty list")
    for lang in languages:
        if not isinstance(lang, str) or not lang.strip():
            raise ValueError(f"Market pack {pack!r}: 'languages' entries must be non-empty strings")

    # media is optional; when present it must be a mapping or list
    media = data.get("media")
    if media is not None and not isinstance(media, (dict, list)):
        raise ValueError(f"Market pack {pack!r}: 'media' must be a mapping or list when present")

    return data
