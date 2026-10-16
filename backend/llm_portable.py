"""Portable LLM wrapper (native providers only).

Provides the `LlmChat` / `UserMessage` interface used across the codebase,
backed directly by the native OpenAI / Google SDKs. There is no third-party
platform SDK in the path: keys are the operator's own.

Usage (unchanged across the codebase):
    from llm_portable import LlmChat, UserMessage
    chat = LlmChat(api_key=KEY, session_id="x", system_message="...") \
                .with_model("openai", "gpt-6-sol")
    reply = await chat.send_message(UserMessage(text="hello"))

Provider resolution at send time:
    1. `OPENAI_API_KEY` set → openai async SDK directly.
    2. Else `GEMINI_API_KEY` set → google-genai async SDK
       (free tier via Google AI Studio — no card needed).
    3. Else raise so the deterministic fallback in each callsite kicks in.

The shared AI key (used by callsites as their configured/not-configured gate)
is read by `ai_api_key()`: `AI_API_KEY` first, with `EMERGENT_LLM_KEY` kept
as a legacy fallback name so existing deployments keep working until their
env is renamed.
"""
from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Optional


def ai_api_key() -> Optional[str]:
    """The operator's AI key: `AI_API_KEY`, falling back to the legacy
    `EMERGENT_LLM_KEY` env name (read second, kept only so pre-rename
    deployments don't break). Returns None when neither is set."""
    return os.environ.get("AI_API_KEY") or os.environ.get("EMERGENT_LLM_KEY") or None


@dataclass
class UserMessage:
    text: str


# --- Native-SDK path (OpenAI, then Gemini; Claude can be added later). ---
_MODEL_ALIAS = {
    # Legacy advertised names → closest native OpenAI model id.
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
        OPENAI_API_KEY → GEMINI_API_KEY → raise."""

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
        raise RuntimeError("No LLM provider configured (set OPENAI_API_KEY or GEMINI_API_KEY)")

    async def _send_openai(self, msg: UserMessage) -> str:
        # Lazy import so Workers/Cloudflare builds without the pkg still boot.
        from openai import AsyncOpenAI
        key = os.environ["OPENAI_API_KEY"]
        client = AsyncOpenAI(api_key=key)
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
        key = os.environ["GEMINI_API_KEY"]
        client = genai.Client(api_key=key)
        prompt = msg.text if not self._system else f"{self._system}\n\n{msg.text}"
        resp = await client.aio.models.generate_content(
            model=self._model,
            contents=[prompt],
        )
        return (getattr(resp, "text", None) or "").strip()


class LlmChat:
    """Drop-in chat client. Picks native OpenAI or native Gemini at send
    time (see module docstring for order). The `api_key` argument is kept
    for interface compatibility with existing callsites; the native path
    reads the provider keys from the environment."""

    def __init__(self, api_key: str, session_id: str, system_message: str):
        self._api_key = api_key
        self._session_id = session_id
        self._system_message = system_message
        self._impl = _NativeChat(
            system_message=system_message, session_id=session_id
        )

    def with_model(self, provider: str, model: str) -> "LlmChat":
        self._impl.with_model(provider, model)
        return self

    async def send_message(self, msg) -> str:
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
