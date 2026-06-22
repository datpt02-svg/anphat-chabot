"""M5 LLM provider tests (vast-painting-sparkle plan §7.1).

Covers: factory tests, Anthropic `thinking` pass-through, OpenAI skipping
proprietary params, ANTHROPIC_* alias errors when LLM_PROVIDER != anthropic,
conflict resolution, provider instance caching, reasoning model detection.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

# Minimal env defaults — tests set the rest via monkeypatch.
os.environ.setdefault("HMAC_SALT", "test_salt")
os.environ.setdefault("HMAC_SALT_VERSION", "v1")
os.environ.setdefault("JWT_SECRET", "test_secret")
os.environ.setdefault("JWT_ALGORITHM", "HS256")
os.environ.setdefault("OPENAI_API_KEY", "test_key")


# ---------------------------------------------------------------------------
# Provider factory tests
# ---------------------------------------------------------------------------
def test_factory_anthropic(monkeypatch):
    from agents import providers
    from agents.providers.anthropic import AnthropicProvider

    providers.reset_provider_cache()
    monkeypatch.setattr(providers.config, "LLM_PROVIDER", "anthropic")
    p = providers.get_provider("anthropic")
    assert isinstance(p, AnthropicProvider)
    assert p.provider_name() == "anthropic"


def test_factory_openai(monkeypatch):
    from agents import providers
    from agents.providers.openai import OpenAIProvider

    providers.reset_provider_cache()
    monkeypatch.setattr(providers.config, "LLM_PROVIDER", "openai")
    p = providers.get_provider("openai")
    assert isinstance(p, OpenAIProvider)
    assert p.provider_name() == "openai"


def test_factory_ollama(monkeypatch):
    from agents import providers
    from agents.providers.local import OllamaProvider

    providers.reset_provider_cache()
    monkeypatch.setattr(providers.config, "LLM_PROVIDER", "ollama")
    p = providers.get_provider("ollama")
    assert isinstance(p, OllamaProvider)
    assert p.provider_name() == "ollama"
    assert p._base_url() == "http://localhost:11434/v1"


def test_factory_vllm(monkeypatch):
    from agents import providers
    from agents.providers.local import VLLMProvider

    providers.reset_provider_cache()
    monkeypatch.setattr(providers.config, "LLM_PROVIDER", "vllm")
    p = providers.get_provider("vllm")
    assert isinstance(p, VLLMProvider)
    assert p._base_url() == "http://localhost:8000/v1"


def test_factory_unknown_raises():
    from agents import providers
    providers.reset_provider_cache()
    with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
        providers.get_provider("bogus")


def test_factory_singleton_cache(monkeypatch):
    from agents import providers

    providers.reset_provider_cache()
    monkeypatch.setattr(providers.config, "LLM_PROVIDER", "anthropic")
    a = providers.get_provider("anthropic")
    b = providers.get_provider("anthropic")
    assert a is b


# ---------------------------------------------------------------------------
# Anthropic pass-through (thinking + effort on smart + reasoning model)
# ---------------------------------------------------------------------------
class _FakeAnthropicModel:
    """Stand-in for `langchain_anthropic.ChatAnthropic`."""

    instances: list = []
    last_call_kwargs: dict = {}

    def __init__(self, *, model: str, max_tokens: int, **kwargs):
        self.model = model
        self.max_tokens = max_tokens
        self.kwargs = kwargs
        _FakeAnthropicModel.instances.append(self)
        _FakeAnthropicModel.last_call_kwargs = {}

    async def ainvoke(self, **kwargs):
        # Record kwargs for the latest call
        _FakeAnthropicModel.last_call_kwargs = dict(kwargs)
        from langchain_core.messages import AIMessage
        return AIMessage(content="ok", response_metadata={"model_name": self.model})


def test_anthropic_passes_thinking_on_smart_tier(monkeypatch):
    from agents import providers
    from agents.providers import anthropic as anthropic_mod

    providers.reset_provider_cache()
    monkeypatch.setattr(providers.config, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(providers.config, "LLM_DEFAULT_SMART_MODEL", "claude-opus-4-1-20250805")
    monkeypatch.setattr(anthropic_mod, "ChatAnthropic", _FakeAnthropicModel)
    _FakeAnthropicModel.instances = []
    _FakeAnthropicModel.last_call_kwargs = {}

    import asyncio
    from langchain_core.messages import HumanMessage, SystemMessage
    p = providers.get_provider("anthropic")
    asyncio.run(
        p.acomplete(
            messages=[SystemMessage(content="s"), HumanMessage(content="h")],
            tier="smart",
        )
    )
    assert _FakeAnthropicModel.last_call_kwargs.get("thinking") == {"type": "adaptive"}
    assert _FakeAnthropicModel.last_call_kwargs.get("extra_body") == {"output_config": {"effort": "high"}}


def test_anthropic_no_thinking_on_fast_tier(monkeypatch):
    from agents import providers
    from agents.providers import anthropic as anthropic_mod

    providers.reset_provider_cache()
    monkeypatch.setattr(providers.config, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(providers.config, "LLM_DEFAULT_FAST_MODEL", "claude-haiku-4-5-20251001")
    monkeypatch.setattr(anthropic_mod, "ChatAnthropic", _FakeAnthropicModel)
    _FakeAnthropicModel.instances = []
    _FakeAnthropicModel.last_call_kwargs = {}

    import asyncio
    from langchain_core.messages import HumanMessage, SystemMessage
    p = providers.get_provider("anthropic")
    asyncio.run(
        p.acomplete(
            messages=[SystemMessage(content="s"), HumanMessage(content="h")],
            tier="fast",
        )
    )
    assert "thinking" not in _FakeAnthropicModel.last_call_kwargs
    assert "extra_body" not in _FakeAnthropicModel.last_call_kwargs


# ---------------------------------------------------------------------------
# OpenAI skips thinking params, uses reasoning_effort for o-series
# ---------------------------------------------------------------------------
class _FakeOpenAIModel:
    instances: list = []
    last_call_kwargs: dict = {}

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        _FakeOpenAIModel.instances.append(self)
        _FakeOpenAIModel.last_call_kwargs = {}

    async def ainvoke(self, **kwargs):
        _FakeOpenAIModel.last_call_kwargs = dict(kwargs)
        from langchain_core.messages import AIMessage
        return AIMessage(content="ok", response_metadata={"model_name": "gpt-4o"})


def test_openai_skips_thinking(monkeypatch):
    from agents import providers
    from agents.providers import openai as openai_mod

    providers.reset_provider_cache()
    monkeypatch.setattr(providers.config, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(providers.config, "LLM_DEFAULT_SMART_MODEL", "gpt-4o")
    monkeypatch.setattr(openai_mod, "ChatOpenAI", _FakeOpenAIModel)
    _FakeOpenAIModel.instances = []
    _FakeOpenAIModel.last_call_kwargs = {}

    import asyncio
    from langchain_core.messages import HumanMessage, SystemMessage
    p = providers.get_provider("openai")
    asyncio.run(
        p.acomplete(
            messages=[SystemMessage(content="s"), HumanMessage(content="h")],
            tier="smart",
        )
    )
    # OpenAI must not receive Anthropic-proprietary params
    assert "thinking" not in _FakeOpenAIModel.last_call_kwargs
    assert "extra_body" not in _FakeOpenAIModel.last_call_kwargs
    # gpt-4o is not o-series → no reasoning_effort
    assert "reasoning_effort" not in _FakeOpenAIModel.last_call_kwargs


def test_openai_o_series_uses_reasoning_effort(monkeypatch):
    from agents import providers
    from agents.providers import openai as openai_mod

    providers.reset_provider_cache()
    monkeypatch.setattr(providers.config, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(providers.config, "LLM_DEFAULT_SMART_MODEL", "o1-preview")
    monkeypatch.setattr(openai_mod, "ChatOpenAI", _FakeOpenAIModel)
    _FakeOpenAIModel.instances = []
    _FakeOpenAIModel.last_call_kwargs = {}

    import asyncio
    from langchain_core.messages import HumanMessage, SystemMessage
    p = providers.get_provider("openai")
    asyncio.run(
        p.acomplete(
            messages=[SystemMessage(content="s"), HumanMessage(content="h")],
            tier="smart",
        )
    )
    assert _FakeOpenAIModel.last_call_kwargs.get("reasoning_effort") == "high"


def test_ollama_with_base_url(monkeypatch):
    from agents import providers
    from agents.providers import openai as openai_mod

    providers.reset_provider_cache()
    monkeypatch.setattr(providers.config, "LLM_PROVIDER", "ollama")
    monkeypatch.setattr(providers.config, "LLM_DEFAULT_FAST_MODEL", "llama3:8b")
    monkeypatch.setattr(openai_mod, "ChatOpenAI", _FakeOpenAIModel)
    _FakeOpenAIModel.instances = []

    p = providers.get_provider("ollama")
    assert isinstance(p, providers.LLMProvider)
    assert p._base_url() == "http://localhost:11434/v1"
    assert len(_FakeOpenAIModel.instances) == 0  # lazy init


# ---------------------------------------------------------------------------
# Config: ANTHROPIC_* alias error when LLM_PROVIDER != anthropic
# ---------------------------------------------------------------------------
def test_anthropic_alias_error_when_provider_not_anthropic(monkeypatch):
    """Plan §1: raise at config load if `ANTHROPIC_DEFAULT_*` set with non-anthropic provider."""
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("ANTHROPIC_DEFAULT_HAIKU_MODEL", "claude-haiku-custom-fake-id-999")
    # Reload config module to trigger validation
    from agents import config
    with pytest.raises(RuntimeError, match="ANTHROPIC_DEFAULT_\\* aliases cannot be set"):
        importlib.reload(config)
    # Restore for subsequent tests
    monkeypatch.delenv("ANTHROPIC_DEFAULT_HAIKU_MODEL")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    importlib.reload(config)


def test_conflict_resolution_raises(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_DEFAULT_SMART_MODEL", "claude-opus-4-7-future")
    monkeypatch.setenv("ANTHROPIC_DEFAULT_OPUS_MODEL", "claude-opus-4-1-20250805")
    from agents import config
    with pytest.raises(RuntimeError, match="Conflicting model IDs"):
        importlib.reload(config)
    # Restore
    monkeypatch.delenv("LLM_DEFAULT_SMART_MODEL")
    importlib.reload(config)


# ---------------------------------------------------------------------------
# Reasoning model detection
# ---------------------------------------------------------------------------
def test_is_reasoning_model_name_heuristic():
    from agents.providers.base import is_reasoning_model_name

    assert is_reasoning_model_name("deepseek-r1:70b") is True
    assert is_reasoning_model_name("qwen2.5-qwq") is True
    assert is_reasoning_model_name("o1-preview") is True
    assert is_reasoning_model_name("o3-mini") is True
    assert is_reasoning_model_name("some-reasoning-model") is True
    assert is_reasoning_model_name("gpt-4o") is False
    assert is_reasoning_model_name("claude-haiku-4-5-20251001") is False
    assert is_reasoning_model_name("") is False
    assert is_reasoning_model_name("llama3:8b") is False


def test_openai_o_series_detected_as_reasoning():
    from agents.providers.openai import OpenAIProvider

    p = OpenAIProvider()
    assert p.is_reasoning_model("o1-preview") is True
    assert p.is_reasoning_model("o3-mini") is True
    assert p.is_reasoning_model("o4-mini") is True
    assert p.is_reasoning_model("gpt-4o") is False
