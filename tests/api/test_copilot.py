"""Smoke tests for the M5 /api/copilotkit endpoint.

These tests do NOT call the LLM; they exercise auth, budget kill switch, and
the SSE framing. Live graph runs are covered by `tests/evals/test_agent_golden.py`.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

# Ensure M5 env defaults are set before importing api.* modules.
os.environ.setdefault("HMAC_SALT", "test_hmac_salt")
os.environ.setdefault("HMAC_SALT_VERSION", "v1")
os.environ.setdefault("JWT_SECRET", "test_jwt_secret")
os.environ.setdefault("JWT_ALGORITHM", "HS256")

import jwt  # noqa: E402

from tests.api.conftest import _FakeConnection, _FakeCursor, _FakePool  # noqa: E402


def _make_token(user_id: str = "u-1", is_admin: bool = False) -> str:
    import time

    payload = {
        "sub": user_id,
        "exp": int(time.time()) + 600,
        "roles": ["admin"] if is_admin else ["user"],
    }
    return jwt.encode(payload, os.environ["JWT_SECRET"], algorithm="HS256")


@pytest.fixture
def agent_app():
    """Build a FastAPI app with M5 agent deps stubbed out."""
    from api.dependencies import get_agent_graph, get_db_conn
    from api.main import create_app

    class _StubGraph:
        async def ainvoke(self, state, config):  # noqa: ANN001
            from langchain_core.messages import AIMessage

            state.messages = list(state.messages) + [AIMessage(content="stub reply")]
            return state

    app = create_app()
    app.state.agent_graph = _StubGraph()
    app.state.db_pool = _FakePool(_FakeConnection(_FakeCursor(fetchone_value={"tokens_used": 0})))

    def _stub_conn():
        return _FakeConnection(_FakeCursor(fetchone_value={"tokens_used": 0}))

    app.dependency_overrides[get_db_conn] = _stub_conn
    app.dependency_overrides[get_agent_graph] = lambda: app.state.agent_graph
    yield app
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_copilotkit_requires_token(agent_app) -> None:
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=agent_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post("/api/copilotkit", json={"messages": []})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_copilotkit_rejects_invalid_token(agent_app) -> None:
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=agent_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post(
            "/api/copilotkit",
            json={"messages": []},
            headers={"Authorization": "Bearer not-a-jwt"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_copilotkit_streams_stub_response(agent_app) -> None:
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=agent_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post(
            "/api/copilotkit",
            json={"messages": [{"role": "user", "content": "hello"}]},
            headers={"Authorization": f"Bearer {_make_token()}"},
        )
    assert resp.status_code == 200
    body = resp.text
    assert "event: state" in body
    assert "event: message" in body
    assert "stub reply" in body


@pytest.mark.asyncio
async def test_copilotkit_returns_503_when_budget_killed(agent_app) -> None:
    from httpx import ASGITransport, AsyncClient

    # Override app.state.db_pool to return a fake pool whose fetchone returns a
    # usage count past the kill threshold. The endpoint queries via the pool, not
    # the `get_db_conn` dependency, so we patch the pool directly.
    agent_app.state.db_pool = _FakePool(_FakeConnection(_FakeCursor(fetchone_value={"tokens_used": 9_999_999_999})))
    transport = ASGITransport(app=agent_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post(
            "/api/copilotkit",
            json={"messages": [{"role": "user", "content": "x"}]},
            headers={"Authorization": f"Bearer {_make_token()}"},
        )
    assert resp.status_code == 503
    assert "Retry-After" in resp.headers
