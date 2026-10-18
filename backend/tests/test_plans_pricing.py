"""Locked pricing ladder guard.

Locked pricing (decided 2026-10-15): Lite $9.99 / Starter $39.99 /
Pro $159.99 / Empire $369.99. DEFAULT_PLANS in routers/plans.py carried
Pro 15900 and Empire 36900 (a 99-cent undercharge on both) while
services/stripe_mx.py USD_LIST_CENTS already used the locked values.
This test pins DEFAULT_PLANS to the locked ladder and to the Stripe MX
mirror table so the two cannot drift apart again.

Unlimited $549.99 (decided 2026-10-17) sits above Empire: unlimited
users (limits.users is None = no cap), every other limit and the
overage table copied from Empire exactly — EXCEPT ai_minutes, which
the user deliberately set to 3,000 on Unlimited (vs Empire's 1,000)
the same day: minutes stay capped on Unlimited; only seats are not.
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
    "unlimited": 54999,  # $549.99
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


def _plan(key):
    return next(p for p in DEFAULT_PLANS if p["key"] == key)


def test_unlimited_plan_shape():
    """Unlimited (2026-10-17): $549.99, users uncapped (None), every other
    limit and the overage rates identical to Empire, sorted above Empire
    and below the custom Enterprise entry. ONE deliberate exception
    (user decision 2026-10-17): ai_minutes is 3,000 on Unlimited vs
    1,000 on Empire — minutes stay capped on Unlimited; only seats are
    unlimited. That asymmetry is the user's design, not drift."""
    unlimited, empire = _plan("unlimited"), _plan("empire")
    assert unlimited["name"] == "Unlimited"
    assert unlimited["price_cents"] == 54999
    assert unlimited["limits"]["users"] is None  # None = no cap
    # The deliberate exception, pinned on both sides so neither can drift.
    assert unlimited["limits"]["ai_minutes"] == 3000
    assert empire["limits"]["ai_minutes"] == 1000  # Empire itself unchanged
    for metric, value in empire["limits"].items():
        if metric in ("users", "ai_minutes"):
            continue
        assert unlimited["limits"][metric] == value, f"limits.{metric} drifted from Empire"
    assert unlimited["overage"] == empire["overage"]
    assert empire["sort_order"] < unlimited["sort_order"] < _plan("enterprise")["sort_order"]
    # The "Unlimited users" claim must be in the copy — and true (users is None).
    copy = " ".join([unlimited["description"]] + unlimited["features"])
    assert "Unlimited users" in copy
    # Usage-metering fallback parity: models.PLAN_LIMITS must know the key
    # with Empire's allowances (same ai_minutes exception: 3000 vs 1000),
    # or an unseeded Unlimited tenant meters as trial.
    # Reconciliation vs the audit (Track B, PR #98): PLAN_LIMITS now also
    # carries the seat caps under "users" (Empire 25). Unlimited's users is
    # None BY DESIGN (uncapped seats) — the second deliberate exception —
    # so the parity loop skips it and both sides are pinned explicitly.
    from models import PLAN_LIMITS
    assert PLAN_LIMITS["unlimited"]["ai_minutes"] == 3000
    assert PLAN_LIMITS["empire"]["ai_minutes"] == 1000
    assert PLAN_LIMITS["unlimited"]["users"] is None
    assert PLAN_LIMITS["empire"]["users"] == 25
    for metric, value in PLAN_LIMITS["empire"].items():
        if metric in ("ai_minutes", "users"):
            continue
        assert PLAN_LIMITS["unlimited"][metric] == value, (
            f"PLAN_LIMITS.{metric} drifted from Empire")
