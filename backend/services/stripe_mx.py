"""Stripe Mexico — MXN price book + local payment rails (playbook §3.3/§3.4, WS-3).

Everything here is data + pure builders behind the platform's existing
Stripe key-gating (services/stripe_config.py): with no STRIPE_SECRET_KEY
configured, checkout still refuses exactly as before, and demo mode is
unchanged. Activating a real Stripe MX account is an owner step
(playbook §4.4) — this module only makes the code ready for it.

FX ASSUMPTION (playbook header + §3.3): MX$18.4 ≈ US$1 — the rate implied
by the international scan's "Pro ≈ MX$2,940/mo" figure. All MXN anchors
below are planning approximations derived from the locked USD list
prices at that rate, NOT independently decided peso prices.

Pending-state contract (playbook §3.4 + §4.5 gate): OXXO and SPEI are
NOT auto-debit rails. Checkout completes with payment_status "unpaid"
while the customer pays the voucher / transfer; the subscription may
only activate when checkout.session.async_payment_succeeded lands, and
checkout.session.async_payment_failed closes the transaction without
activating. routers/payments.py enforces this via
ACTIVATING_PAYMENT_STATUSES + the async handlers.
"""
from __future__ import annotations

from locales import format_mxn

# Playbook planning assumption — see module docstring. If the rate moves,
# re-derive the anchors; do not hand-tune individual peso prices here.
MXN_FX_RATE = 18.4

# Locked USD list prices (cents), mirroring routers/plans.py DEFAULT_PLANS.
USD_LIST_CENTS: dict = {
    "lite": 999,        # $9.99
    "starter": 3999,    # $39.99
    "pro": 15999,       # $159.99
    "empire": 36999,    # $369.99
    "unlimited": 54999,  # $549.99 (added 2026-10-17; anchor derived by the same FX pattern)
}


def _anchor_pesos(usd_cents: int) -> int:
    """Whole-peso MXN anchor: USD dollars x FX rate, rounded (playbook §3.3
    table: 184 / 736 / 2,944 / 6,808)."""
    return round(usd_cents / 100 * MXN_FX_RATE)


# plan key -> anchor record. mxn_cents is what Checkout charges.
MXN_PRICE_ANCHORS: dict = {
    key: {
        "usd_cents": usd_cents,
        "mxn_pesos": (p := _anchor_pesos(usd_cents)),
        "mxn_cents": p * 100,
        "fx_rate": MXN_FX_RATE,
    }
    for key, usd_cents in USD_LIST_CENTS.items()
}

# Local rails (playbook §3.4): card (with MSI — meses sin intereses, enabled
# in the options below), OXXO cash vouchers, and SPEI via Stripe's
# customer_balance bank transfer (bank_transfer type "mx_bank_transfer").
MX_PAYMENT_METHOD_TYPES = ["card", "oxxo", "customer_balance"]

MX_PAYMENT_METHOD_OPTIONS: dict = {
    "card": {"installments": {"enabled": True}},  # MSI, up to 18 on major networks
    "customer_balance": {
        "funding_type": "bank_transfer",
        "bank_transfer": {"type": "mx_bank_transfer"},  # SPEI
    },
}

# Stripe MX fee context (playbook §3.4; per Stripe MX pricing as reported
# Feb 2026 — [verify on stripe.com/mx/pricing at build time]; informational
# only, nothing in billing logic reads this).
STRIPE_MX_FEES = {
    "card": "3.6% + MX$3 + IVA",
    "oxxo": "3.6% + MX$3 + IVA",
    "spei": "MX$7 + IVA flat",
    "international_card_surcharge": "+0.5–1.5%",
}


def mx_checkout_line_item(plan: dict, quantity: int = 1) -> dict:
    """Ad-hoc MXN price_data line item for a plan dict (key/name), priced
    from the §3.3 anchor table — the MX price book is anchor-based, so an
    admin-configured USD stripe_price_id is never reused for MX checkout."""
    from brand import brand_name

    key = plan["key"]
    if key not in MXN_PRICE_ANCHORS:
        raise ValueError(f"No MXN anchor for plan {key!r}")
    return {
        "price_data": {
            "currency": "mxn",
            "product_data": {"name": f"{brand_name()} — {plan['name']}"},
            "unit_amount": MXN_PRICE_ANCHORS[key]["mxn_cents"],
            "recurring": {"interval": "month"},
        },
        "quantity": quantity,
    }


def mx_checkout_kwargs(plan: dict, quantity: int = 1) -> dict:
    """Session kwargs for an MX-market subscription checkout."""
    return {
        "line_items": [mx_checkout_line_item(plan, quantity)],
        "payment_method_types": list(MX_PAYMENT_METHOD_TYPES),
        "payment_method_options": MX_PAYMENT_METHOD_OPTIONS,
    }


def format_price_display_mx(plan_key: str) -> dict:
    """IVA-inclusive MX price display (playbook §3.4: prices shown to MX
    customers must be IVA-inclusive by Mexican consumer-pricing law)."""
    if plan_key not in MXN_PRICE_ANCHORS:
        raise ValueError(f"No MXN anchor for plan {plan_key!r}")
    anchor = MXN_PRICE_ANCHORS[plan_key]
    # locales.format_mxn takes a peso amount and already appends the
    # "MXN" disambiguator: "$2,944.00 MXN".
    amount = format_mxn(anchor["mxn_cents"] / 100)
    return {
        "plan": plan_key,
        "currency": "MXN",
        "mxn_cents": anchor["mxn_cents"],
        "amount": amount,
        "iva_included": True,
        "label_es": f"{amount} al mes · IVA incluido",
        "label_en": f"{amount} per month · VAT (IVA) included",
    }


# Billing copy data (playbook §3.4 packaging recommendation): monthly by
# card; annual pushed via SPEI/OXXO — one transfer, 12 months, framed as
# "ahorra dos meses" (the platform's annual = 2 months free convention).
MX_BILLING_COPY: dict = {
    "annual_via_transfer": {
        "es": "Paga el año por SPEI u OXXO y ahorra dos meses: una sola transferencia, "
              "sin cargos mes a mes.",
        "en": "Pay the year by SPEI or OXXO and save two months: one single transfer, "
              "no monthly charges.",
    },
    "oxxo_pending": {
        "es": "Su ficha de OXXO está generada. Su suscripción se activa en cuanto se "
              "refleje el pago (puede tardar unas horas).",
        "en": "Your OXXO voucher is ready. Your subscription activates as soon as the "
              "payment lands (it can take a few hours).",
    },
    "spei_pending": {
        "es": "Transfiera por SPEI con los datos indicados. Su suscripción se activa "
              "al confirmarse la transferencia.",
        "en": "Transfer by SPEI using the details shown. Your subscription activates "
              "once the transfer is confirmed.",
    },
    "iva_note": {
        "es": "Precios en pesos mexicanos (MXN), IVA incluido.",
        "en": "Prices in Mexican pesos (MXN), VAT (IVA) included.",
    },
    "payment_methods_note": {
        "es": "Aceptamos tarjeta (incluye meses sin intereses), OXXO y SPEI.",
        "en": "We accept card (including interest-free monthly installments), OXXO and SPEI.",
    },
}


_PLAN_NAMES = {"lite": "Lite", "starter": "Starter", "pro": "Pro", "empire": "Empire", "unlimited": "Unlimited"}


def mx_plans_display() -> list:
    """All MX display prices + copy, for GET /payments/plans/mx."""
    return [
        {**format_price_display_mx(key), "name": _PLAN_NAMES[key]}
        for key in ("lite", "starter", "pro", "empire", "unlimited")
    ]
