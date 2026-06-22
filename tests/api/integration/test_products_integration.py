"""Integration tests for /api/products routes (live PostgreSQL)."""
from __future__ import annotations

import pytest

from tests.api.integration.conftest import insert_test_product

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_get_product_404_for_nonexistent_slug(live_app, real_client, clean_source):
    resp = await real_client.get("/api/products/does-not-exist")
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == "PRODUCT_NOT_FOUND"


async def test_get_product_returns_active_product(live_app, real_client, clean_source):
    insert_test_product(
        clean_source, "laptop-int-1", "Laptop Integration 1",
        category="laptop", brand="ASUS", price_vnd=12345678,
    )
    resp = await real_client.get("/api/products/laptop-int-1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["slug"] == "laptop-int-1"
    assert body["name"] == "Laptop Integration 1"
    assert body["brand"] == "ASUS"
    assert body["category"] == "laptop"


async def test_get_related_returns_other_products_in_category(
    live_app, real_client, clean_source
):
    insert_test_product(clean_source, "laptop-rel-1", "Laptop R1", category="laptop")
    insert_test_product(clean_source, "laptop-rel-2", "Laptop R2", category="laptop")
    insert_test_product(clean_source, "laptop-rel-3", "Laptop R3", category="laptop")
    insert_test_product(clean_source, "mouse-1", "Mouse M1", category="mouse")

    resp = await real_client.get("/api/products/laptop-rel-1/related")
    assert resp.status_code == 200
    body = resp.json()
    slugs = [r["slug"] for r in body]
    assert "laptop-rel-2" in slugs
    assert "laptop-rel-3" in slugs
    assert "mouse-1" not in slugs
    assert "laptop-rel-1" not in slugs


async def test_get_related_404_when_source_missing(live_app, real_client, clean_source):
    resp = await real_client.get("/api/products/missing-thing/related")
    assert resp.status_code == 404
