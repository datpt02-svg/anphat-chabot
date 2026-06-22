"""Unit tests for /api/products routes (no live DB)."""
from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.asyncio


class _MultiCursor:
    def __init__(self, scripts: list[dict]) -> None:
        self._scripts = scripts
        self._idx = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None

    async def execute(self, sql, params=None):
        return None

    async def fetchone(self):
        if self._idx >= len(self._scripts):
            return None
        s = self._scripts[self._idx]
        self._idx += 1
        return s.get("fetchone")

    async def fetchall(self):
        if self._idx >= len(self._scripts):
            return []
        s = self._scripts[self._idx]
        self._idx += 1
        return s.get("fetchall", [])


class _ScriptedConnection:
    def __init__(self, scripts: list[dict]) -> None:
        self._scripts = scripts
        self._cursor = _MultiCursor(scripts)

    def cursor(self) -> _MultiCursor:
        return self._cursor


async def test_get_product_returns_full_detail(app, client, override_db):
    product_row = {
        "id": "anphatpc:abc123",
        "slug": "laptop-x",
        "source": "anphatpc",
        "source_url": "https://anphatpc.vn/p/laptop-x",
        "sku": "ABC123",
        "name": "Laptop X",
        "brand": "ASUS",
        "category": "laptop",
        "breadcrumbs": ["Laptop", "Laptop ASUS"],
        "images": ["https://img/a.jpg", "https://img/b.jpg"],
        "description": "<p>Desc</p>",
        "updated_at": "2026-06-22T10:00:00+00:00",
    }
    price_row = {
        "price_vnd": 15000000,
        "list_price_vnd": 17000000,
        "stock_status": "in_stock",
        "captured_at": "2026-06-22T10:00:00+00:00",
    }
    spec_row = {
        "cpu_model": "Intel Core i5-13420H",
        "ram_gb": 16,
        "storage_gb": 512,
        "gpu_model": "RTX 4050",
    }
    spec_values = [
        {"group_name": "Bộ xử lý", "spec_key": "cpu_model", "spec_value": "Intel Core i5-13420H"},
        {"group_name": "Bộ nhớ", "spec_key": "ram_gb", "spec_value": "16 GB DDR4"},
    ]
    conn = _ScriptedConnection([
        {"fetchone": product_row},
        {"fetchone": price_row},
        {"fetchone": spec_row},
        {"fetchall": spec_values},
    ])
    override_db(conn)
    resp = await client.get("/api/products/laptop-x")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == "anphatpc:abc123"
    assert body["slug"] == "laptop-x"
    assert body["breadcrumbs"] == ["Laptop", "Laptop ASUS"]
    assert body["images"] == ["https://img/a.jpg", "https://img/b.jpg"]
    assert body["current_price"]["price_vnd"] == 15000000
    assert body["specs_summary"]["ram_gb"] == 16
    assert "Bộ xử lý" in body["specs_grouped"]
    assert body["specs_grouped"]["Bộ xử lý"][0]["label"] == "cpu_model"


async def test_get_product_404_when_missing(app, client, override_db):
    conn = _ScriptedConnection([{"fetchone": None}])
    override_db(conn)
    resp = await client.get("/api/products/invalid-slug")
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == "PRODUCT_NOT_FOUND"
    assert body["details"]["slug"] == "invalid-slug"


async def test_get_related_returns_list(app, client, override_db):
    source_row = {
        "id": "anphatpc:src",
        "slug": "laptop-x",
        "source": "anphatpc",
        "source_url": None,
        "sku": None,
        "name": "Laptop X",
        "brand": "ASUS",
        "category": "laptop",
        "breadcrumbs": [],
        "images": [],
        "description": None,
        "updated_at": None,
    }
    related_rows = [
        {
            "id": "anphatpc:r1",
            "slug": "laptop-y",
            "name": "Laptop Y",
            "brand": "ASUS",
            "category": "laptop",
            "thumbnail_url": "https://img/y.jpg",
            "price_vnd": 12000000,
            "list_price_vnd": 14000000,
            "stock_status": "in_stock",
        }
    ]
    conn = _ScriptedConnection([
        {"fetchone": source_row},
        {"fetchall": related_rows},
    ])
    override_db(conn)
    resp = await client.get("/api/products/laptop-x/related", params={"limit": 5})
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) == 1
    assert body[0]["slug"] == "laptop-y"


async def test_get_related_404_when_source_missing(app, client, override_db):
    conn = _ScriptedConnection([{"fetchone": None}])
    override_db(conn)
    resp = await client.get("/api/products/missing/related")
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == "PRODUCT_NOT_FOUND"


async def test_get_product_handles_null_optional_fields(app, client, override_db):
    product_row = {
        "id": "anphatpc:min",
        "slug": "min",
        "source": "anphatpc",
        "source_url": None,
        "sku": None,
        "name": "Minimal",
        "brand": None,
        "category": "other",
        "breadcrumbs": None,
        "images": None,
        "description": None,
        "updated_at": None,
    }
    conn = _ScriptedConnection([
        {"fetchone": product_row},
        {"fetchone": None},
        {"fetchone": None},
        {"fetchall": []},
    ])
    override_db(conn)
    resp = await client.get("/api/products/min")
    assert resp.status_code == 200
    body = resp.json()
    assert body["breadcrumbs"] == []
    assert body["images"] == []
    assert body["current_price"] is None
    assert body["specs_summary"] is None
    assert body["specs_grouped"] == {}
