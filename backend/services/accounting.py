"""Accounting provider abstraction: push customers, invoices and payments to
an external bookkeeping system.

Product decision (user, 2026-10-15): **NO QuickBooks integration.** Momo
links to the user's OWN bookkeeping app (the QuickBooks competitor). Shops
that want QuickBooks keep using it separately — there is deliberately no
link for that here.

Design:
    * `AccountingProvider` ABC is the contract every provider implements.
    * `MindAtEaseProvider` talks to the user's bookkeeping app. It speaks the
      app's real endpoints today (POST /customers, POST /invoices,
      POST /invoices/{id}/pay, GET /auth/me) with a Bearer token, and it is
      written against a documented minimal receiving contract so the
      bookkeeping app can later expose long-lived API keys / an idempotent
      partner sync endpoint without Momo changing shape.
    * Every push takes an idempotency key (derived by the caller from
      tenant + local record + step). Retries with the same key must never
      create duplicates. The key is sent as an `Idempotency-Key` header
      (harmless until the bookkeeping app honors it) and the caller records
      provider record ids locally to skip already-pushed steps.
    * Providers NEVER raise into the invoice-paid flow: they raise
      `AccountingError`, and the sync helper logs + records the failure.

Money: Momo stores integer cents; the bookkeeping app takes dollars as
floats on its invoice/pay endpoints, so conversion happens at the edge.
"""
from __future__ import annotations

import hashlib
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

log = logging.getLogger("accounting")

# The books app serves its API only under /api (INTEGRATION_CONTRACT.md
# §0). The pre-audit default omitted it, so a tenant who accepted the
# default got 404s on every sync call and could never connect (Track E
# finding D-4; fixed by the financial-sync fix track, 2026-10-17).
DEFAULT_API_BASE_URL = "https://bookkeeping-api-ymh6.onrender.com/api"
HTTP_TIMEOUT_SECONDS = 10.0


class AccountingError(Exception):
    """Anything the provider could not do (network, auth, API error).

    `kind` classifies the failure for connection-health reporting:
      config    — no credential configured on the Momo side
      auth      — the bookkeeping API rejected the credential (401)
      forbidden — authenticated but not permitted (403)
      revoked   — the bookkeeping API reports the credential revoked
      http      — any other HTTP error status
      timeout / network / bad_response — transport-level failures
    """

    def __init__(self, message: str, *, kind: str = "error"):
        super().__init__(message)
        self.kind = kind
        # Populated by MindAtEaseProvider._request for HTTP failures so
        # callers can act on structured error bodies (e.g. the books
        # natural-dedup 409 carries the existing record's id).
        self.status_code: Optional[int] = None
        self.response_body: Any = None


# ---------- value objects ----------

@dataclass(frozen=True)
class SyncCustomer:
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None


@dataclass(frozen=True)
class SyncInvoiceLine:
    name: str
    amount_cents: int
    revenue_code: str = "4020"  # bookkeeping app's default service revenue code


@dataclass(frozen=True)
class SyncInvoice:
    customer_name: str
    lines: List[SyncInvoiceLine]
    total_cents: int
    title: Optional[str] = None
    customer_provider_id: Optional[str] = None
    due_date: Optional[str] = None  # YYYY-MM-DD
    terms: str = "Net 30"


@dataclass(frozen=True)
class SyncPayment:
    invoice_provider_id: str
    amount_cents: int
    method: str = "card"  # bookkeeping app payment_method (bank/card/cash/...)


@dataclass(frozen=True)
class SyncRefund:
    invoice_provider_id: str
    amount_cents: int  # the refund increment to post (not a cumulative total)
    method: str = "card"  # bookkeeping app payment_method (bank/card/cash/...)


