"""M5 LLM provider factory (vast-painting-sparkle plan §2.5).

`get_provider(name)` returns a singleton instance per `name` (cached in
module-level dict). Avoids recreating `ChatAnthropic` / `ChatOpenAI` on every
node call.
"""
from __future__ import annotations

import logging
from typing import Any

from agents import config
from agents.providers.anthropic import AnthropicProvider
from agents.providers.base import LLMProvider
from agents.providers.local import OllamaProvider, VLLMProvider
from agents.providers.openai import OpenAIProvider

logger = logging.getLogger("agents.providers")

_PROVIDER_CACHE: dict[str, LLMProvider] = {}


def _build_provider(name: str) -> LLMProvider:
    name = (name or "anthropic").strip().lower()
    if name == "anthropic":
        return AnthropicProvider()
    if name == "openai":
        return OpenAIProvider(provider_label="openai")
    if name == "ollama":
        return OllamaProvider()
    if name == "vllm":
        return VLLMProvider()
    raise ValueError(f"Unknown LLM_PROVIDER: {name!r}")


def get_provider(name: str | None = None) -> LLMProvider:
    """Return the singleton provider for `name` (default: `config.LLM_PROVIDER`).

    Caches one instance per `name` in module-level dict (plan §2.5).
    """
    name = name or config.LLM_PROVIDER
    cached = _PROVIDER_CACHE.get(name)
    if cached is not None:
        return cached
    instance = _build_provider(name)
    _PROVIDER_CACHE[name] = instance
    logger.info("provider %r instantiated", name)
    return instance


def reset_provider_cache() -> None:
    """Test helper: clear the singleton cache so a new `LLM_PROVIDER` is honored."""
    _PROVIDER_CACHE.clear()


__all__ = ["get_provider", "reset_provider_cache", "LLMProvider"]
