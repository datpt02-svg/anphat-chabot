"""M5 OpenAI provider (vast-painting-sparkle plan §2.3).

Also used as the base for local providers (Ollama / vLLM) since they expose
the OpenAI-compatible `/v1/chat/completions` surface.
"""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from agents import config
from agents.providers.base import LLMProvider, ModelTier, is_reasoning_model_name, resolve_model_name

logger = logging.getLogger("agents.providers.openai")


def _sanitize_messages(messages: list[Any]) -> list[Any]:
    """Strip orphan `ToolMessage` entries whose `tool_call_id` does not match
    any prior assistant `tool_call.id` in the same conversation. Some
    OpenAI-compatible providers (e.g. the local `minimax-m3` proxy) raise
    `400 — tool result's tool id ... not found` if the history contains a
    tool result referencing an id the server doesn't recognise. When the
    upstream error triggers, the entire run aborts before any assistant
    text reaches the user, so the chat UI shows nothing.

    The cheapest robust fix is to scrub mismatched tool messages before the
    request. We do not invent or rewrite ids — we drop the orphan result
    message. The model still receives the human text and any in-band
    assistant content; the trade-off is a missing tool result for one
    turn, which is far better than a hard 400 that kills the whole run.
    """
    known_ids: set[str] = set()
    for message in messages or []:
        tool_calls = getattr(message, "tool_calls", None) or []
        for call in tool_calls:
            call_id = call.get("id") if isinstance(call, dict) else getattr(call, "id", None)
            if isinstance(call_id, str) and call_id:
                known_ids.add(call_id)

    sanitized: list[Any] = []
    dropped = 0
    for message in messages or []:
        tool_call_id = getattr(message, "tool_call_id", None)
        if tool_call_id is not None:
            if tool_call_id in known_ids:
                sanitized.append(message)
            else:
                dropped += 1
            continue
        sanitized.append(message)
    if dropped:
        logger.warning(
            "Dropped %d tool message(s) whose tool_call_id was not in history",
            dropped,
        )
    return sanitized


class OpenAIProvider:
    """Provider wrapping `langchain_openai.ChatOpenAI`.

    - OpenAI o-series (o1/o3/o4) auto-detected → pass `reasoning_effort: high`
      when tier=smart (per plan §2.3).
    - Skip Anthropic-proprietary params (no `thinking`, no `output_config.effort`).
    - Custom `OPENAI_BASE_URL` is honored for local / proxy backends.
    """

    def __init__(self, base_url: str | None = None, provider_label: str = "openai") -> None:
        self._explicit_base_url = base_url
        self._label = provider_label
        self._model_cache: dict[tuple[str, int, str | None], ChatOpenAI] = {}

    def provider_name(self) -> str:
        return self._label

    def is_reasoning_model(self, name: str) -> bool:
        # OpenAI-specific: o-series detect via name prefix.
        n = (name or "").lower()
        if n.startswith(("o1", "o3", "o4")):
            return True
        return is_reasoning_model_name(name)

    def supports_thinking(self, tier: ModelTier) -> bool:
        # OpenAI has no `thinking` param; "thinking" is via `reasoning_effort`.
        # We treat reasoning_effort as a stand-in: supported only on o-series + tier=smart.
        if tier != "smart":
            return False
        return self.is_reasoning_model(self.resolve_model_name(tier))

    def resolve_model_name(self, tier: ModelTier) -> str:
        return resolve_model_name(tier)

    def _base_url(self) -> str:
        return self._explicit_base_url or config.OPENAI_BASE_URL or None

    def _get_model(self, name: str, max_tokens: int) -> ChatOpenAI:
        base_url = self._base_url()
        key = (name, max_tokens, base_url or "")
        cached = self._model_cache.get(key)
        if cached is not None:
            return cached
        kwargs: dict[str, Any] = {
            "model": name,
            "max_tokens": max_tokens,
            "api_key": config.OPENAI_API_KEY,
        }
        if base_url:
            kwargs["base_url"] = base_url
        model = ChatOpenAI(**kwargs)
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
        if tier == "fast":
            max_tokens = config.AGENT_MAX_OUTPUT_TOKENS_FAST
        elif tier == "balanced":
            max_tokens = config.AGENT_MAX_OUTPUT_TOKENS_FAST
        else:  # smart
            max_tokens = config.AGENT_MAX_OUTPUT_TOKENS_SMART

        sanitized = _sanitize_messages(messages)
        # Defensive: if sanitize removed everything, fall back to a
        # bare HumanMessage so `minimax-m3` does not reject the request
        # with `messages must not be empty`. This should not happen in
        # normal flow but guards against a graph state where the
        # last assistant turn left an orphan tool result.
        if not sanitized:
            sanitized = [SystemMessage(content="Bạn là trợ lý mua sắm."), HumanMessage(content="Cần tư vấn laptop.")]
            logger.warning("acomplete: sanitize dropped all messages; injecting fallback pair")

        model = self._get_model(name, max_tokens)
        # LangChain 1.x: ChatModel.ainvoke signature is
        # `(input, config=None, *, stop=None, **kwargs)`. The first positional
        # arg is now named `input` (was `messages` pre-1.0). Pass via `input=`
        # to stay compatible with both the strict and permissive parsers.
        invoke_kwargs: dict[str, Any] = {
            "input": sanitized,
            "max_tokens": max_tokens,
        }

        if tier == "smart" and self.is_reasoning_model(name):
            invoke_kwargs["reasoning_effort"] = opts.get("effort", "high")

        if tools:
            # langchain-openai 1.3.x: passing `tools=...` to ainvoke puts raw
            # BaseTool objects into the OpenAI request body, which pydantic
            # cannot serialize (StructuredTool.args_schema is a Pydantic class,
            # not an instance → "ModelMetaclass" error). bind_tools() converts
            # tools to the dict format OpenAI expects.
            model = model.bind_tools(tools)

        return await model.ainvoke(**invoke_kwargs)

    async def astream(
        self,
        messages: list[BaseMessage],
        *,
        tier: ModelTier,
        **opts: Any,
    ) -> AsyncIterator[AIMessage]:
        response = await self.acomplete(messages, tier=tier, tools=opts.get("tools"))
        yield response


__all__ = ["OpenAIProvider"]
