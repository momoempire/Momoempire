"""Locked pricing ladder guard.

Locked pricing (decided 2026-10-15): Lite $9.99 / Starter $39.99 /
Pro $159.99 / Empire $369.99. DEFAULT_PLANS in routers/plans.py carried
Pro 15900 and Empire 36900 (a 99-cent undercharge on both) while
services/stripe_mx.py USD_LIST_CENTS already used the locked values.
This test pins DEFAULT_PLANS to the locked ladder and to the Stripe MX
mirror table so the two cannot drift apart again.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("JWT_SECRET", "plans-pricing-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "plans_pricing_test")

from routers.plans import DEFAULT_PLANS  # noqa: E402
import services.stripe_mx as mx  # noqa: E402

LOCKED_PRICE_CENTS = {
    "lite": 999,        # $9.99
    "starter": 3999,    # $39.99
    "pro": 15999,       # $159.99
    "empire": 36999,    # $369.99
}


def _price_by_key():
    return {p["key"]: p["price_cents"] for p in DEFAULT_PLANS}


def test_default_plans_match_locked_pricing():
    by_key = _price_by_key()
    for key, cents in LOCKED_PRICE_CENTS.items():
        assert by_key[key] == cents, f"{key}: expected {cents}, got {by_key.get(key)}"


def test_default_plans_match_stripe_mx_usd_list():
    by_key = _price_by_key()
    for key, cents in mx.USD_LIST_CENTS.items():
        assert by_key[key] == cents, f"{key}: DEFAULT_PLANS {by_key.get(key)} != stripe_mx {cents}"


# Owner-approved 2026-10-17 (PR #97): user caps Lite 1 / Starter 2 /
# Pro 12 / Empire 25. Prices above pin the money; this pins the seats so a
# future plan edit cannot silently re-inflate the caps.
LOCKED_USER_CAPS = {
    "lite": 1,
    "starter": 2,
    "pro": 12,
    "empire": 25,
}


def test_default_plans_match_locked_user_caps():
    by_key = {p["key"]: (p.get("limits") or {}).get("users") for p in DEFAULT_PLANS}
    for key, cap in LOCKED_USER_CAPS.items():
        assert by_key[key] == cap, f"{key}: expected {cap} users, got {by_key.get(key)}"
