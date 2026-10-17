"""Live verification for tenant integration configs.

An integration may only be reported as "connected" after a real check
against the provider succeeds. Saving a complete-looking config is NOT
proof of connectivity (wrong tokens, revoked access and typos all "look"
complete). This module owns the per-provider checks:

    * twilio — real check: GET the account resource with the tenant's
      SID + auth token (basic auth). 200 = credentials valid.
    * gmail / google_my_business — OAuth providers: the saved fields are
      an OAuth client (client_id/secret), not a grant. There is no token
      to validate until the tenant completes an OAuth flow, so there is
      NO live check to run here — verify_integration returns None and the
      caller must use the honest "configured" state, never "connected".
    * stripe — platform-provisioned; no per-tenant credential exists to
      verify from this router. Also returns None ("configured").

`verify_integration(key, config)` returns:
    None                    → no live check exists for this provider
    (True, detail)          → check ran and passed
    (False, detail)         → check ran and failed (detail is user-safe)

Checks never raise: network/auth/API failures come back as (False, ...).
"""
from __future__ import annotations

import logging
from typing import Optional, Tuple

import httpx

log = logging.getLogger("integration_verify")

HTTP_TIMEOUT_SECONDS = 5.0


async def _verify_twilio(config: dict) -> Tuple[bool, str]:
    sid = (config or {}).get("account_sid") or ""
    token = (config or {}).get("auth_token") or ""
    if not sid or not token:
        return False, "Twilio account SID and auth token are required before verification can run."
    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}.json"
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            resp = await client.get(url, auth=(sid, token))
    except httpx.TimeoutException:
        return False, f"Twilio API timed out after {HTTP_TIMEOUT_SECONDS:.0f}s — check your connection and try again."
    except httpx.HTTPError as e:
        return False, f"Could not reach the Twilio API: {e}"
    if resp.status_code == 200:
        try:
            friendly = (resp.json() or {}).get("friendly_name") or sid
        except Exception:
            friendly = sid
        return True, f"Twilio account verified ({friendly})."
    if resp.status_code in (401, 403):
        return False, ("Twilio rejected these credentials (HTTP "
                       f"{resp.status_code}) — check the account SID and auth token, then reconnect.")
    return False, f"Twilio API returned HTTP {resp.status_code} — verification could not be completed."


_VERIFIERS = {
    "twilio": _verify_twilio,
    # gmail / google_my_business / stripe: intentionally absent — no live
    # check exists for the saved fields, so they can never be "connected"
    # from config alone (see module docstring).
}


def has_verifier(key: str) -> bool:
    return key in _VERIFIERS


async def verify_integration(key: str, config: dict) -> Optional[Tuple[bool, str]]:
    """Run the provider's live check. None = no check exists for `key`."""
    fn = _VERIFIERS.get(key)
    if fn is None:
        return None
    try:
        return await fn(config or {})
    except Exception as e:  # a verifier bug must not 500 the save flow
        log.warning("integration verification crashed for %s: %s", key, e)
        return False, f"Verification could not be completed: {e}"
