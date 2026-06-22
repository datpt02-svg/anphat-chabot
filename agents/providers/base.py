"""M5 LLM provider abstraction (vast-painting-sparkle plan §2).

Defines the `LLMProvider` Protocol that all concrete providers implement.
Node code calls `await get_provider().acomplete(messages, tier="smart", tools=...)`
without needing to know whether the backend is Anthropic, OpenAI, Ollama, or vLLM.
"""
from __future__ import annotations

from typing import Any, AsyncIterator, Literal, Protocol, runtime_checkable

from langchain_core.messages import AIMessage, BaseMessage

from agents import config

ModelTier = Literal["fast", "balanced", "smart"]


# Keywords that suggest a model is "reasoning" (chain-of-thought baked into weights).
# Used to (a) warn when a reasoning model is configured for fast/balanced tier,
# (b) decide whether to apply reasoning_effort for OpenAI o-series.
_REASONING_KEYWORDS: tuple[str, ...] = (
    "r1",
    "qwq",
    "o1",
    "o3",
    "o4",
    "reasoning",
    "deepseek-r1",
)


def is_reasoning_model_name(name: str) -> bool:
    """Name-based heuristic (default `is_reasoning_model` implementation)."""
    if not name:
        return False
    n = name.lower()
    return any(kw in n for kw in _REASONING_KEYWORDS)


@runtime_checkable
class LLMProvider(Protocol):
    """Abstract interface for any LLM backend.

    Concrete providers (`AnthropicProvider`, `OpenAIProvider`, `OllamaProvider`,
    `VLLMProvider`) must implement `acomplete` and `astream`. The other methods
    have defaults derived from the model name.
    """

    async def acomplete(
        self,
        messages: list[BaseMessage],
        *,
        tier: ModelTier,
        tools: list[Any] | None = None,
        **opts: Any,
    ) -> AIMessage:
        """Single completion. Provider decides which model + params to use based on `tier`."""
        ...

    async def astream(
        self,
        messages: list[BaseMessage],
        *,
        tier: ModelTier,
        **opts: Any,
    ) -> AsyncIterator[AIMessage]:
        """Streaming completion. Default implementation calls `acomplete` once."""
        ...

    def supports_thinking(self, tier: ModelTier) -> bool:
        """Whether the current model+provider supports extended thinking for `tier`."""
        ...

    def is_reasoning_model(self, name: str) -> bool:
        """Whether the named model is a reasoning-trained model."""
        ...

    def provider_name(self) -> str:
        """Stable identifier (`anthropic` / `openai` / `ollama` / `vllm`)."""
        ...

    def resolve_model_name(self, tier: ModelTier) -> str:
        """Read `config.LLM_DEFAULT_<TIER>_MODEL`, fall back to provider-specific alias."""
        ...


def resolve_model_name(tier: ModelTier) -> str:
    """Generic model-name resolver shared by all providers.

    Logic (per plan §2.1):
    - Read `config.LLM_DEFAULT_<TIER>_MODEL` (set by user)
    - If empty and provider is anthropic, fall back to ANTHROPIC_DEFAULT_* alias
    - Otherwise raise (provider-specific validation should catch first)
    """
    generic = {
        "fast": config.LLM_DEFAULT_FAST_MODEL,
        "balanced": config.LLM_DEFAULT_BALANCED_MODEL,
        "smart": config.LLM_DEFAULT_SMART_MODEL,
    }.get(tier, "")

    if generic:
        return generic

    # Anthropic fallback (only valid when LLM_PROVIDER=anthropic, validated upstream)
    if config.LLM_PROVIDER == "anthropic":
        from agents import config as _cfg  # avoid circular at module load

        return {
            "fast": _cfg.ANTHROPIC_DEFAULT_HAIKU_MODEL,
            "balanced": _cfg.ANTHROPIC_DEFAULT_SONNET_MODEL,
            "smart": _cfg.ANTHROPIC_DEFAULT_OPUS_MODEL,
        }[tier]

    raise RuntimeError(
        f"LLM_DEFAULT_{tier.upper()}_MODEL is required when LLM_PROVIDER={config.LLM_PROVIDER!r}"
    )


__all__ = [
    "LLMProvider",
    "ModelTier",
    "is_reasoning_model_name",
    "resolve_model_name",
]
