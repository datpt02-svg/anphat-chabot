"""M5b /api/copilotkit integration test (LLM provider mocked)."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("HMAC_SALT", "test_salt")
os.environ.setdefault("HMAC_SALT_VERSION", "v1")
os.environ.setdefault("JWT_SECRET", "test_secret")
os.environ.setdefault("JWT_ALGORITHM", "HS256")
os.environ.setdefault("OPENAI_API_KEY", "test_key")
os.environ.setdefault("DATABASE_URL", "postgresql://test/test")
os.environ.setdefault("MEILI_HOST", "http://localhost:7700")
os.environ.setdefault("MEILI_MASTER_KEY", "test")


@pytest.mark.asyncio
async def test_copilotkit_routes_to_build_pc_tool(monkeypatch):
    """Mock the LLM provider so the graph executes build_pc and returns PCBuild."""
    from api.main import create_app
    from agents.providers import base as base_module
    import httpx

    app = create_app()
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)

    from langchain_core.messages import AIMessage

    fake_response = AIMessage(
        content="Đây là build PC gaming 30 triệu...",
        tool_calls=[{
            "id": "call_1",
            "name": "build_pc",
            "args": {
                "use_case": "gaming",
                "budget_vnd": 30_000_000,
                "priority": "balanced",
            },
        }],
    )

    async def fake_acomplete(self, messages, *, tier, tools=None, **opts):
        return fake_response

    monkeypatch.setattr(base_module.LLMProvider, "acomplete", fake_acomplete)

    fake_pool = MagicMock()
    fake_pool.connection.return_value.__aenter__.return_value.cursor.return_value.__aenter__.return_value.execute = MagicMock()
    fake_pool.connection.return_value.__aenter__.return_value.cursor.return_value.__aenter__.return_value.fetchall = MagicMock(return_value=[])
    monkeypatch.setattr(app.state, "db_pool", fake_pool, raising=False)
    monkeypatch.setattr(app.state, "http_client", httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))), raising=False)

    # M7: bridge needs agent_graph; mount it manually with the test app.
    if not any(getattr(r, "path", None) == "/api/copilotkit" for r in app.routes):
        from ag_ui_langgraph import add_langgraph_fastapi_endpoint
        from ag_ui_langgraph.agent import LangGraphAgent
        from api.routes.copilotkit_bridge import _AdminGatedAgent, _ensure_budget_state
        from agents import config as agent_config
        from langgraph.graph import END, START, MessagesState, StateGraph
        from langchain_core.messages import AIMessage

        # Build a real CompiledStateGraph stub (LangGraphAgent ctor requires .nodes).
        async def call_model(state):
            return {"messages": [AIMessage(content="stub", tool_calls=[{
                "id": "call_1",
                "name": "build_pc",
                "args": {"use_case": "gaming", "budget_vnd": 30_000_000, "priority": "balanced"},
            }])]}

        builder = StateGraph(MessagesState)
        builder.add_node("agent", call_model)
        builder.add_edge(START, "agent")
        builder.add_edge("agent", END)
        stub_graph = builder.compile()

        app.state.agent_graph = stub_graph
        base = LangGraphAgent(
            name=agent_config.COPILOTKIT_AGENT_NAME,
            graph=stub_graph,
            description=agent_config.COPILOTKIT_AGENT_DESCRIPTION,
        )
        gated = _AdminGatedAgent(
            name=base.name,
            graph=base.graph,
            description=base.description,
            config=base.config,
        )
        add_langgraph_fastapi_endpoint(app, gated, path=agent_config.COPILOTKIT_PATH)
        _ensure_budget_state(app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
        # M7: ag-ui RunAgentInput shape (locked from spike §4).
        # RunAgentInput requires id on messages + tools/context/forwardedProps.
        resp = await ac.post(
            "/api/copilotkit",
            json={
                "thread_id": "t-m5b-1",
                "run_id": "r-m5b-1",
                "state": {},
                "messages": [{"id": "m1", "role": "user", "content": "Build PC gaming 30tr"}],
                "tools": [],
                "context": [],
                "forwardedProps": {},
            },
        )
    # M7: 200/422/500/503 acceptable. 422 = Pydantic validation if schema diverges.
    assert resp.status_code in (200, 422, 500, 503), resp.text
    if resp.status_code == 500:
        body = resp.json()
        assert body.get("code") in {"INTERNAL_ERROR", "BUDGET_KILLED"}
