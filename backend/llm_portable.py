"""Portable LLM wrapper for Cloudflare / Oracle / Fly / Render deployments.

Why: existing callsites use `emergentintegrations.llm.chat.LlmChat` which is
Emergent-platform-locked. This shim provides the SAME interface backed by the
native OpenAI/Google SDKs when `EMERGENT_LLM_KEY` is absent, so no
callsite needs to change.

Usage (unchanged across the codebase):
    from llm_portable import LlmChat, UserMessage
    chat = LlmChat(api_key=key, session_id="x", system_message="...") \
                .with_model("openai", "gpt-6-sol")
    reply = await chat.send_message(UserMessage(text="hello"))

Resolution order:
    1. If `EMERGENT_LLM_KEY` is set AND `emergentintegrations` imports, use it.
    2. Else if `OPENAI_API_KEY` is set, use the openai async SDK directly.
    3. Else if `GEMINI_API_KEY` is set, use the google-genai async SDK
       (free tier via Google AI Studio — no card needed).
    4. Else raise so the deterministic fallback in each callsite kicks in.
"""
from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Optional

# Try to resolve the Emergent SDK once at import time.
_EI_AVAILABLE = False
try:
    if os.environ.get("EMERGENT_LLM_KEY"):
        from emergentintegrations.llm.chat import LlmChat as _EILlmChat  # noqa: F401
        from emergentintegrations.llm.chat import UserMessage as _EIUserMessage  # noqa: F401
        _EI_AVAILABLE = True
except Exception:
    _EI_AVAILABLE = False


@dataclass
class UserMessage:
    text: str


# --- Native-SDK fallback path (OpenAI, then Gemini; Claude can be added later). ---
_MODEL_ALIAS = {
    # Emergent-advertised names → closest native OpenAI model id.
    "gpt-6-sol": "gpt-4o-mini",
    "gpt-6": "gpt-4o",
    "gpt-5.5": "gpt-4o-mini",
    "gpt-5.6-terra": "gpt-4o",
}

# Default Gemini model for the free tier (text + vision-capable).
_GEMINI_DEFAULT_MODEL = "gemini-2.0-flash"


class _NativeChat:
    """Native SDK chat. Provider is resolved lazily at send time so that
    deterministic callsite fallbacks keep working:
        EMERGENT (LlmChat construction) → OPENAI_API_KEY → GEMINI_API_KEY → raise."""

    def __init__(self, system_message: str, session_id: str):
        self._system = system_message or ""
        self._session_id = session_id
        self._provider = "openai"
        self._model = "gpt-4o-mini"

    def with_model(self, provider: str, model: str) -> "_NativeChat":
        self._provider = provider or "openai"
        if self._provider == "gemini":
            # Pass real gemini-* model ids through; everything else → free-tier default.
            self._model = model if str(model).startswith("gemini") else _GEMINI_DEFAULT_MODEL
        else:
            self._model = _MODEL_ALIAS.get(model, model)
        return self

    async def send_message(self, msg: UserMessage) -> str:
        if os.environ.get("OPENAI_API_KEY"):
            return await self._send_openai(msg)
        if os.environ.get("GEMINI_API_KEY"):
            return await self._send_gemini(msg)
        raise RuntimeError("OPENAI_API_KEY and GEMINI_API_KEY missing and EMERGENT_LLM_KEY absent")

    async def _send_openai(self, msg: UserMessage) -> str:
        # Lazy import so Workers/Cloudflare builds without the pkg still boot.
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
        resp = await client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": self._system},
                {"role": "user", "content": msg.text},
            ],
        )
        return resp.choices[0].message.content or ""

    async def _send_gemini(self, msg: UserMessage) -> str:
        # google-genai==2.26.0. Google AI Studio key = free tier.
        from google import genai
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        prompt = msg.text if not self._system else f"{self._system}\n\n{msg.text}"
        resp = await client.aio.models.generate_content(
            model=self._model,
            contents=[prompt],
        )
        return (getattr(resp, "text", None) or "").strip()


class LlmChat:
    """Drop-in replacement. Picks Emergent SDK, native OpenAI, or native
    Gemini at construction/send time (see module docstring for order)."""

    def __init__(self, api_key: str, session_id: str, system_message: str):
        self._api_key = api_key
        self._session_id = session_id
        self._system_message = system_message
        self._impl: Optional[object] = None
        # Re-resolve Emergent at construction (import-time env may have been empty).
        emergent_ok = _EI_AVAILABLE
        if not emergent_ok and os.environ.get("EMERGENT_LLM_KEY"):
            try:
                from emergentintegrations.llm.chat import LlmChat as _E, UserMessage as _U  # noqa: F401
                emergent_ok = True
            except Exception:
                emergent_ok = False
        if emergent_ok and api_key and api_key.startswith("sk-emergent"):
            from emergentintegrations.llm.chat import LlmChat as _EILlmChat2
            self._impl = _EILlmChat2(
                api_key=api_key, session_id=session_id, system_message=system_message
            )
            self._uses_emergent = True
        else:
            self._impl = _NativeChat(
                system_message=system_message, session_id=session_id
            )
            self._uses_emergent = False

    def with_model(self, provider: str, model: str) -> "LlmChat":
        self._impl.with_model(provider, model)
        return self

    async def send_message(self, msg) -> str:
        if self._uses_emergent and isinstance(msg, UserMessage):
            from emergentintegrations.llm.chat import UserMessage as _EIUM
            return await self._impl.send_message(_EIUM(text=msg.text))  # type: ignore
        return await self._impl.send_message(msg)


def preferred_native_provider() -> Optional[str]:
    """Which native provider (if any) can answer today, in resolution order.

    Vision/photo-estimate providers call this: "gemini" when GEMINI_API_KEY
    is present (free tier), else "openai" when OPENAI_API_KEY is present,
    else None (callers fall back to their deterministic/mock provider).
    """
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    return None


def using_emergent() -> bool:
    """Health-probe helper."""
    if _EI_AVAILABLE:
        return True
    if os.environ.get("EMERGENT_LLM_KEY"):
        try:
            import emergentintegrations.llm.chat  # noqa: F401
            return True
        except Exception:
            return False
    return False
