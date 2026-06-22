"""M4 integration test fixtures (live PostgreSQL)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(ROOT))

os.environ["DATABASE_URL"] = "postgresql://anphat:anphat_dev_password@localhost:5432/anphat_commerce"
os.environ["MEILI_HOST"] = "http://localhost:7700"
os.environ["MEILI_MASTER_KEY"] = "anphat_meili_dev_master_key"
os.environ["MEILI_PRODUCTS_INDEX"] = "products"
os.environ["CORS_ALLOWED_ORIGINS"] = "http://localhost:3000"
os.environ["SEARCH_FALLBACK_ENABLED"] = "true"


@pytest.fixture
def app():
    from api.main import create_app
    return create_app()


@pytest.fixture
async def real_client(app):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac
    http = getattr(app.state, "http_client", None)
    if http is not None:
        await http.aclose()
    pool = getattr(app.state, "db_pool", None)
    if pool is not None:
        await pool.close()


@pytest.fixture
async def live_app(app, real_client):
    from api.dependencies import get_db_conn, get_http_client, lifespan

    async with lifespan(app):
        async def _conn_override():
            pool = app.state.db_pool
            async with pool.connection() as conn:
                yield conn

        app.dependency_overrides[get_db_conn] = _conn_override
        app.dependency_overrides[get_http_client] = lambda: app.state.http_client
        yield app
    app.dependency_overrides.clear()


def insert_test_product(
    source: str,
    slug: str,
    name: str,
    *,
    category: str = "laptop",
    brand: str = "ASUS",
    price_vnd: int | None = 15000000,
    list_price_vnd: int | None = 17000000,
    stock_status: str = "in_stock",
) -> str:
    import json
    from scripts.m2_pipeline.db import connect

    product_id = f"{source}:{slug}"
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO products (
                    id, source, source_url, slug, name, brand, category,
                    breadcrumbs, images, price_vnd, list_price_vnd,
                    stock_status, status
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s,
                    %s::jsonb, %s::jsonb, %s, %s,
                    %s, 'active'
                )
                ON CONFLICT (id) DO UPDATE SET
                    name = EXCLUDED.name,
                    brand = EXCLUDED.brand,
                    category = EXCLUDED.category,
                    price_vnd = EXCLUDED.price_vnd,
                    list_price_vnd = EXCLUDED.list_price_vnd,
                    stock_status = EXCLUDED.stock_status,
                    status = 'active',
                    deleted_at = NULL
                """,
                (
                    product_id, source, f"https://test.local/{slug}",
                    slug, name, brand, category,
                    json.dumps(["Laptop"]), json.dumps([]),
                    price_vnd, list_price_vnd, stock_status,
                ),
            )
    return product_id
