"""M5 product tools: get_product, compare_products, explain_specs.

All read from Postgres via the same async pool used by M4 catalog endpoints.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from agents.langgraph.state import Citation

logger = logging.getLogger("agents.tools.products")


class GetProductInput(BaseModel):
    product_id_or_slug: str = Field(description="anphatpc:123 ID or slug")


class CompareProductsInput(BaseModel):
    product_ids: list[str] = Field(min_length=2, max_length=5)


class ExplainSpecsInput(BaseModel):
    product_id: str
    spec_keys: list[str] = Field(default_factory=list)


_SQL_BY_ID = """
SELECT id, slug, name, brand, category, description
FROM products
WHERE id = %s AND status = 'active' AND deleted_at IS NULL
LIMIT 1;
"""

_SQL_BY_SLUG = """
SELECT id, slug, name, brand, category, description
FROM products
WHERE slug = %s AND status = 'active' AND deleted_at IS NULL
LIMIT 1;
"""

_SQL_CURRENT_PRICE = """
SELECT price_vnd, list_price_vnd, stock_status, captured_at
FROM product_current_prices
WHERE product_id = %s;
"""

_SQL_SPECS_SUMMARY = """
SELECT cpu_model, ram_gb, ram_type, storage_gb, storage_type,
       gpu_model, screen_inches, refresh_rate_hz
FROM product_specs
WHERE product_id = %s;
"""

_SQL_SPEC_VALUES = """
SELECT group_name, spec_key, spec_value
FROM product_spec_values
WHERE product_id = %s
ORDER BY group_name NULLS LAST, spec_key ASC;
"""


@tool("get_product", args_schema=GetProductInput)
async def get_product(product_id_or_slug: str, conn: Any | None = None) -> dict[str, Any]:
    """Lấy thông tin chi tiết sản phẩm từ Postgres: price, stock, specs. Trả về dict với các trường `product_id`, `slug`, `name`, `current_price`, `specs_summary`, `specs_grouped`."""
    from scripts.m2_pipeline.db import connect

    if conn is None:
        with connect() as sync_conn:
            return _get_product_sync(sync_conn, product_id_or_slug)
    return await _get_product_async(conn, product_id_or_slug)


def _resolve(sync_conn: Any, key: str) -> dict[str, Any] | None:
    sql = _SQL_BY_ID if ":" in key else _SQL_BY_SLUG
    with sync_conn.cursor() as cur:
        cur.execute(sql, (key,))
        row = cur.fetchone()
    if not row:
        return None
    return dict(row)


def _enrich(sync_conn: Any, product_id: str, base: dict[str, Any]) -> dict[str, Any]:
    base = dict(base)
    with sync_conn.cursor() as cur:
        cur.execute(_SQL_CURRENT_PRICE, (product_id,))
        price_row = cur.fetchone()
    base["current_price"] = dict(price_row) if price_row else None

    with sync_conn.cursor() as cur:
        cur.execute(_SQL_SPECS_SUMMARY, (product_id,))
        summary_row = cur.fetchone()
    base["specs_summary"] = dict(summary_row) if summary_row else None

    grouped: dict[str, list[dict[str, Any]]] = {}
    with sync_conn.cursor() as cur:
        cur.execute(_SQL_SPEC_VALUES, (product_id,))
        for spec_row in cur.fetchall():
            group = spec_row["group_name"] or "_"
            grouped.setdefault(group, []).append(
                {"label": spec_row["spec_key"], "value": spec_row["spec_value"]}
            )
    base["specs_grouped"] = grouped
    return base


def _get_product_sync(sync_conn: Any, key: str) -> dict[str, Any]:
    base = _resolve(sync_conn, key)
    if not base:
        return {"error": "not_found", "key": key}
    return _enrich(sync_conn, base["id"], base)


async def _get_product_async(conn: Any, key: str) -> dict[str, Any]:
    sql = _SQL_BY_ID if ":" in key else _SQL_BY_SLUG
    async with conn.cursor() as cur:
        await cur.execute(sql, (key,))
        row = await cur.fetchone()
    if not row:
        return {"error": "not_found", "key": key}
    base = dict(row)
    async with conn.cursor() as cur:
        await cur.execute(_SQL_CURRENT_PRICE, (base["id"],))
        price_row = await cur.fetchone()
    base["current_price"] = dict(price_row) if price_row else None

    async with conn.cursor() as cur:
        await cur.execute(_SQL_SPECS_SUMMARY, (base["id"],))
        summary_row = await cur.fetchone()
    base["specs_summary"] = dict(summary_row) if summary_row else None

    grouped: dict[str, list[dict[str, Any]]] = {}
    async with conn.cursor() as cur:
        await cur.execute(_SQL_SPEC_VALUES, (base["id"],))
        async for spec_row in cur:
            group = spec_row["group_name"] or "_"
            grouped.setdefault(group, []).append(
                {"label": spec_row["spec_key"], "value": spec_row["spec_value"]}
            )
    base["specs_grouped"] = grouped
    return base


@tool("compare_products", args_schema=CompareProductsInput)
async def compare_products(product_ids: list[str], conn: Any | None = None) -> dict[str, Any]:
    """So sánh các sản phẩm trên các thông số chính (price, CPU, RAM, GPU, screen). Trả về dict `{products: [...], compared_keys: [...]}`."""
    if not 2 <= len(product_ids) <= 5:
        return {"error": "invalid_count", "min": 2, "max": 5}

    products: list[dict[str, Any]] = []
    keys: set[str] = set()
    for pid in product_ids:
        detail = await get_product.coroutine(pid) if hasattr(get_product, "coroutine") else await get_product.ainvoke({"product_id_or_slug": pid})
        if "error" in detail:
            continue
        if detail.get("specs_summary"):
            keys.update(detail["specs_summary"].keys())
        if detail.get("current_price"):
            keys.add("price_vnd")
        products.append(
            {
                "product_id": detail.get("id") or pid,
                "slug": detail.get("slug"),
                "name": detail.get("name"),
                "current_price": (detail.get("current_price") or {}).get("price_vnd"),
                "specs_summary": detail.get("specs_summary"),
            }
        )
    return {"products": products, "compared_keys": sorted(keys)}


@tool("explain_specs", args_schema=ExplainSpecsInput)
async def explain_specs(product_id: str, spec_keys: list[str]) -> dict[str, Any]:
    """Giải thích các thông số kỹ thuật bằng tiếng Việt dễ hiểu. Trả về `{product_id, explanations: {spec_key: explanation}}` — chỉ giải thích các key được yêu cầu, dùng LLM để diễn giải từ `product_spec_values`."""
    detail = await get_product.ainvoke({"product_id_or_slug": product_id})
    if "error" in detail:
        return {"error": "not_found", "product_id": product_id}
    grouped = detail.get("specs_grouped") or {}
    flat: dict[str, str] = {}
    for items in grouped.values():
        for item in items:
            flat[item["label"]] = item["value"]
    keys = spec_keys or list(flat.keys())[:8]
    explanations: dict[str, str] = {k: flat.get(k, "(không có)") for k in keys}
    return {"product_id": product_id, "explanations": explanations}


def make_citation(product_id: str, slug: str, claim: str, base_url: str = "https://anphatpc.com.vn") -> Citation:
    return Citation(
        product_id=product_id,
        slug=slug,
        url=f"{base_url.rstrip('/')}/{slug}.html" if slug else base_url,
        claim=claim,
    )
