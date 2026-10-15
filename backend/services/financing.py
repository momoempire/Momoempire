"""Financing at checkout — provider-abstracted customer financing.

Product decision (user, 2026-10-15): the live provider target is **Wisetack**
(home-services financing leader, embedded in Jobber/Housecall Pro, loans up to
$25,000). Shops connect their OWN Wisetack merchant accounts later — same
model as Stripe Connect — so there are no platform-level credentials and no
live calls anywhere in this module.

Design:
    * `FinancingProvider` ABC is the contract every provider implements.
    * `DemoFinancingProvider` computes offers with real amortization math and is
      clearly labeled demo. It is the default until a tenant connects live.
    * `WisetackProvider` is a skeleton implementing the same interface. Every
      spot that would touch the Wisetack API is marked TODO(LIVE) and raises
      `FinancingNotConfigured` today — no network calls, no keys.

Money is integers (cents) everywhere; APRs are floats (percent, e.g. 9.99).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional, Sequence


class FinancingError(Exception):
    """Base financing error (maps to 4xx/5xx in the router)."""


class FinancingNotConfigured(FinancingError):
    """The selected provider has no live credentials yet."""


# ---------- math ----------

def monthly_payment_cents(principal_cents: int, apr_percent: float, term_months: int) -> int:
    """Standard amortization: P * r(1+r)^n / ((1+r)^n - 1), rounded to cents.

    `apr_percent` is a percent (9.99, not 0.0999). A 0% APR (promotional rate)
    pays principal straight over the term — handled explicitly so the formula
    never divides by zero.
    """
    if term_months <= 0:
        raise ValueError("term_months must be positive")
    if principal_cents < 0:
        raise ValueError("principal_cents must be non-negative")
    if apr_percent < 0:
        raise ValueError("apr_percent must be non-negative")
    if apr_percent == 0:
        return int(round(principal_cents / term_months))
    r = apr_percent / 100.0 / 12.0
    factor = (1.0 + r) ** term_months
    payment = principal_cents * r * factor / (factor - 1.0)
    return int(round(payment))


# ---------- offer ----------

@dataclass(frozen=True)
class FinancingOffer:
    term_months: int
    apr_percent: float
    monthly_payment_cents: int
    provider_name: str
    demo: bool = False
    total_payable_cents: Optional[int] = field(default=None)

    def __post_init__(self):
        if self.total_payable_cents is None:
            object.__setattr__(self, "total_payable_cents",
                               self.monthly_payment_cents * self.term_months)

    def as_dict(self) -> dict:
        return {
            "term_months": self.term_months,
            "apr_percent": self.apr_percent,
            "monthly_payment_cents": self.monthly_payment_cents,
            "total_payable_cents": self.total_payable_cents,
            "provider_name": self.provider_name,
            "demo": self.demo,
        }


# ---------- provider contract ----------

class FinancingProvider(ABC):
    """Contract every financing provider implements.

    `get_offers` is synchronous and deterministic — it is pure math, safe to
    call from a public page. `create_application` is async because the live
    provider talks to an external API.
    """

    name: str = "base"

    @abstractmethod
    def get_offers(self, amount_cents: int,
                   term_options: Optional[Sequence[int]] = None) -> List[FinancingOffer]:
        """Monthly-payment options for an amount. Raises FinancingError on bad input."""

    @abstractmethod
    async def create_application(self, *, amount_cents: int, term_months: int,
                                 customer: dict, quote_id: Optional[str] = None,
                                 invoice_id: Optional[str] = None) -> dict:
        """Start a financing application. Returns at least
        {"application_id": ..., "status": ..., "demo": bool}."""


# ---------- demo provider ----------

#: Demo rate card: (term_months, APR percent). 0% APR on 12 months is the
#: "same-as-cash" promotional line — the demo makes sure the math path for it
#: is exercised.
DEMO_TERMS: Sequence[tuple] = (
    (12, 0.00),
    (24, 7.99),
    (36, 9.99),
    (48, 11.99),
    (60, 12.99),
)

#: Demo APRs only apply between these bounds; outside them the demo declines
#: (a real provider would price risk itself — see WisetackProvider below).
DEMO_MIN_AMOUNT_CENTS = 1_000   # $10
DEMO_MAX_AMOUNT_CENTS = 2_500_000  # $25,000 — Wisetack's headline loan cap


class DemoFinancingProvider(FinancingProvider):
    """Clearly-labeled sandbox provider: deterministic offers, real math,
    mock applications. Nothing here touches money or a network."""

    name = "demo"

    def get_offers(self, amount_cents: int,
                   term_options: Optional[Sequence[int]] = None) -> List[FinancingOffer]:
        if amount_cents < DEMO_MIN_AMOUNT_CENTS:
            raise FinancingError(
                f"Demo financing needs at least ${DEMO_MIN_AMOUNT_CENTS/100:.2f}")
        if amount_cents > DEMO_MAX_AMOUNT_CENTS:
            raise FinancingError(
                f"Demo financing caps at ${DEMO_MAX_AMOUNT_CENTS/100:,.0f} (Wisetack's headline cap)")
        terms = list(term_options) if term_options else [t for t, _ in DEMO_TERMS]
        apr_by_term = {t: a for t, a in DEMO_TERMS}
        offers = []
        for term in terms:
            if term not in apr_by_term:
                raise FinancingError(f"Demo does not offer a {term}-month term")
            apr = apr_by_term[term]
            offers.append(FinancingOffer(
                term_months=term,
                apr_percent=apr,
                monthly_payment_cents=monthly_payment_cents(amount_cents, apr, term),
                provider_name=self.name,
                demo=True,
            ))
        return offers

    async def create_application(self, *, amount_cents: int, term_months: int,
                                 customer: dict, quote_id: Optional[str] = None,
                                 invoice_id: Optional[str] = None) -> dict:
        # No network: mint a mock application so staff can demo the full flow.
        import uuid as _uuid
        return {
            "application_id": f"demo-app-{_uuid.uuid4().hex[:12]}",
            "status": "demo_pending",
            "provider_name": self.name,
            "demo": True,
            "term_months": term_months,
            "amount_cents": amount_cents,
            "note": ("Demo application only — no credit check was run and no "
                     "lender was contacted."),
        }


# ---------- Wisetack skeleton (live target) ----------

class WisetackProvider(FinancingProvider):
    """Skeleton for the live provider. Wisetack's model: the shop holds its own
    Wisetack merchant account; we call Wisetack's API with that shop's API key
    (same shape as Stripe Connect — per-tenant credentials, no platform key).

    Every live path raises FinancingNotConfigured until the credential plumbing
    lands. When it does, replace each TODO(LIVE) block with the real call and
    add `api_key`/`merchant_id` handling from the tenant's financing settings.
    """

    name = "wisetack"

    def __init__(self, *, merchant_id: Optional[str] = None, api_key: Optional[str] = None):
        # TODO(LIVE): api_key arrives from encrypted per-tenant storage (not a
        # plaintext DB field). merchant_id identifies the shop's own Wisetack
        # merchant account.
        self.merchant_id = merchant_id
        self._api_key = api_key  # keep off the wire; never log or serialize

    def _require_live(self) -> None:
        # TODO(LIVE): replace with a credentials-present check, e.g.
        #   if not (self.merchant_id and self._api_key): raise ...
        raise FinancingNotConfigured(
            "Wisetack is not connected for this shop yet. Connect your own "
            "Wisetack merchant account in Settings → Financing to go live.")

    def get_offers(self, amount_cents: int,
                   term_options: Optional[Sequence[int]] = None) -> List[FinancingOffer]:
        # TODO(LIVE): Wisetack pre-qualification returns real offers for the
        # customer. Until then, shops see the demo math rather than fake live
        # rates — never present demo numbers as Wisetack's.
        # Real call sketch:
        #   resp = POST https://api.wisetack.com/v1/prequalifications
        #          Authorization: Bearer <shop api key>
        #          body: {amount_cents, merchant_id, term_options}
        #   offers = [FinancingOffer(term_months=o["term"], apr_percent=o["apr"],
        #                            monthly_payment_cents=o["monthly_cents"],
        #                            provider_name="wisetack", demo=False)
        #             for o in resp["offers"]]
        self._require_live()

    async def create_application(self, *, amount_cents: int, term_months: int,
                                 customer: dict, quote_id: Optional[str] = None,
                                 invoice_id: Optional[str] = None) -> dict:
        # TODO(LIVE): Wisetack's flow is merchant-initiated — POST
        # https://api.wisetack.com/v1/loan_applications with the shop's key,
        # get back an application id + a hosted checkout URL the customer
        # completes. Store the returned id on the financing_applications doc.
        self._require_live()


# ---------- provider registry ----------

def get_provider(name: str, **kwargs) -> FinancingProvider:
    """Resolve a provider by the tenant's configured name."""
    if name == "demo":
        return DemoFinancingProvider()
    if name == "wisetack":
        return WisetackProvider(**kwargs)
    raise FinancingError(f"Unknown financing provider: {name!r}")
