"""GET /api/products/{slug} and /api/products/{slug}/related."""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query

from api.dependencies import get_db_conn
from api.schemas import (
    CurrentPrice,
    ProductDetail,
    ProductNotFound,
    RelatedProduct,
    SpecItem,
    SpecsSummary,
)

logger = logging.getLogger("api.products")
router = APIRouter(prefix="/api/products", tags=["products"])


SQL_PRODUCT_BY_SLUG = """
SELECT
    p.id,
    p.slug,
    p.source,
    p.source_url,
    p.sku,
    p.name,
    p.brand,
    p.category,
    p.breadcrumbs,
    p.images,
    p.description,
    p.updated_at
FROM products p
WHERE p.slug = %s
  AND p.status = 'active'
  AND p.deleted_at IS NULL
"""

SQL_CURRENT_PRICE = """
SELECT
    price_vnd,
    list_price_vnd,
    stock_status,
    captured_at
FROM product_current_prices
WHERE product_id = %s
"""

SQL_SPECS_SUMMARY = """
SELECT
    cpu_model,
    ram_gb,
    storage_gb,
    gpu_model
FROM product_specs
WHERE product_id = %s
"""

SQL_SPEC_VALUES_GROUPED = """
SELECT
    group_name,
    spec_key,
    spec_value
FROM product_spec_values
WHERE product_id = %s
ORDER BY group_name NULLS LAST, spec_key ASC
"""

SQL_RELATED = """
SELECT
    p.id,
    p.slug,
    p.name,
    p.brand,
    p.category,
    p.thumbnail_url,
    COALESCE(cp.price_vnd, p.price_vnd) AS price_vnd,
    p.list_price_vnd,
    COALESCE(cp.stock_status, p.stock_status) AS stock_status
FROM products p
LEFT JOIN product_current_prices cp ON cp.product_id = p.id
WHERE p.category = (
        SELECT category FROM products
        WHERE slug = %s AND status = 'active' AND deleted_at IS NULL
    )
  AND p.slug <> %s
  AND p.status = 'active'
  AND p.deleted_at IS NULL
ORDER BY p.updated_at DESC
LIMIT %s
"""


def _isoformat(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


async def _row_to_product_detail(row: dict[str, Any], conn: Any) -> ProductDetail:
    product_id = row["id"]
    images = row.get("images") or []
    if isinstance(images, str):
        import json
        images = json.loads(images)
    breadcrumbs = row.get("breadcrumbs") or []
    if isinstance(breadcrumbs, str):
        import json
        breadcrumbs = json.loads(breadcrumbs)

    current_price: CurrentPrice | None = None
    async with conn.cursor() as cur:
        await cur.execute(SQL_CURRENT_PRICE, (product_id,))
        price_row = await cur.fetchone()
        if price_row:
            current_price = CurrentPrice(
                price_vnd=price_row["price_vnd"],
                list_price_vnd=price_row["list_price_vnd"],
                stock_status=price_row["stock_status"],
                captured_at=_isoformat(price_row["captured_at"]),
            )

    specs_summary: SpecsSummary | None = None
    async with conn.cursor() as cur:
        await cur.execute(SQL_SPECS_SUMMARY, (product_id,))
        summary_row = await cur.fetchone()
        if summary_row:
            specs_summary = SpecsSummary(
                cpu_model=summary_row["cpu_model"],
                ram_gb=summary_row["ram_gb"],
                storage_gb=summary_row["storage_gb"],
                gpu_model=summary_row["gpu_model"],
            )

    grouped: dict[str, list[SpecItem]] = defaultdict(list)
    async with conn.cursor() as cur:
        await cur.execute(SQL_SPEC_VALUES_GROUPED, (product_id,))
        for spec_row in await cur.fetchall():
            group = spec_row["group_name"] or "_"
            grouped[group].append(
                SpecItem(label=spec_row["spec_key"], value=spec_row["spec_value"])
            )

    return ProductDetail(
        id=product_id,
        slug=row["slug"],
        source=row["source"],
        source_url=row["source_url"],
        sku=row["sku"],
        name=row["name"],
        brand=row["brand"],
        category=row["category"],
        breadcrumbs=breadcrumbs,
        images=images,
        description=row["description"],
        current_price=current_price,
        specs_summary=specs_summary,
        specs_grouped=dict(grouped),
        updated_at=_isoformat(row["updated_at"]),
    )


@router.get("/{slug}", response_model=ProductDetail)
async def get_product(slug: str, conn: Any = Depends(get_db_conn)) -> ProductDetail:
    async with conn.cursor() as cur:
        await cur.execute(SQL_PRODUCT_BY_SLUG, (slug,))
        row = await cur.fetchone()
    if not row:
        raise ProductNotFound(
            "Product not found", details={"slug": slug}
        )
    return await _row_to_product_detail(row, conn)


@router.get("/{slug}/related", response_model=list[RelatedProduct])
async def get_related_products(
    slug: str,
    limit: int = Query(8, ge=1, le=20),
    conn: Any = Depends(get_db_conn),
) -> list[RelatedProduct]:
    async with conn.cursor() as cur:
        await cur.execute(SQL_PRODUCT_BY_SLUG, (slug,))
        if not await cur.fetchone():
            raise ProductNotFound(
                "Product not found", details={"slug": slug}
            )
        await cur.execute(SQL_RELATED, (slug, slug, limit))
        rows = await cur.fetchall()
    return [
        RelatedProduct(
            id=r["id"],
            slug=r["slug"],
            name=r["name"],
            brand=r["brand"],
            category=r["category"],
            thumbnail_url=r["thumbnail_url"],
            price_vnd=r["price_vnd"],
            list_price_vnd=r["list_price_vnd"],
            stock_status=r["stock_status"],
        )
        for r in rows
    ]
