"""Integration tests for /api/search and /api/health (live PostgreSQL)."""
from __future__ import annotations

import os

import pytest

from tests.api.integration.conftest import insert_test_product

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_health_ok_with_live_pg(live_app, real_client, clean_source):
    resp = await real_client.get("/api/health")
    assert resp.status_code in (200, 503)
    body = resp.json()
    assert body["postgres"] is True
    assert body["status"] in ("ok", "degraded")


async def test_search_falls_back_to_postgres_when_meili_unreachable(
    live_app, real_client, clean_source, monkeypatch
):
    insert_test_product(clean_source, "fb-laptop-1", "Laptop Fallback Test", category="laptop")
    insert_test_product(clean_source, "fb-laptop-2", "Laptop Fallback Two", category="laptop")

    class _BoomClient:
        async def get(self, *a, **kw):
            import httpx
            raise httpx.ConnectError("meili down")

        async def post(self, *a, **kw):
            import httpx
            raise httpx.ConnectError("meili down")

        async def aclose(self):
            return None

    from api.dependencies import get_http_client
    live_app.dependency_overrides[get_http_client] = lambda: _BoomClient()

    resp = await real_client.get(
        "/api/search",
        params={"q": "laptop", "source": clean_source},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["fallback"] is True
    assert body["source"] == "postgres"
