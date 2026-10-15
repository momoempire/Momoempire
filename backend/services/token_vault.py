"""Per-tenant secret encryption (bookkeeping-link API tokens, etc.).

=====================================================================
CLEARLY-MARKED SECURITY MODULE: symmetric encryption for secrets that
must be stored in the database (e.g. a tenant's bookkeeping-app API
token). The key comes from the environment variable
BOOKKEEPING_LINK_ENCRYPTION_KEY, which must be a Fernet key
(urlsafe-base64 32 bytes — generate with Fernet.generate_key()).

FAIL CLOSED: any encrypt/decrypt attempt without a configured key
raises EncryptionNotConfigured instead of silently falling back to
plaintext. The plaintext secret is never logged anywhere in this
module, and callers must never return it in API responses.
=====================================================================
"""
from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken

ENV_VAR = "BOOKKEEPING_LINK_ENCRYPTION_KEY"


class EncryptionNotConfigured(Exception):
    """Raised when encryption is attempted but no key is configured."""


class SecretDecryptionFailed(Exception):
    """Raised when a stored secret cannot be decrypted (wrong key / corrupt)."""


def _fernet() -> Fernet:
    raw = os.environ.get(ENV_VAR, "").strip()
    if not raw:
        raise EncryptionNotConfigured(
            f"{ENV_VAR} is not set — cannot encrypt or decrypt secrets. "
            "Generate one with: python -c "
            "\"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    try:
        return Fernet(raw.encode("utf-8"))
    except Exception as e:
        raise EncryptionNotConfigured(f"{ENV_VAR} is not a valid Fernet key: {e}")


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a secret for storage. Fails closed when no key is configured."""
    if not plaintext:
        raise ValueError("cannot encrypt an empty secret")
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(ciphertext: str) -> str:
    """Decrypt a stored secret. Fails closed when no key is configured."""
    if not ciphertext:
        raise ValueError("cannot decrypt an empty secret")
    try:
        return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken as e:
        raise SecretDecryptionFailed(
            "Stored secret could not be decrypted — the encryption key may "
            "have changed. Re-enter the secret in settings."
        ) from e
