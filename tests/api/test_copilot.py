"""Audit-only tests for M5 SSE route removal.

The hand-rolled SSE endpoint at POST /api/copilotkit was removed in M7 and
replaced by the ag-ui CopilotKit bridge (api/routes/copilotkit_bridge.py).
These tests verify the M5 SSE envelope is gone — no external code path or
consumer should still see the old M5 events (`event: state`, `event: message`,
`event: citations`, etc.).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

# Ensure M5/M7 env defaults are set before importing api.* modules.
os.environ.setdefault("HMAC_SALT", "test_hmac_salt")
os.environ.setdefault("HMAC_SALT_VERSION", "v1")
os.environ.setdefault("JWT_SECRET", "test_jwt_secret")
os.environ.setdefault("JWT_ALGORITHM", "HS256")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("MEILI_HOST", "http://localhost:7700")
os.environ.setdefault("MEILI_MASTER_KEY", "test_master_key")
os.environ.setdefault("MEILI_PRODUCTS_INDEX", "products_test")
os.environ.setdefault("MEILI_TIMEOUT_SECONDS", "5")
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:5173")
os.environ.setdefault("SEARCH_FALLBACK_ENABLED", "true")
os.environ.setdefault("SEARCH_MAX_LIMIT", "100")
os.environ.setdefault("COPILOTKIT_ENABLED", "true")  # so bridge mount path runs
os.environ.setdefault("AGENT_DAILY_BUDGET_TOKENS", "2000000")
os.environ.setdefault("AGENT_BUDGET_KILL_PCT", "100")

import pytest


def _make_token(user_id: str = "u-1", is_admin: bool = False) -> str:
    import time

    import jwt

    payload = {
        "sub": user_id,
        "exp": int(time.time()) + 600,
        "roles": ["admin"] if is_admin else ["user"],
    }
    return jwt.encode(payload, os.environ["JWT_SECRET"], algorithm="HS256")


@pytest.fixture
def client():
    """M5-route-removed audit: use the real app, no agent_graph stub.

    Bridge gracefully no-ops when agent_graph is None (lifespan skipped),
    so the copilot route is NOT mounted — POST /api/copilotkit should
    return 404 (FastAPI default) or 405.
    """
    from httpx import ASGITransport, AsyncClient

    from api.main import create_app

    app = create_app()
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return AsyncClient(transport=transport, base_url="http://testserver")


@pytest.mark.asyncio
async def test_m5_route_not_mounted(client) -> None:
    """POST /api/copilotkit must NOT be a 200 SSE stream (M5 was 200 + event: state)."""
    resp = await client.post(
        "/api/copilotkit",
        json={"messages": []},
        headers={"Authorization": f"Bearer {_make_token()}"},
    )
    # 404 (route not registered) or 405 (method not allowed) — both prove M5 SSE gone.
    assert resp.status_code in (404, 405), (
        f"M5 SSE route should be removed, got status {resp.status_code}"
    )
    # Body must NOT contain M5 SSE envelope.
    body = resp.text
    assert "event: state" not in body
    assert "event: step_start" not in body
    assert "event: citations" not in body


@pytest.mark.asyncio
async def test_m5_request_shape_rejected(client) -> None:
    """M5 `CopilotRequest{messages, session_id}` shape is no longer accepted as SSE."""
    resp = await client.post(
        "/api/copilotkit",
        json={"messages": [{"role": "user", "content": "hi"}], "session_id": "x"},
        headers={"Authorization": f"Bearer {_make_token()}"},
    )
    # 404/405: route not mounted. NOT 200 (which would mean M5 SSE still active).
    assert resp.status_code in (404, 405)


def test_copilot_module_not_importable() -> None:
    """`api.routes.copilot` module must be removed in M7."""
    # If import succeeds, the file is still present — fail.
    with pytest.raises(ImportError):
        import api.routes.copilot  # noqa: F401
