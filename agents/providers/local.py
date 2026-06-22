"""M5 local (Ollama / vLLM) providers (vast-painting-sparkle plan §2.4).

Both expose OpenAI-compatible `/v1/chat/completions` endpoints, so we subclass
`OpenAIProvider` and just override the default `base_url`. Reasoning-trained
local models (DeepSeek-R1, QwQ) emit `<think>` blocks natively — the API has
no `thinking` / `effort` knob, so we skip those params (inherited behavior).
"""
from __future__ import annotations

from agents.providers.openai import OpenAIProvider

DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434/v1"
DEFAULT_VLLM_BASE_URL = "http://localhost:8000/v1"


class OllamaProvider(OpenAIProvider):
    """Ollama local-server provider (default base_url `http://localhost:11434/v1`)."""

    def __init__(self) -> None:
        super().__init__(base_url=DEFAULT_OLLAMA_BASE_URL, provider_label="ollama")


class VLLMProvider(OpenAIProvider):
    """vLLM local-server provider (default base_url `http://localhost:8000/v1`)."""

    def __init__(self) -> None:
        super().__init__(base_url=DEFAULT_VLLM_BASE_URL, provider_label="vllm")


__all__ = ["OllamaProvider", "VLLMProvider", "DEFAULT_OLLAMA_BASE_URL", "DEFAULT_VLLM_BASE_URL"]
