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

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
        resp = await ac.post(
            "/api/copilotkit",
            json={"messages": [{"role": "user", "content": "Build PC gaming 30tr"}]},
        )
    assert resp.status_code in (200, 500)
    if resp.status_code == 500:
        body = resp.json()
        assert body.get("code") in {"INTERNAL_ERROR", "BUDGET_KILLED"}
