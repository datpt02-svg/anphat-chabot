"""agents.langgraph: LangGraph AI agent graph definition."""
from __future__ import annotations

from agents.langgraph.state import AgentState, RunContext


def build_graph():
    from agents.langgraph.graph import build_graph as _build_graph

    return _build_graph()


def compile(checkpointer=None):
    from agents.langgraph.graph import compile as _compile

    return _compile(checkpointer=checkpointer)


__all__ = ["AgentState", "RunContext", "build_graph", "compile"]
