"""Telephony provisioning: provider-abstracted auto-provisioning of a business's phones.

A new shop either ports in their existing main line, gets a freshly provisioned
number, or both (the typical case: keep the familiar main line + add a dedicated
AI/marketing line). The platform parent account owns subaccounts per tenant; the
number model matches the approved bundled fee model: telephony is included in the
flat platform fee with a generous usage allowance; overages are billed through
(recorded here as usage, no charging logic).

Implementations:
- ``MockTelephonyProvider``: deterministic, zero-network. Same tenant always gets
  the same subaccount SID and the same numbers (hash-derived), so tests and demos
  are reproducible.
- ``TwilioSubaccountProvider``: TODO(LIVE) skeleton. The real implementation
  creates a subaccount under the platform parent account (TWILIO_PARENT_SID /
  TWILIO_PARENT_TOKEN), purchases/provisions numbers via the API, and submits a
  port-in request for existing numbers. Not wired to any network path yet.
"""
from __future__ import annotations

import hashlib
import logging
import os
from abc import ABC, abstractmethod
from typing import Dict, List, Optional

logger = logging.getLogger("telephony")

# Bundled fee model: telephony is included in the flat platform fee.
FEE_MODEL = {
    "plan": "bundled_flat_fee",
    "notes": "Telephony included in the flat platform fee with a generous allowance; "
    "overages are billed through (usage recorded, no charging logic).",
    "usage_allowance": {"sms_segments": 1000, "voice_minutes": 500},
}

# Fake pool of provisionable numbers for the mock provider.
_MOCK_NUMBER_POOL = [
    "+15550010001", "+15550010002", "+15550010003", "+15550010004",
    "+15550010005", "+15550010006", "+15550010007", "+15550010008",
]

MODES = ("provisioned", "ported", "both")


class TelephonyProvider(ABC):
    """Abstract telephony provider: subaccount provisioning + number allocation."""

    name: str = "base"

    @abstractmethod
    async def provision(
        self,
        *,
        tenant_id: str,
        tenant_name: str,
        mode: str,
        area_code: Optional[str] = None,
        port_numbers: Optional[List[str]] = None,
        count: int = 1,
    ) -> Dict:
        """Provision telephony for a tenant.

        Returns a dict with ``provider``, ``subaccount_sid``, ``numbers`` (list
        of {number, type: provisioned|ported, status}), and ``mode``.
        """
        raise NotImplementedError


def _deterministic_sid(tenant_id: str, prefix: str = "AC") -> str:
    """Deterministic subaccount SID: same tenant always maps to the same SID."""
    digest = hashlib.sha256(f"momo-telephony-subaccount:{tenant_id}".encode()).hexdigest()
    return f"{prefix}mock{digest[:24]}"


def _deterministic_number(tenant_id: str, index: int, area_code: Optional[str]) -> str:
    """Deterministically pick a number from the fake pool."""
    pool = list(_MOCK_NUMBER_POOL)
    if area_code:
        preferred = [n for n in pool if n[2:5] == area_code.strip()[1:4] if area_code.strip()]
        pool = preferred or pool
    digest = hashlib.sha256(f"momo-telephony-number:{tenant_id}:{index}".encode()).hexdigest()
    return pool[int(digest, 16) % len(pool)]


class MockTelephonyProvider(TelephonyProvider):
    """Zero-network deterministic provider (default for demos, tests, and CI)."""

    name = "mock"

    async def provision(
        self,
        *,
        tenant_id: str,
        tenant_name: str,
        mode: str,
        area_code: Optional[str] = None,
        port_numbers: Optional[List[str]] = None,
        count: int = 1,
    ) -> Dict:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        subaccount_sid = _deterministic_sid(tenant_id)
        numbers: List[Dict] = []
        if mode in ("provisioned", "both"):
            for i in range(max(1, count)):
                numbers.append({
                    "number": _deterministic_number(tenant_id, i, area_code),
                    "type": "provisioned",
                    "status": "active",
                })
        if mode in ("ported", "both"):
            for n in port_numbers or []:
                numbers.append({"number": n, "type": "ported", "status": "port_requested"})
        result = {
            "provider": self.name,
            "subaccount_sid": subaccount_sid,
            "numbers": numbers,
            "mode": mode,
            "tenant_name": tenant_name,
        }
        logger.info("[telephony mock] provisioned tenant=%s sid=%s mode=%s numbers=%d",
                    tenant_id, subaccount_sid, mode, len(numbers))
        return result


class TwilioSubaccountProvider(TelephonyProvider):
    """TODO(LIVE) skeleton: real Twilio subaccount + number provisioning.

    Live implementation plan (not yet wired to the network):
      1. ``twilio.rest.Client(TWILIO_PARENT_SID, TWILIO_PARENT_TOKEN)`` against the
         platform parent account.
      2. ``client.api.accounts.create(friendly_name=<tenant name>)`` -> subaccount SID.
      3. Purchase/provision: search ``client.available_phone_numbers("US").local``
         by area_code, then ``client.incoming_phone_numbers.create(phone_number=...)``
         under the subaccount.
      4. Port-in: submit a port request via the Hosted Numbers / Portability API for
         each existing number (stub below), then flip the number status to active
         once the port completes (webhook).
    """

    name = "twilio"

    def __init__(self) -> None:
        self.parent_sid = os.environ.get("TWILIO_PARENT_SID") or ""
        self.parent_token = os.environ.get("TWILIO_PARENT_TOKEN") or ""
        if not (self.parent_sid and self.parent_token):
            raise RuntimeError(
                "TODO(LIVE): TwilioSubaccountProvider requires TWILIO_PARENT_SID "
                "and TWILIO_PARENT_TOKEN"
            )

    async def provision(
        self,
        *,
        tenant_id: str,
        tenant_name: str,
        mode: str,
        area_code: Optional[str] = None,
        port_numbers: Optional[List[str]] = None,
        count: int = 1,
    ) -> Dict:
        # TODO(LIVE): create the subaccount under the parent account, then
        # purchase/provision numbers and/or submit port-in requests. Kept as a
        # skeleton so no network path exists until the live credentials are wired.
        raise NotImplementedError(
            "TODO(LIVE): Twilio subaccount provisioning is not implemented yet. "
            "See TwilioSubaccountProvider docstring for the implementation plan."
        )


def get_telephony_provider() -> TelephonyProvider:
    """Provider factory: real Twilio path only when parent creds are configured.

    Default is the deterministic mock. The Twilio skeleton raises
    NotImplementedError on provision until the TODO(LIVE) work is done, so even
    with creds set there is no network path yet.
    """
    try:
        return TwilioSubaccountProvider()
    except RuntimeError:
        return MockTelephonyProvider()
