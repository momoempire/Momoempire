"""UTC helpers for stored timestamps (EMP-W-CF-026).

Expiry fields (password_reset_tokens, user_sessions, portal_tokens) are STORED as timezone-aware
UTC datetimes, which MongoDB keeps as BSON dates (the TTL indexes on expires_at need real dates).
pymongo/Motor return BSON dates as NAIVE datetimes (tz_aware=False is the default), and comparing
naive with aware raises TypeError, so every expiry comparison goes through as_utc() first.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def as_utc(value) -> Optional[datetime]:
    """Return `value` as an aware UTC datetime, or None if it isn't a usable timestamp.

    - aware datetime: converted to UTC
    - naive datetime (what MongoDB hands back): treated as UTC
    - ISO 8601 string (older rows / string storage), "Z" accepted: parsed, naive treated as UTC
    - anything else (None, numbers, garbage): None
    """
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def is_expired(value, now: Optional[datetime] = None) -> bool:
    """True if `value` is in the past OR unreadable (fail closed: a token with a missing or
    malformed expiry is treated as expired)."""
    exp = as_utc(value)
    if exp is None:
        return True
    return exp < (now or datetime.now(timezone.utc))
