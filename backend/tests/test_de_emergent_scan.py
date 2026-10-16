"""De-Emergent source scan — live source must not reference the old platform.

The wave/de-emergent build removed every production dependency on the old
hosting platform (Google OAuth broker, managed email proxy, platform LLM
SDK, preview-domain defaults). This scan keeps it that way.

Banned in live backend + frontend source:
  - the old platform auth/backend/email/integration hostnames
  - the old platform domain
  - "sk-emergent"       — the old platform LLM key prefix (routing is gone)
  - "EMERGENT_EMAIL_KEY"— replaced by the operator's own RESEND_API_KEY
  - "EMERGENT_LLM_KEY" as a primary env read: the string may survive ONLY
    inside backend/llm_portable.py, where ai_api_key() reads it second as
    a legacy fallback for pre-rename deployments.

Scope: backend/**/*.py and frontend/src/** (+ craco.config.js), excluding
test files — tests legitimately assert the ABSENCE of these strings
(e.g. test_waitlist_db_errors.py) and exercise the legacy fallback — and
excluding historical records (test_reports/, memory/), which are kept
verbatim as records.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend"
FRONTEND_SRC = REPO_ROOT / "frontend" / "src"

# Host strings are built by concatenation so this file itself stays clear of
# the legacy guard in test_waitlist_db_errors.py (no literal platform host
# may appear in any test file).
_PLATFORM_HOST = "emergent" + "agent.com"
_PLATFORM_DOMAIN = "emergent" + ".sh"
BANNED = (_PLATFORM_HOST, _PLATFORM_DOMAIN, "sk-emergent", "EMERGENT_EMAIL_KEY")
LEGACY_KEY = "EMERGENT_LLM_KEY"
LEGACY_KEY_HOME = BACKEND / "llm_portable.py"  # the one sanctioned fallback read


def _is_test_file(p: Path) -> bool:
    name = p.name
    return (
        name.startswith("test_")
        or name.endswith((".test.js", ".test.jsx", ".test.ts", ".test.tsx"))
        or "tests" in p.parts
        or "__pycache__" in p.parts
    )


def _live_source_files():
    files = []
    for p in BACKEND.rglob("*.py"):
        if not _is_test_file(p):
            files.append(p)
    for p in FRONTEND_SRC.rglob("*"):
        if p.is_file() and p.suffix in {".js", ".jsx", ".ts", ".tsx", ".css"}:
            if not _is_test_file(p):
                files.append(p)
    craco = REPO_ROOT / "frontend" / "craco.config.js"
    if craco.exists():
        files.append(craco)
    return files


def test_no_platform_references_in_live_source():
    offenders = []
    for p in _live_source_files():
        text = p.read_text(errors="ignore")
        for lineno, line in enumerate(text.splitlines(), 1):
            for bad in BANNED:
                if bad in line:
                    offenders.append(f"{p.relative_to(REPO_ROOT)}:{lineno}: {bad}")
            if LEGACY_KEY in line and p != LEGACY_KEY_HOME:
                offenders.append(
                    f"{p.relative_to(REPO_ROOT)}:{lineno}: {LEGACY_KEY} "
                    "outside llm_portable.ai_api_key fallback"
                )
    assert not offenders, "platform references remain:\n" + "\n".join(offenders)


def test_legacy_key_fallback_lives_only_in_llm_portable():
    text = LEGACY_KEY_HOME.read_text()
    # The fallback read exists (AI_API_KEY first, legacy name second)…
    assert 'os.environ.get("AI_API_KEY") or os.environ.get("EMERGENT_LLM_KEY")' in text
    # …and the platform SDK is gone from the module entirely.
    assert "emergentintegrations" not in text
    assert "sk-emergent" not in text
