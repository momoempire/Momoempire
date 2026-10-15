"""Outbound voice AI — provider abstraction, script templates, rendering, consent.

Voice is provider-abstracted (same pattern as services.marketing_automation):
  - VoiceProvider ABC ............ contract for placing a call.
  - MockVoiceProvider ............ records calls in-memory, deterministic
                                   outcomes, ZERO network. Used in tests and
                                   as the default until live creds exist.
  - TwilioVoiceProvider ........... TODO(LIVE) skeleton; reuses the per-tenant
                                   config in services.twilio (needs TWILIO_*
                                   creds + a TwiML bin/webhook).

FCC consent guard: campaigns may only call numbers whose customer record has
voice_consent=True. Unknown numbers are treated as no-consent for proactive
campaigns. Missed-call callbacks allow unknown numbers (the caller reached out
first — implied consent) but still refuse an explicit opt-out.

Call templates live here as constants (id -> {name, use_case, script, variables})
so they are versioned with the code; tenants render them with per-customer
variables at campaign time.
"""
from __future__ import annotations

import hashlib
import logging
import re
from abc import ABC, abstractmethod
from typing import Any, Optional

logger = logging.getLogger("outbound_voice")

# ---------------------------------------------------------------------------
# Script templates ({{variable}} placeholders)
# ---------------------------------------------------------------------------

TEMPLATES: dict[str, dict] = {
    "missed_call_callback": {
        "id": "missed_call_callback",
        "name": "Missed-call callback (60s)",
        "use_case": "missed_call_callback",
        "variables": ["customer_name", "shop_name", "callback_number"],
        "script": (
            "Hi {{customer_name}}, this is {{shop_name}} — we missed your call and "
            "wanted to catch you right away. You can also reach us at "
            "{{callback_number}}. Are you still looking for help today?"
        ),
    },
    "overdue_invoice": {
        "id": "overdue_invoice",
        "name": "Overdue invoice reminder",
        "use_case": "overdue_invoice",
        "variables": ["customer_name", "shop_name", "amount", "days_overdue",
                      "payment_link"],
        "script": (
            "Hi {{customer_name}}, this is {{shop_name}} with a friendly reminder "
            "that your invoice for {{amount}} is {{days_overdue}} days past due. "
            "You can pay securely at {{payment_link}}. Press 1 to pay now, or "
            "press 2 and we'll connect you with our office."
        ),
    },
    "reschedule_nudge": {
        "id": "reschedule_nudge",
        "name": "Reschedule nudge",
        "use_case": "reschedule_nudge",
        "variables": ["customer_name", "shop_name", "old_date", "new_window"],
        "script": (
            "Hi {{customer_name}}, this is {{shop_name}}. Your appointment on "
            "{{old_date}} needs to move — we can fit you in {{new_window}}. "
            "Reply yes to confirm, or press 1 and we'll find another time."
        ),
    },
    "tuneup_campaign": {
        "id": "tuneup_campaign",
        "name": "Seasonal tune-up offer",
        "use_case": "tuneup_campaign",
        "variables": ["customer_name", "shop_name", "season", "offer",
                      "booking_link"],
        "script": (
            "Hi {{customer_name}}, this is {{shop_name}} with a {{season}} "
            "reminder: {{offer}}. Book your tune-up at {{booking_link}} — "
            "press 1 and we'll get you scheduled today."
        ),
    },
}

_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def render_script(script: str, variables: dict[str, Any]) -> str:
    """Fill {{variables}} in a template script. Raises ValueError on missing."""

    def _fill(match: re.Match) -> str:
        key = match.group(1)
        if key not in variables or variables[key] is None:
            raise ValueError(f"missing variable: {{{{{key}}}}}")
        return str(variables[key])

    return _VAR_RE.sub(_fill, script)


def template_variables(template_id: str) -> list[str]:
    tpl = TEMPLATES.get(template_id)
    if not tpl:
        raise ValueError(f"unknown template: {template_id}")
    return list(tpl["variables"])


# ---------------------------------------------------------------------------
# Voice provider abstraction
# ---------------------------------------------------------------------------

class VoiceProvider(ABC):
    @abstractmethod
    async def place_call(self, *, tenant_id: str, to: str, script: str,
                         call_id: str, metadata: Optional[dict] = None) -> dict:
        """Place one outbound call. Returns {sid, status, demo}."""


