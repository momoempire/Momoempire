"""AI photo estimates — vision provider abstraction.

A customer (or tech) uploads a photo of the job; the vision provider suggests
line items that get matched against the tenant's pricebook; a human approves
or edits them; the approved lines flow into the existing quote-draft pipeline
(routers/call_to_payment._create_estimate_doc via routers/pricebook).

Design mirrors services/financing.py:
    * `VisionProvider` ABC is the contract every provider implements.
    * `MockVisionProvider` returns deterministic suggestions for tests and the
      no-keys default. Clearly labeled mock — never mistaken for real vision.
    * `GeminiVisionProvider` and `OpenAICompatibleVisionProvider` are skeletons
      implementing the same interface. Every spot that would touch a live
      model API is marked TODO(LIVE) and raises `VisionNotConfigured` today —
      no network calls, no keys, no spend.

Provider resolution (v1, env-based; per-tenant config is a future iteration):
    GEMINI_API_KEY present  → gemini (free tier via Google AI Studio)
    OPENAI_API_KEY present  → openai-compatible
    neither                 → mock
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional


class VisionError(Exception):
    """Base vision error (maps to 4xx/5xx in the router)."""


class VisionNotConfigured(VisionError):
    """The selected live provider has no credentials yet — mock is used."""


# ---------- suggested item ----------

@dataclass(frozen=True)
class SuggestedItem:
    """One AI-suggested line item.

    `suggested_pricebook_item_id` is filled by the router after matching the
    description against the tenant's pricebook (None = generic line item).
    `confidence` is a 0..1 self-assessed score surfaced in the UI so a human
    knows what to double-check.
    """
    description: str
    confidence: float
    qty: float = 1.0
    suggested_pricebook_item_id: Optional[str] = None
    pricebook_match_name: Optional[str] = None
    mock: bool = False

    def as_dict(self) -> dict:
        return {
            "description": self.description,
            "confidence": round(float(self.confidence), 2),
            "qty": float(self.qty),
            "suggested_pricebook_item_id": self.suggested_pricebook_item_id,
            "pricebook_match_name": self.pricebook_match_name,
            "mock": self.mock,
        }


# ---------- provider ABC ----------

class VisionProvider(ABC):
    """Contract: look at image bytes, suggest job line items."""

    name: str = "vision"

    @abstractmethod
    async def suggest_line_items(
        self, image_bytes: bytes, context: dict
    ) -> List[SuggestedItem]:
        """Return suggested line items for the photo.

        `context` is a small dict the router fills: tenant trade, notes from
        the uploader, and the tenant's pricebook names so the model can favor
        descriptions that map to real catalog rows.
        """
        raise NotImplementedError


# ---------- mock ----------

class MockVisionProvider(VisionProvider):
    """Deterministic suggestions for tests and the no-keys default.

    Always returns the same two items (labeled mock) so tests are stable and
    demos never depend on a model. Descriptions are trade-neutral on purpose —
    the router's pricebook matcher decides what survives.
    """

    name = "mock"

    async def suggest_line_items(
        self, image_bytes: bytes, context: dict
    ) -> List[SuggestedItem]:
        trade = str(context.get("trade") or "general")
        return [
            SuggestedItem(
                description=f"Diagnostic visit ({trade})",
                confidence=0.92,
                qty=1.0,
                mock=True,
            ),
            SuggestedItem(
                description="Replacement part (generic)",
                confidence=0.61,
                qty=1.0,
                mock=True,
            ),
        ]


# ---------- live skeletons (no live calls today) ----------

class GeminiVisionProvider(VisionProvider):
    """Free-tier vision via Google AI Studio (GEMINI_API_KEY).

    TODO(LIVE): implement `suggest_line_items` with google-genai==2.26.0:
        from google import genai
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        resp = await client.aio.models.generate_content(
            model="gemini-2.0-flash",
            contents=[
                "Suggest line items as JSON: [{description, confidence, qty}]...",
                genai.types.Part.from_bytes(
                    data=image_bytes, mime_type=context.get("mime_type", "image/jpeg")),
            ],
        )
        then parse resp.text into SuggestedItem rows.
    Raises VisionNotConfigured until that lands — no network calls today.
    """

    name = "gemini"

    async def suggest_line_items(
        self, image_bytes: bytes, context: dict
    ) -> List[SuggestedItem]:
        if not os.environ.get("GEMINI_API_KEY"):
            raise VisionNotConfigured("GEMINI_API_KEY is not set")
        # TODO(LIVE): real google-genai vision call goes here (see docstring).
        raise VisionNotConfigured(
            "Gemini vision is stubbed — TODO(LIVE) not implemented yet"
        )


class OpenAICompatibleVisionProvider(VisionProvider):
    """Vision via any OpenAI-compatible chat endpoint (OPENAI_API_KEY).

    TODO(LIVE): implement `suggest_line_items` with a base64 data-URL image
    message to an OpenAI-compatible /chat/completions endpoint supporting
    image inputs (e.g. gpt-4o):
        {"role": "user", "content": [
            {"type": "text", "text": "... JSON schema prompt ..."},
            {"type": "image_url",
             "image_url": {"url": "data:<mime>;base64," + b64(image_bytes)}},
        ]}
    then parse the JSON content into SuggestedItem rows.
    Raises VisionNotConfigured until that lands — no network calls today.
    """

    name = "openai"

    async def suggest_line_items(
        self, image_bytes: bytes, context: dict
    ) -> List[SuggestedItem]:
        if not os.environ.get("OPENAI_API_KEY"):
            raise VisionNotConfigured("OPENAI_API_KEY is not set")
        # TODO(LIVE): real OpenAI-compatible vision call goes here (see docstring).
        raise VisionNotConfigured(
            "OpenAI vision is stubbed — TODO(LIVE) not implemented yet"
        )


# ---------- resolution ----------

def resolve_provider(override: Optional[str] = None) -> VisionProvider:
    """Pick a provider in resolution order.

    override: explicit name for tests ("mock" | "gemini" | "openai").
    Default: gemini if GEMINI_API_KEY is set (free tier), else openai if
    OPENAI_API_KEY is set, else mock.
    """
    if override == "mock":
        return MockVisionProvider()
    if override == "gemini":
        return GeminiVisionProvider()
    if override == "openai":
        return OpenAICompatibleVisionProvider()
    if os.environ.get("GEMINI_API_KEY"):
        return GeminiVisionProvider()
    if os.environ.get("OPENAI_API_KEY"):
        return OpenAICompatibleVisionProvider()
    return MockVisionProvider()
