"""Password strength rule for admin / first-login password changes."""
import re

MIN_LENGTH = 12

# Known-weak / previously shipped default passwords (original case, so stored hashes can be
# checked against them). Includes the old seeded admin default. Never accepted as a new password,
# and any existing admin hash matching one is replaced at startup (seed_data.seed_admin).
KNOWN_DEFAULT_PASSWORDS = (
    "AdminPass123!",
    "Password123!",
    "ChangeMe123!",
    "Admin123456!",
    "Welcome123!!",
    "admin",
    "password",
    "changeme",
)
WEAK_DENYLIST = frozenset(p.lower() for p in KNOWN_DEFAULT_PASSWORDS)


def password_problems(password: str) -> list[str]:
    """Return a list of human-readable problems; empty list means strong enough."""
    pw = password or ""
    problems = []
    if len(pw) < MIN_LENGTH:
        problems.append(f"must be at least {MIN_LENGTH} characters")
    if not re.search(r"[a-z]", pw):
        problems.append("must include a lowercase letter")
    if not re.search(r"[A-Z]", pw):
        problems.append("must include an uppercase letter")
    if not re.search(r"\d", pw):
        problems.append("must include a digit")
    if not re.search(r"[^A-Za-z0-9]", pw):
        problems.append("must include a symbol")
    if pw.lower() in WEAK_DENYLIST:
        problems.append("is a known default/weak password")
    return problems


def is_strong_password(password: str) -> bool:
    return not password_problems(password)
