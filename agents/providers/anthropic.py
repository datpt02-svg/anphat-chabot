"""M5 Anthropic provider (vast-painting-sparkle plan §2.2)."""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, BaseMessage

from agents import config
from agents.providers.base import LLMProvider, ModelTier, is_reasoning_model_name, resolve_model_name

logger = logging.getLogger("agents.providers.anthropic")

_MAX_TOKENS_FAST = 512
_MAX_TOKENS_BALANCED = 2048
_MAX_TOKENS_SMART_DEFAULT = 2000  # overridden per tier below


def _max_tokens_for(tier: ModelTier) -> int:
    if tier == "fast":
        return _MAX_TOKENS_FAST
    if tier == "balanced":
        return _MAX_TOKENS_BALANCED
    return config.AGENT_MAX_OUTPUT_TOKENS_SMART


class AnthropicProvider:
    """Provider wrapping `langchain_anthropic.ChatAnthropic`.

    Pass-through behavior (plan feature matrix):
    - tier=smart + thinking_model → `thinking: {type: adaptive}` + `output_config.effort: high`
    - tier=smart + non-thinking → no thinking params
    - tier=fast/balanced → plain `ChatAnthropic` (no thinking, smaller max_tokens)
    """

    def __init__(self) -> None:
        self._model_cache: dict[tuple[str, int], ChatAnthropic] = {}

    def provider_name(self) -> str:
        return "anthropic"

    def is_reasoning_model(self, name: str) -> bool:
        # Claude Opus 4.1+ supports `thinking: adaptive` + `output_config.effort: high`.
        # Extend the base heuristic with Anthropic-specific detection.
        n = (name or "").lower()
        if "opus-4-" in n or "opus-4-1" in n or "opus-4-5" in n or "opus-4-7" in n:
            return True
        return is_reasoning_model_name(name)

    def supports_thinking(self, tier: ModelTier) -> bool:
        if tier != "smart":
            return False
        return self.is_reasoning_model(self.resolve_model_name(tier))

    def resolve_model_name(self, tier: ModelTier) -> str:
        return resolve_model_name(tier)

    def _get_model(self, name: str, max_tokens: int) -> ChatAnthropic:
        key = (name, max_tokens)
        cached = self._model_cache.get(key)
        if cached is not None:
            return cached
        model = ChatAnthropic(model=name, max_tokens=max_tokens)
        self._model_cache[key] = model
        return model

    async def acomplete(
        self,
        messages: list[BaseMessage],
        *,
        tier: ModelTier,
        tools: list[Any] | None = None,
        **opts: Any,
    ) -> AIMessage:
        name = self.resolve_model_name(tier)
        max_tokens = _max_tokens_for(tier)
        model = self._get_model(name, max_tokens)

        if tier == "smart" and self.is_reasoning_model(name):
            effort = opts.get("effort", "high")
            invoke_kwargs: dict[str, Any] = {
                "messages": messages,
                "max_tokens": max_tokens,
                "thinking": {"type": "adaptive"},
                "extra_body": {"output_config": {"effort": effort}},
            }
        else:
            invoke_kwargs = {"messages": messages, "max_tokens": max_tokens}

        if tools:
            invoke_kwargs["tools"] = tools

        return await model.ainvoke(**invoke_kwargs)

    async def astream(
        self,
        messages: list[BaseMessage],
        *,
        tier: ModelTier,
        **opts: Any,
    ) -> AsyncIterator[AIMessage]:
        # Streaming is only used for tier=smart; acomplete is fine for fast/balanced.
        response = await self.acomplete(messages, tier=tier, tools=opts.get("tools"))
        yield response


__all__ = ["AnthropicProvider"]