def idempotency_key(*parts: str) -> str:
    """Deterministic idempotency key from tenant + local record + step."""
    h = hashlib.sha256()
    for p in parts:
        h.update(str(p).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def _existing_id_from_error(err: AccountingError) -> Optional[str]:
    """The existing record id from a books natural-dedup 409, if any.

    Books answers a duplicate customer create with 409 and a detail
    object {"message": "Customer already exists", "existing_id": ...}
    (FastAPI wraps it as {"detail": {...}}). Returns None for every
    other error — in particular for the idempotency payload-mismatch
    409, whose detail is a plain string with no id to adopt.
    """
    if getattr(err, "status_code", None) != 409:
        return None
    body = getattr(err, "response_body", None)
    if not isinstance(body, dict):
        return None
    detail = body.get("detail")
    if isinstance(detail, dict) and detail.get("existing_id"):
        return str(detail["existing_id"])
    if body.get("existing_id"):
        return str(body["existing_id"])
    return None


def _body_signals_revoked(resp) -> bool:
    """True when an error response body says the credential was revoked.

    CONTRACT ASSUMPTION (books-side integration credentials are being
    built in parallel — see the audit report): a revoked credential is
    signalled either by a machine-readable code ("credential_revoked"
    in a `code`/`error` field) or by the word "revoked" in the detail
    text of a 401/403 response. If the final books contract uses a
    different signal, this one function is the only place to update.
    """
    try:
        data = resp.json()
    except Exception:
        data = None
    if isinstance(data, dict):
        for field in ("code", "error", "detail", "message"):
            value = data.get(field)
            if isinstance(value, str) and "revoked" in value.lower():
                return True
    text = (getattr(resp, "text", "") or "").lower()
    return "revoked" in text


# ---------- provider ABC ----------

class AccountingProvider(ABC):
    """Contract every accounting provider implements."""

    name: str = "base"

    @abstractmethod
    async def test_connection(self) -> Dict[str, Any]:
        """Validate credentials. Returns provider info; raises AccountingError."""

    @abstractmethod
    async def push_customer(self, customer: SyncCustomer, *, idempotency_key: str) -> str:
        """Create/update the customer remotely. Returns the provider record id."""

    @abstractmethod
    async def push_invoice(self, invoice: SyncInvoice, *, idempotency_key: str) -> str:
        """Issue the invoice remotely. Returns the provider invoice id."""

    @abstractmethod
    async def push_payment(self, payment: SyncPayment, *, idempotency_key: str) -> str:
        """Record the payment remotely. Returns the provider payment/entry id."""

    async def push_refund(self, refund: SyncRefund, *, idempotency_key: str) -> str:
        """Record a payment refund remotely. Returns the provider entry id.

        Not abstract: providers without a refund surface inherit this
        refusal; MindAtEaseProvider implements it against the books
        app's sync-surface refund endpoint (INTEGRATION_CONTRACT_ADDENDUM.md).
        """
        raise AccountingError(
            f"Provider {self.name} does not support refund sync", kind="config")


# ---------- the user's own bookkeeping app ----------

class MindAtEaseProvider(AccountingProvider):
    """Provider for the user's OWN bookkeeping app (the QuickBooks competitor).

    Auth: a Bearer credential stored per tenant (encrypted at rest in
    routers/bookkeeping_link.py). Two kinds are accepted by this client:
      * a dedicated INTEGRATION credential issued by the bookkeeping app
        (long-lived, scoped to sync, revocable/rotatable — the target
        state), and
      * a LEGACY owner JWT pasted from the bookkeeping app (POST
        /auth/login; ~7-day expiry) — still supported so existing
        tenants keep syncing; the link layer flags it for migration.
    Errors carry a `kind` (auth/forbidden/revoked/network/...) that the
    link layer turns into connection health: healthy/degraded/revoked.
    """

    name = "mindatease"

    def __init__(self, api_base_url: str = DEFAULT_API_BASE_URL,
                 api_token: str = "", timeout: float = HTTP_TIMEOUT_SECONDS):
        self.base_url = (api_base_url or DEFAULT_API_BASE_URL).rstrip("/")
        self.api_token = api_token or ""
        self.timeout = timeout

    def _headers(self, idem: Optional[str] = None) -> Dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
            "User-Agent": "momo-bookkeeping-link/1.0",
        }
        if idem:
            # Honored server-side by the bookkeeping app since the
            # 2026-10-17 audit (INTEGRATION_CONTRACT.md §3): duplicates
            # replay the recorded response, payload mismatches 409.
            headers["Idempotency-Key"] = idem
        return headers

    async def _request(self, method: str, path: str,
                       json: Optional[dict] = None,
                       idem: Optional[str] = None) -> Any:
        if not self.api_token:
            raise AccountingError("No bookkeeping-app API token configured", kind="config")
        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.request(method, url, headers=self._headers(idem), json=json)
        except httpx.TimeoutException as e:
            raise AccountingError(f"Bookkeeping API timed out ({self.timeout}s): {e}", kind="timeout")
        except httpx.HTTPError as e:
            raise AccountingError(f"Bookkeeping API unreachable: {e}", kind="network")
        if resp.status_code in (401, 403) and _body_signals_revoked(resp):
            # The books side revoked this integration credential. This is
            # terminal until a new credential is connected — the health
            # layer pauses sync instead of hammering a dead credential.
            raise AccountingError(
                "Bookkeeping API reports this integration credential revoked — "
                "reconnect the bookkeeping link with a new credential",
                kind="revoked")
        if resp.status_code == 401:
            raise AccountingError("Bookkeeping API rejected the token (401) — it may have expired; re-enter it in settings", kind="auth")
        if resp.status_code == 403:
            raise AccountingError("Bookkeeping API refused (403) — the linked user needs the owner role", kind="forbidden")
        if resp.status_code >= 400:
            body = (resp.text or "")[:200]
            err = AccountingError(
                f"Bookkeeping API error {resp.status_code}: {body}", kind="http")
            err.status_code = resp.status_code
            try:
                err.response_body = resp.json()
            except Exception:
                err.response_body = None
            raise err
        try:
            return resp.json()
        except Exception as e:
            raise AccountingError(f"Bookkeeping API returned non-JSON: {e}", kind="bad_response")

    async def test_connection(self) -> Dict[str, Any]:
        # Track E reconciliation fix (audit 2026-10-17): integration
        # credentials (mae1.*) are NOT user JWTs — books rejects them on
        # /auth/me by design (INTEGRATION_CONTRACT.md §1.3/§2). Their
        # identity/health endpoint is GET /integrations/me (§1.4), whose
        # response carries no user `id`. Branch on the token format,
        # exactly as the contract's §4.2 prescribes; legacy JWTs keep
        # the /auth/me path.
        if self.api_token.startswith("mae1."):
            me = await self._request("GET", "/integrations/me")
            if not isinstance(me, dict) or not me.get("ok"):
                raise AccountingError("Bookkeeping API /integrations/me returned an unexpected response")
            return {
                "ok": True,
                "user_name": me.get("label"),
                "user_email": None,
                "tenant_name": me.get("tenant_name"),
                "client_id": me.get("client_id"),
                "expires_at": me.get("expires_at"),
            }
        me = await self._request("GET", "/auth/me")
        if not isinstance(me, dict) or not me.get("id"):
            raise AccountingError("Bookkeeping API /auth/me returned an unexpected response")
        return {
            "ok": True,
            "user_name": me.get("name"),
            "user_email": me.get("email"),
            "tenant_name": (me.get("tenant") or {}).get("name"),
        }

    async def push_customer(self, customer: SyncCustomer, *, idempotency_key: str) -> str:
        payload = {"name": customer.name}
        if customer.email:
            payload["email"] = customer.email
        if customer.phone:
            payload["phone"] = customer.phone
        try:
            doc = await self._request("POST", "/customers", json=payload, idem=idempotency_key)
        except AccountingError as e:
            # Track E Confirmed Broken #1 (fixed here): books' natural
            # dedup answers a repeat customer's push with 409 "Customer
            # already exists" and the EXISTING record's id in the body.
            # The customer step is keyed per invoice, so a repeat
            # customer's later invoices always take this path. Adopt the
            # existing id — that is the dedup contract's whole point —
            # instead of failing the entire invoice sync. Any other 409
            # (e.g. the idempotency payload-mismatch, whose body carries
            # no existing_id) stays a hard error per contract §4.3.
            existing = _existing_id_from_error(e)
            if existing:
                log.info("bookkeeping customer push adopted existing id "
                         "(natural dedup): %s", existing)
                return existing
            raise
        cid = (doc or {}).get("id")
        if not cid:
            raise AccountingError("Bookkeeping API /customers returned no id")
        return cid

    async def push_invoice(self, invoice: SyncInvoice, *, idempotency_key: str) -> str:
        lines = [
            {"name": ln.name, "amount": round(ln.amount_cents / 100, 2),
             "revenue_code": ln.revenue_code}
            for ln in invoice.lines
        ]
        payload = {
            "client_name": invoice.customer_name,
            "lines": lines,
            "tax": 0,
            "terms": invoice.terms,
        }
        if invoice.customer_provider_id:
            payload["customer_id"] = invoice.customer_provider_id
        if invoice.due_date:
            payload["due_date"] = invoice.due_date
        doc = await self._request("POST", "/invoices", json=payload, idem=idempotency_key)
        iid = (doc or {}).get("id")
        if not iid:
            raise AccountingError("Bookkeeping API /invoices returned no id")
        return iid

    async def push_payment(self, payment: SyncPayment, *, idempotency_key: str) -> str:
        payload = {
            "payment_method": payment.method,
            "amount": round(payment.amount_cents / 100, 2),
        }
        doc = await self._request(
            "POST", f"/invoices/{payment.invoice_provider_id}/pay", json=payload, idem=idempotency_key)
        entry = (doc or {}).get("entry_number")
        if entry is None:
            raise AccountingError("Bookkeeping API /invoices/{id}/pay returned no entry number")
        return str(entry)

    async def push_refund(self, refund: SyncRefund, *, idempotency_key: str) -> str:
        payload = {
            "payment_method": refund.method,
            "amount": round(refund.amount_cents / 100, 2),
        }
        doc = await self._request(
            "POST", f"/invoices/{refund.invoice_provider_id}/refund",
            json=payload, idem=idempotency_key)
        entry = (doc or {}).get("entry_number")
        if entry is None:
            raise AccountingError("Bookkeeping API /invoices/{id}/refund returned no entry number")
        return str(entry)


def get_provider(name: str, **kwargs) -> AccountingProvider:
    """Resolve a provider by name. Only 'mindatease' ships today."""
    if name == "mindatease":
        return MindAtEaseProvider(**kwargs)
    raise AccountingError(f"Unknown accounting provider: {name}")
