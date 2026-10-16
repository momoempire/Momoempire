"""TOTP two-factor authentication helpers.

Secrets are encrypted at rest with Fernet. Key resolution:
  TWO_FACTOR_ENCRYPTION_KEY, else BOOKKEEPING_LINK_ENCRYPTION_KEY (shared vault).
Fail closed: no key configured -> EncryptionNotConfigured (setup returns 503
telling the operator what to set), never plaintext storage.

Backup codes: 10 single-use codes, SHA-256 hashed in the DB, shown once at
enrollment. Verification accepts a TOTP code OR an unused backup code.
"""
from __future__ import annotations

import hashlib
import os
import secrets

ENV_VARS = ("TWO_FACTOR_ENCRYPTION_KEY", "BOOKKEEPING_LINK_ENCRYPTION_KEY")
ISSUER = "AI Office"


class EncryptionNotConfigured(Exception):
    """Raised when 2FA is used but no encryption key is configured."""


def _fernet():
    from cryptography.fernet import Fernet

    for var in ENV_VARS:
        raw = os.environ.get(var, "").strip()
        if raw:
            try:
                return Fernet(raw.encode("utf-8"))
            except Exception as e:
                raise EncryptionNotConfigured(f"{var} is not a valid Fernet key: {e}")
    raise EncryptionNotConfigured(
        "Set TWO_FACTOR_ENCRYPTION_KEY (or BOOKKEEPING_LINK_ENCRYPTION_KEY) to a Fernet key. "
        "Generate one with: python -c "
        "\"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
    )


def generate_secret() -> str:
    """New random base32 TOTP secret (160 bits)."""
    import pyotp

    return pyotp.random_base32()


def provisioning_uri(secret: str, account: str) -> str:
    import pyotp

    return pyotp.totp.TOTP(secret).provisioning_uri(name=account, issuer_name=ISSUER)


def verify_code(secret: str, code: str) -> bool:
    """TOTP check with ±1 step window for clock skew. Constant-time-ish via pyotp."""
    import pyotp

    code = (code or "").strip().replace(" ", "")
    if not code.isdigit():
        return False
    try:
        return pyotp.TOTP(secret).verify(code, valid_window=1)
    except Exception:
        return False


def encrypt_secret(secret: str) -> str:
    return _fernet().encrypt(secret.encode("utf-8")).decode("utf-8")


def decrypt_secret(token: str) -> str:
    from cryptography.fernet import InvalidToken

    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as e:
        raise EncryptionNotConfigured(f"2FA secret cannot be decrypted: {e}")


def generate_backup_codes(n: int = 10) -> list[str]:
    """Human-typable codes: 8 chars grouped 4-4."""
    codes = []
    for _ in range(n):
        raw = secrets.token_hex(4).upper()
        codes.append(f"{raw[:4]}-{raw[4:]}")
    return codes


def hash_backup_code(code: str) -> str:
    normalized = (code or "").strip().upper().replace(" ", "")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def backup_code_matches(code: str, hashed: str) -> bool:
    return hash_backup_code(code) == hashed
