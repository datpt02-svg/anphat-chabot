"""M5 tracing: Langfuse CallbackHandler with local JSON fallback.

When `LANGFUSE_PUBLIC_KEY` is empty, the `LocalFallbackHandler` writes spans
to a structured JSON log. This keeps the agent debuggable when Langfuse is
down or unconfigured.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from agents import config

logger = logging.getLogger("agents.tracing")


@dataclass
class _Span:
    name: str
    start: float
    parent_id: str | None = None
    span_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    metadata: dict[str, Any] = field(default_factory=dict)
    children: list["_Span"] = field(default_factory=list)
    end: float | None = None
    error: str | None = None

    def finish(self, error: str | None = None) -> None:
        self.end = time.perf_counter()
        self.error = error


class LocalFallbackHandler:
    """Minimal callback handler that logs spans as JSON lines.

    Mirrors a subset of the LangChain `BaseCallbackHandler` interface so it
    can be plugged in as a drop-in when Langfuse is unavailable.
    """

    def __init__(self) -> None:
        self._stack: list[_Span] = []
        self._root: list[_Span] = []
        self._session_id: str = ""
        self._user_id_hash: str | None = None
        self._intent: str = ""
        self._product_ids: list[str] = []
        self._search_query: str = ""
        self._trace_id: str = uuid.uuid4().hex

    # --- public config hooks ---
    def set_session(self, session_id: str) -> None:
        self._session_id = session_id

    def set_user(self, user_id_hash: str | None) -> None:
        self._user_id_hash = user_id_hash

    def set_intent(self, intent: str) -> None:
        self._intent = intent

    def set_product_ids(self, product_ids: list[str]) -> None:
        self._product_ids = list(product_ids)

    def set_search_query(self, query: str) -> None:
        self._search_query = query

    # --- BaseCallbackHandler-compatible shims ---
    def on_chain_start(
        self, serialized: dict[str, Any] | None, inputs: dict[str, Any], **kwargs: Any
    ) -> None:
        name = (serialized or {}).get("name") or kwargs.get("name") or "chain"
        span = _Span(name=name, start=time.perf_counter())
        if self._stack:
            self._stack[-1].children.append(span)
            span.parent_id = self._stack[-1].span_id
        else:
            self._root.append(span)
        self._stack.append(span)

    def on_chain_end(self, outputs: dict[str, Any] | None, **kwargs: Any) -> None:
        if self._stack:
            self._stack.pop().finish()

    def on_chain_error(self, error: Exception | KeyboardInterrupt, **kwargs: Any) -> None:
        if self._stack:
            self._stack.pop().finish(error=str(error))

    def on_llm_start(
        self, serialized: dict[str, Any] | None, prompts: list[str], **kwargs: Any
    ) -> None:
        name = (serialized or {}).get("name") or "llm"
        span = _Span(name=name, start=time.perf_counter(), metadata={"prompts": prompts})
        if self._stack:
            self._stack[-1].children.append(span)
            span.parent_id = self._stack[-1].span_id
        else:
            self._root.append(span)
        self._stack.append(span)

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        if self._stack:
            self._stack.pop().finish()

    def on_llm_error(self, error: Exception | KeyboardInterrupt, **kwargs: Any) -> None:
        if self._stack:
            self._stack.pop().finish(error=str(error))

    def on_tool_start(
        self, serialized: dict[str, Any] | None, input_str: str, **kwargs: Any
    ) -> None:
        name = (serialized or {}).get("name") or "tool"
        span = _Span(name=name, start=time.perf_counter(), metadata={"input": input_str})
        if self._stack:
            self._stack[-1].children.append(span)
            span.parent_id = self._stack[-1].span_id
        else:
            self._root.append(span)
        self._stack.append(span)

    def on_tool_end(self, output: str, **kwargs: Any) -> None:
        if self._stack:
            self._stack.pop().finish()

    def on_tool_error(self, error: Exception | KeyboardInterrupt, **kwargs: Any) -> None:
        if self._stack:
            self._stack.pop().finish(error=str(error))

    def flush(self) -> None:
        """Emit all finished spans as JSON lines, then reset the stack."""
        if not self._root:
            return
        for span in self._root:
            logger.info(
                "trace_event=%s",
                json.dumps(_span_to_dict(
                    span, self._trace_id, self._session_id, self._user_id_hash,
                    self._intent, self._product_ids, self._search_query,
                ), default=str),
            )
        self._root = []
        self._stack = []


def _span_to_dict(span: _Span, trace_id: str, session_id: str, user_id_hash: str | None, intent: str = "", product_ids: list[str] | None = None, search_query: str = "") -> dict:
    return {
        "trace_id": trace_id,
        "session_id": session_id,
        "user_id_hash": user_id_hash,
        "intent": intent,
        "product_ids": product_ids or [],
        "search_query": search_query,
        "span_id": span.span_id,
        "parent_id": span.parent_id,
        "name": span.name,
        "start_s": span.start,
        "end_s": span.end,
        "duration_ms": ((span.end or span.start) - span.start) * 1000,
        "metadata": span.metadata,
        "error": span.error,
        "children": [_span_to_dict(c, trace_id, session_id, user_id_hash, intent, product_ids, search_query) for c in span.children],
    }


def build_handler(
    *,
    intent: str = "",
    product_ids: list[str] | None = None,
    search_query: str = "",
) -> Any:
    """Return a Langfuse CallbackHandler if configured, else the local fallback.

    The local handler is always returned in dev to avoid surprise network
    calls; in production we honor the sampling rate.
    """
    if not config.LANGFUSE_PUBLIC_KEY or not config.LANGFUSE_SECRET_KEY:
        logger.info("Langfuse not configured; using LocalFallbackHandler")
        h = LocalFallbackHandler()
        h.set_intent(intent)
        h.set_product_ids(product_ids or [])
        h.set_search_query(search_query)
        return h
    if config.LANGFUSE_SAMPLING_RATE < 1.0:
        import random
        if random.random() > config.LANGFUSE_SAMPLING_RATE:
            logger.debug("Langfuse sample skipped (rate=%s)", config.LANGFUSE_SAMPLING_RATE)
            h = LocalFallbackHandler()
            h.set_intent(intent)
            h.set_product_ids(product_ids or [])
            h.set_search_query(search_query)
            return h
    try:
        from langfuse.langchain import CallbackHandler
    except ImportError as exc:
        logger.warning("langfuse.langchain import failed (%s); using fallback", exc)
        h = LocalFallbackHandler()
        h.set_intent(intent)
        h.set_product_ids(product_ids or [])
        h.set_search_query(search_query)
        return h
    tags = [
        f"service:{config.LANGFUSE_SERVICE}",
        f"env:{config.LANGFUSE_ENVIRONMENT}",
    ]
    if intent:
        tags.append(f"intent:{intent}")
    if product_ids:
        tags.append(f"product_ids:{','.join(product_ids[:5])}")
    if search_query:
        tags.append(f"search_query:{search_query[:64]}")
    return CallbackHandler(
        public_key=config.LANGFUSE_PUBLIC_KEY,
        secret_key=config.LANGFUSE_SECRET_KEY,
        host=config.LANGFUSE_HOST or None,
        tags=tags,
    )