class MockVoiceProvider(VoiceProvider):
    """Zero-network mock: records calls in-memory with deterministic outcomes.

    Outcome is derived from a stable hash of the phone number so repeated test
    runs agree: ends in 0-5 -> answered, 6-7 -> voicemail, 8-9 -> no_answer.
    Duration is deterministic in [45, 240] seconds.
    """

    def __init__(self) -> None:
        self.recorded: list[dict] = []

    def reset(self) -> None:
        self.recorded.clear()

    @staticmethod
    def _outcome_for(phone: str) -> tuple[str, int]:
        digest = hashlib.sha256(phone.encode()).hexdigest()
        tail = int(digest[-1], 16) % 10
        if tail <= 5:
            outcome = "answered"
        elif tail <= 7:
            outcome = "voicemail"
        else:
            outcome = "no_answer"
        duration = 45 + (int(digest[:4], 16) % 196)  # 45..240s
        return outcome, duration

    async def place_call(self, *, tenant_id: str, to: str, script: str,
                         call_id: str, metadata: Optional[dict] = None) -> dict:
        outcome, duration = self._outcome_for(to)
        record = {
            "sid": f"CA_mock_{call_id[:8]}",
            "status": "completed" if outcome == "answered" else "no-answer",
            "demo": True,
            "outcome": outcome,
            "duration_seconds": duration,
            "transcript": "[demo transcript unavailable — voice mock]",
        }
        self.recorded.append({
            "tenant_id": tenant_id, "to": to, "script": script,
            "call_id": call_id, "metadata": metadata or {}, **record,
        })
        logger.info("[voice-mock tenant=%s to=%s] %s %ss",
                    tenant_id, to, outcome, duration)
        return record


class TwilioVoiceProvider(VoiceProvider):
    """TODO(LIVE): Twilio voice skeleton.

    Reads per-tenant credentials via services.twilio (_tenant_twilio_cfg:
    TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_FROM_NUMBER or
    TWILIO_MESSAGING_SID, overridable per-tenant in the `integrations`
    collection). Before calling, point Twilio at a TwiML bin or webhook that
    speaks the rendered script, then replace this raise with
    client.calls.create(to=..., from_=..., twiml=...).
    """

    async def place_call(self, *, tenant_id: str, to: str, script: str,
                         call_id: str, metadata: Optional[dict] = None) -> dict:
        try:
            from services.twilio import _tenant_twilio_cfg
        except Exception as e:  # pragma: no cover - defensive
            raise RuntimeError(f"live voice unavailable: {e}")
        cfg = await _tenant_twilio_cfg(tenant_id)
        if not (cfg.get("account_sid") and cfg.get("auth_token")
                and (cfg.get("from_number") or cfg.get("messaging_sid"))):
            raise RuntimeError(
                "live voice unavailable: TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / "
                "TWILIO_FROM_NUMBER (or TWILIO_MESSAGING_SID) not configured")
        # TODO(LIVE): build the Twilio Client here and calls.create(...).
        raise NotImplementedError(
            "Twilio voice live path is a skeleton — wire TwiML before enabling.")


_mock_provider: Optional[MockVoiceProvider] = None


def get_voice_provider(tenant_id: Optional[str] = None) -> VoiceProvider:
    """Default to the mock. Set VOICE_PROVIDER=live (plus TWILIO_* creds)
    to select the Twilio skeleton. Env is read lazily for testability."""
    import os
    if os.environ.get("VOICE_PROVIDER", "mock").lower() == "live":
        return TwilioVoiceProvider()
    global _mock_provider
    if _mock_provider is None:
        _mock_provider = MockVoiceProvider()
    return _mock_provider


def reset_mock_provider() -> MockVoiceProvider:
    global _mock_provider
    _mock_provider = MockVoiceProvider()
    return _mock_provider


# ---------------------------------------------------------------------------
# Consent guard
# ---------------------------------------------------------------------------

async def customer_consent(db, tenant_id: str, *, customer_id=None,
                           phone: str = "") -> Optional[bool]:
    """Return voice_consent for a customer, or None if the customer is unknown.

    Looks up by customer id first, then by phone number within the tenant.
    """
    query = {"tenant_id": tenant_id}
    if customer_id:
        query["id"] = customer_id
    elif phone:
        query["phone"] = phone
    else:
        return None
    doc = await db.customers.find_one(query, {"_id": 0, "voice_consent": 1})
    if not doc:
        return None
    return bool(doc.get("voice_consent"))


# ---------------------------------------------------------------------------
# Call logistics
# ---------------------------------------------------------------------------

EST_MINUTES_PER_CALL = 3.0  # planning estimate; actual billing per provider


def estimate_minutes(num_calls: int) -> float:
    return round(num_calls * EST_MINUTES_PER_CALL, 2)


def planned_call_order(audience: list[dict]) -> list[dict]:
    """Priority order for a launch batch.

    Entries with priority="high" (e.g. missed-call callbacks) go first,
    preserving submission order within each priority class.
    """
    return sorted(audience, key=lambda m: 0 if m.get("priority") == "high" else 1)
