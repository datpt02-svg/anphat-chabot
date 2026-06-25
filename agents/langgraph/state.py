"""M5 graph state and runtime context models.

`AgentState` is what gets persisted to the Postgres checkpointer.
`RunContext` is per-invocation mutable state that must NOT be checkpointed
(see M5 plan §2 Runtime Context).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Annotated

from pydantic import BaseModel, ConfigDict
from langchain_core.messages import BaseMessage
from langgraph.graph import add_messages


class ProductSummary(BaseModel):
    """Compact product view returned to the LLM. No full specs."""

    model_config = ConfigDict(extra="forbid")

    product_id: str
    slug: str
    title: str
    price: int | None = None
    in_stock: bool | None = None


class RetrievedChunk(BaseModel):
    """A single chunk returned by FTS or Meilisearch."""

    model_config = ConfigDict(extra="forbid")

    product_id: str
    content: str
    source: str  # "fts" | "meilisearch"


class Citation(BaseModel):
    """Structured citation emitted with the final answer."""

    model_config = ConfigDict(extra="forbid")

    product_id: str
    slug: str
    url: str
    claim: str


class AgentState(BaseModel):
    """Persisted state of the LangGraph workflow."""

    model_config = ConfigDict(extra="ignore")

    state_version: int = 1
    # `messages` uses LangGraph's `add_messages` reducer so that
    # returning `{"messages": [new_msg]}` from a node APPENDS to the
    # channel rather than replacing it. Without the reducer, each
    # node's response overwrites the previous turn's history — which
    # is what triggered the `messages must not be empty` rejection from
    # `minimax-m3` after the first reason/call_tool cycle.
    messages: Annotated[list[BaseMessage], add_messages] = []
    user_intent: str = ""
    filters: dict = {}
    retrieved_products: list[ProductSummary] = []
    retrieved_chunks: list[RetrievedChunk] = []
    compare_list: list[str] = []  # anphatpc:123 format
    citations: list[Citation] = []
    clarify_count: int = 0
    session_id: str = ""
    user_id_hash: str | None = None
    is_admin: bool = False


@dataclass
class RunContext:
    """Per-invocation mutable state. NOT checkpointed.

    `stream_started` is set by the `reason` node when the first LLM token
    arrives. The SSE heartbeat task awaits this Event to know when to stop
    emitting heartbeats.
    """

    stream_started: asyncio.Event = field(default_factory=asyncio.Event)
    trace_id: str = ""
    cancelled: bool = False
    session_id: str = ""
    user_id_hash: str | None = None
    is_admin: bool = False
