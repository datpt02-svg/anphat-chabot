"""Unit tests for GET /api/health."""
from __future__ import annotations

import httpx
import pytest


class _StubCursor:
    def __init__(self, fetchone_value):
        self._value = fetchone_value

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None

    async def execute(self, sql, params=None):
        return None

    async def fetchone(self):
        return self._value


class _StubConn:
    def __init__(self, fetchone_value):
        self._value = fetchone_value

    def cursor(self):
        return _StubCursor(self._value)


def _meili_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"status": "available"})


def _meili_down(request: httpx.Request) -> httpx.Response:
    return httpx.Response(503, json={"status": "down"})


def _meili_unreachable(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused")


async def test_health_ok(app, client, override_db, override_http):
    override_db(_StubConn((1,)))
    override_http(_meili_ok)
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["postgres"] is True
    assert body["meilisearch"] is True


async def test_health_degraded_when_meili_down(app, client, override_db, override_http):
    override_db(_StubConn((1,)))
    override_http(_meili_down)
    resp = await client.get("/api/health")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["postgres"] is True
    assert body["meilisearch"] is False


async def test_health_degraded_when_meili_unreachable(app, client, override_db, override_http):
    override_db(_StubConn((1,)))
    override_http(_meili_unreachable)
    resp = await client.get("/api/health")
    assert resp.status_code == 503
    body = resp.json()
    assert body["meilisearch"] is False
