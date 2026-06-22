"""Unit tests for GET /api/search (Meili success path, no live DB)."""
from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.asyncio


def _meili_ok(hits, total=1, facets=None, processing=5):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path.endswith("/indexes/products_test/search")
        return httpx.Response(
            200,
            json={
                "hits": hits,
                "query": "",
                "processingTimeMs": processing,
                "estimatedTotalHits": total,
                "facetDistribution": facets or {},
            },
        )
    return handler


async def test_search_returns_meili_hits(app, client, override_http):
    hits = [
        {
            "id": "anphatpc_abc",
            "product_id": "anphatpc:abc",
            "slug": "laptop-x",
            "name": "Laptop X",
            "brand": "ASUS",
            "category": "laptop",
            "thumbnail_url": "https://img/x.jpg",
            "price_vnd": 15000000,
            "list_price_vnd": 17000000,
            "sale_price_vnd": 15000000,
            "stock_status": "in_stock",
            "spec_summary": "i5 / 16GB / 512GB",
        }
    ]
    override_http(_meili_ok(hits, total=1, facets={"brand": {"ASUS": 1}}))
    resp = await client.get("/api/search", params={"q": "laptop"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "meilisearch"
    assert body["fallback"] is False
    assert len(body["hits"]) == 1
    assert body["hits"][0]["id"] == "anphatpc:abc"
    assert body["hits"][0]["name"] == "Laptop X"
    assert body["facets"] == {"brand": {"ASUS": 1}}
    assert body["pagination"]["total_hits"] == 1


async def test_search_invalid_sort_returns_422(app, client, override_http):
    override_http(_meili_ok([]))
    resp = await client.get("/api/search", params={"sort": "garbage"})
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "VALIDATION_ERROR"


async def test_search_unknown_brand_returns_empty_hits_200(app, client, override_http):
    override_http(_meili_ok([], total=0, facets={}))
    resp = await client.get("/api/search", params={"brand": "NoSuchBrand"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["hits"] == []
    assert body["pagination"]["total_hits"] == 0


async def test_search_meili_failure_raises_500_when_fallback_disabled(
    app, client, override_http, monkeypatch
):
    monkeypatch.setenv("SEARCH_FALLBACK_ENABLED", "false")

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("meili down")

    override_http(boom)
    resp = await client.get("/api/search", params={"q": "laptop"})
    assert resp.status_code == 500
    body = resp.json()
    assert body["code"] == "INTERNAL_ERROR"
