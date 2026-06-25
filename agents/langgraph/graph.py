"""M5 LangGraph topology and compiled graph.

Implements the edges from M5 plan §2:
- START → classify_intent_and_extract
- classify_intent_and_extract → retrieve_catalog | retrieve_chunks_fts | reason | handoff_or_clarify
- retrieve_* → reason
- reason → call_tool | verify_grounding
- call_tool → reason (loop, max 5)
- verify_grounding → END | reason
- handoff_or_clarify → END

The `goto` field on state drives transitions. The recursion cap is enforced
by LangGraph's `recursion_limit` config.
"""
from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph

from agents.langgraph.nodes import NODE_FUNCS
from agents.langgraph.state import AgentState, RunContext

logger = logging.getLogger("agents.langgraph.graph")


def _route_from_classify(state: AgentState) -> str:
    return state.user_intent and _intent_to_node(state.user_intent) or "handoff_or_clarify"


def _intent_to_node(intent: str) -> str:
    return {
        "search": "retrieve_catalog",
        "compare": "retrieve_catalog",
        "explain": "retrieve_chunks_fts",
        "admin_debug": "retrieve_catalog",
        "build_pc": "reason",
        "check_compat": "reason",
        "find_alternative": "reason",
    }.get(intent, "handoff_or_clarify")


def _route_from_reason(state: AgentState) -> str:
    last = state.messages[-1] if state.messages else None
    if last is not None and getattr(last, "tool_calls", None):
        return "call_tool"
    return "verify_grounding"


def _route_from_retrieve_catalog(state: AgentState) -> str:
    if state.retrieved_products:
        return "reason"
    if state.user_intent in {"search", "compare"}:
        return "verify_grounding"
    return "reason"


def _route_from_verify(state: AgentState) -> str:
    last = state.messages[-1] if state.messages else None
    if getattr(last, "content", None) == "chưa có dữ liệu":
        return END
    return END if not _has_goto_reason(state) else "reason"


def _has_goto_reason(state: AgentState) -> bool:
    if not state.messages:
        return False
    last = state.messages[-1]
    return getattr(last, "content", None) and "REWRITE_NEEDED" in str(last.content)


def _route_from_clarify(state: AgentState) -> str:
    if getattr(state, "_pending_tool_calls", None) or state.clarify_count > 2:
        return "retrieve_catalog"
    return END


def _route_from_call_tool(state: AgentState) -> str:
    """After a tool call, return to `reason` unless the previous assistant
    turn was a terminal UI action (e.g. `renderLaptopSuggestions`) that
    already produced structured output for the frontend. Routing those
    back to `reason` would re-inject the same action and trip the
    recursion limit on `minimax-m3`.
    """
    last_assistant = next(
        (m for m in reversed(state.messages or []) if isinstance(m, AIMessage)),
        None,
    )
    if last_assistant is None:
        return "reason"
    tool_calls = getattr(last_assistant, "tool_calls", None) or []
    names = [
        (c.get("name") if isinstance(c, dict) else getattr(c, "name", ""))
        for c in tool_calls
    ]
    names = [n for n in names if n]
    if names and all(n == "renderLaptopSuggestions" for n in names):
        return END
    return "reason"


def build_graph() -> Any:
    g = StateGraph(AgentState)
    for name, fn in NODE_FUNCS.items():
        g.add_node(name, fn)
    g.add_edge(START, "classify_intent_and_extract")
    g.add_conditional_edges(
        "classify_intent_and_extract",
        _route_from_classify,
        {
            "retrieve_catalog": "retrieve_catalog",
            "retrieve_chunks_fts": "retrieve_chunks_fts",
            "reason": "reason",
            "handoff_or_clarify": "handoff_or_clarify",
        },
    )
    g.add_conditional_edges(
        "retrieve_catalog",
        _route_from_retrieve_catalog,
        {"reason": "reason", "verify_grounding": "verify_grounding"},
    )
    g.add_edge("retrieve_chunks_fts", "reason")
    g.add_conditional_edges(
        "reason",
        _route_from_reason,
        {"call_tool": "call_tool", "verify_grounding": "verify_grounding"},
    )
    g.add_conditional_edges(
        "call_tool",
        _route_from_call_tool,
        {"reason": "reason", END: END},
    )
    g.add_conditional_edges(
        "verify_grounding",
        _route_from_verify,
        {"reason": "reason", END: END},
    )
    g.add_conditional_edges(
        "handoff_or_clarify",
        _route_from_clarify,
        {"retrieve_catalog": "retrieve_catalog", END: END},
    )
    return g


def compile(checkpointer: Any | None = None) -> Any:
    g = build_graph()
    return g.compile(checkpointer=checkpointer)


__all__ = ["build_graph", "compile", "RunContext"]
