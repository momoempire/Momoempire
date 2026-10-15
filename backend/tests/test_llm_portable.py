"""Unit tests for backend/llm_portable.py (incl. GEMINI_API_KEY support).

Resolution order under test:
    EMERGENT_LLM_KEY → OPENAI_API_KEY → GEMINI_API_KEY → raise.

No live API calls anywhere: the emergentintegrations, openai, and google
packages are faked in sys.modules (the SDK imports in llm_portable are all
lazy, so fakes injected before send_message take effect).
"""
from __future__ import annotations

import asyncio
import os
import sys
import types

import pytest

import llm_portable
from llm_portable import LlmChat, UserMessage, preferred_native_provider, using_emergent


# ---------- fakes ----------

class FakeCompletions:
    calls = []

    async def create(self, **kwargs):
        FakeCompletions.calls.append(kwargs)
        return types.SimpleNamespace(choices=[
            types.SimpleNamespace(message=types.SimpleNamespace(
                content="openai-reply"))])


class FakeAsyncOpenAI:
    instances = []

    def __init__(self, api_key=None):
        self.api_key = api_key
        self.chat = types.SimpleNamespace(completions=FakeCompletions())
        FakeAsyncOpenAI.instances.append(self)


class FakeGenModels:
    calls = []

    async def generate_content(self, model=None, contents=None):
        FakeGenModels.calls.append({"model": model, "contents": contents})
        return types.SimpleNamespace(text="gemini-reply")


class FakeGenClient:
    def __init__(self, api_key=None):
        self.api_key = api_key
        self.aio = types.SimpleNamespace(models=FakeGenModels())


class FakeEILlmChat:
    instances = []

    def __init__(self, api_key=None, session_id=None, system_message=None):
        self.api_key = api_key
        FakeEILlmChat.instances.append(self)

    def with_model(self, provider, model):
        return self

    async def send_message(self, msg):
        return f"emergent-reply:{getattr(msg, 'text', '')}"


class FakeEIUserMessage:
    def __init__(self, text=""):
        self.text = text


def _install_fake_openai(monkeypatch):
    mod = types.ModuleType("openai")
    mod.AsyncOpenAI = FakeAsyncOpenAI
    monkeypatch.setitem(sys.modules, "openai", mod)


def _install_fake_google(monkeypatch):
    google_mod = types.ModuleType("google")
    genai_mod = types.ModuleType("google.genai")
    genai_mod.Client = FakeGenClient
    google_mod.genai = genai_mod
    monkeypatch.setitem(sys.modules, "google", google_mod)
    monkeypatch.setitem(sys.modules, "google.genai", genai_mod)


def _install_fake_emergent(monkeypatch):
    root = types.ModuleType("emergentintegrations")
    llm = types.ModuleType("emergentintegrations.llm")
    chat = types.ModuleType("emergentintegrations.llm.chat")
    chat.LlmChat = FakeEILlmChat
    chat.UserMessage = FakeEIUserMessage
    root.llm = llm
    llm.chat = chat
    monkeypatch.setitem(sys.modules, "emergentintegrations", root)
    monkeypatch.setitem(sys.modules, "emergentintegrations.llm", llm)
    monkeypatch.setitem(sys.modules, "emergentintegrations.llm.chat", chat)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("EMERGENT_LLM_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    FakeCompletions.calls.clear()
    FakeAsyncOpenAI.instances.clear()
    FakeGenModels.calls.clear()
    FakeEILlmChat.instances.clear()
    yield


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------- resolution order ----------

def test_no_keys_raises(_clean_env):
    chat = LlmChat(api_key="", session_id="s", system_message="sys")
    with pytest.raises(RuntimeError):
        _run(chat.send_message(UserMessage(text="hi")))


def test_openai_wins_over_gemini(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    _install_fake_openai(monkeypatch)
    _install_fake_google(monkeypatch)
    chat = LlmChat(api_key="", session_id="s", system_message="sys")
    assert _run(chat.send_message(UserMessage(text="hi"))) == "openai-reply"
    assert len(FakeAsyncOpenAI.instances) == 1
    assert FakeGenModels.calls == []  # gemini never consulted


def test_gemini_used_when_no_openai(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    _install_fake_google(monkeypatch)
    chat = LlmChat(api_key="", session_id="s", system_message="sys") \
        .with_model("gemini", "gemini-2.0-flash")
    assert _run(chat.send_message(UserMessage(text="hi"))) == "gemini-reply"
    assert FakeGenModels.calls[0]["model"] == "gemini-2.0-flash"
    # system message is prepended to the prompt
    assert "sys" in FakeGenModels.calls[0]["contents"][0]


def test_gemini_model_alias_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    _install_fake_google(monkeypatch)
    chat = LlmChat(api_key="", session_id="s", system_message="") \
        .with_model("gemini", "gpt-6-sol")  # non-gemini name → free-tier default
    _run(chat.send_message(UserMessage(text="hi")))
    assert FakeGenModels.calls[0]["model"] == "gemini-2.0-flash"


def test_openai_model_alias_still_applies(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    _install_fake_openai(monkeypatch)
    chat = LlmChat(api_key="", session_id="s", system_message="") \
        .with_model("openai", "gpt-6-sol")
    _run(chat.send_message(UserMessage(text="hi")))
    assert FakeCompletions.calls[0]["model"] == "gpt-4o-mini"
    msgs = FakeCompletions.calls[0]["messages"]
    assert msgs[0]["role"] == "system" and msgs[1]["role"] == "user"


def test_emergent_still_first(monkeypatch):
    monkeypatch.setenv("EMERGENT_LLM_KEY", "emergent-key")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    _install_fake_emergent(monkeypatch)
    _install_fake_openai(monkeypatch)
    chat = LlmChat(api_key="sk-emergent-abc", session_id="s",
                   system_message="sys")
    assert _run(chat.send_message(UserMessage(text="hi"))) == "emergent-reply:hi"
    assert len(FakeEILlmChat.instances) == 1
    assert FakeAsyncOpenAI.instances == []


# ---------- helper probes ----------

def test_preferred_native_provider_order(monkeypatch):
    assert preferred_native_provider() is None
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    assert preferred_native_provider() == "gemini"
    monkeypatch.setenv("OPENAI_API_KEY", "o")
    assert preferred_native_provider() == "gemini"  # gemini preferred
    monkeypatch.delenv("GEMINI_API_KEY")
    assert preferred_native_provider() == "openai"


def test_using_emergent_probe(monkeypatch):
    assert using_emergent() is False
    _install_fake_emergent(monkeypatch)
    monkeypatch.setenv("EMERGENT_LLM_KEY", "k")
    assert using_emergent() is True


def test_usermessage_dataclass():
    assert UserMessage(text="x").text == "x"
    assert llm_portable._MODEL_ALIAS["gpt-6-sol"] == "gpt-4o-mini"
