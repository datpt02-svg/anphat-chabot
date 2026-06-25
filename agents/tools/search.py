"""M5 search_catalog tool — Postgres full-text search (BM25 when available,
plain FTS otherwise). Meilisearch is reserved for the user-facing search
bar; the chat agent should always go through the canonical product search
that lives in `scripts.m3_search.fallback`.

The previous implementation called Meili directly, which made the agent
return 0 hits whenever the local Meili index was missing or had a
different `source` label than the local Postgres catalog.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from agents import config
from agents.langgraph.state import ProductSummary
from scripts.m3_search.fallback import fallback_search

logger = logging.getLogger("agents.tools.search")

_FILTER_ALIASES = {
    "min_price": "price_min",
    "max_price": "price_max",
}

_ALLOWED_FILTER_KEYS = {
    "category",
    "subcategory",
    "brand",
    "stock_status",
    "gpu_model",
    "cpu_model",
    "socket",
    "price_min",
    "price_max",
    "ram_min",
    "ram_max",
    "storage_min",
    "storage_max",
    "screen_min",
    "refresh_rate_min",
}


def _normalize_search_filters(filters: dict[str, Any] | None) -> dict[str, Any]:
    if not filters:
        return {}

    normalized: dict[str, Any] = {}
    dropped: set[str] = set()
    for raw_key, value in filters.items():
        key = _FILTER_ALIASES.get(str(raw_key), str(raw_key))
        if key in {"source", "sort", "limit"}:
            continue
        if key not in _ALLOWED_FILTER_KEYS:
            dropped.add(str(raw_key))
            continue
        normalized[key] = value

    if dropped:
        logger.warning("search_catalog dropped unknown filters: %s", ", ".join(sorted(dropped)))
    return normalized


class SearchCatalogInput(BaseModel):
    query: str = Field(default="", description="Free-text search query")
    filters: dict[str, Any] = Field(default_factory=dict)
    sort: str | None = Field(default=None)
    limit: int = Field(default=24, ge=1, le=config.MAX_RETRIEVED_PRODUCTS)


@tool("search_catalog", args_schema=SearchCatalogInput)
async def search_catalog(
    query: str = "",
    filters: dict[str, Any] | None = None,
    sort: str | None = None,
    limit: int = 24,
) -> list[dict[str, Any]]:
    """Tìm kiếm sản phẩm trong catalog. Trả về tối đa 50 bản ghi tóm tắt (ID/slug/title/price/stock). Không bao gồm full spec — dùng `get_product` để xem chi tiết."""
    effective_limit = min(max(1, limit), config.MAX_RETRIEVED_PRODUCTS)
    filters = filters or {}
    source = filters.get("source") or "anphatpc"
    normalized = _normalize_search_filters(filters)
    try:
        result = await asyncio.to_thread(
            _search_sync,
            query=query,
            source=source,
            filters=normalized,
            sort=sort,
            page=1,
            limit=effective_limit,
        )
    except Exception as exc:
        logger.exception("search_catalog failed: %s", exc)
        return []

    summaries: list[dict[str, Any]] = []
    for hit in result.get("hits", [])[: effective_limit]:
        raw_id = hit.get("id") or hit.get("product_id") or ""
        summaries.append(
            ProductSummary(
                product_id=str(raw_id),
                slug=hit.get("slug") or "",
                title=hit.get("name") or "",
                price=hit.get("price_vnd"),
                in_stock=(hit.get("stock_status") == "in_stock") if hit.get("stock_status") else None,
            ).model_dump()
        )
    return summaries


def _search_sync(
    *,
    query: str,
    source: str,
    filters: dict[str, Any],
    sort: str | None,
    page: int,
    limit: int,
) -> dict:
    # `sort` from the agent layer is currently best-effort. The Postgres
    # path orders by score; for `price_asc` we widen the limit so the
    # post-sort still fits the asked budget.
    sort = sort or ""
    if sort == "price_asc":
        limit = max(limit, 50)
    return fallback_search(
        query=query,
        source=source,
        page=page,
        limit=limit,
        category=filters.get("category"),
        brand=filters.get("brand"),
        subcategory=filters.get("subcategory"),
        stock_status=filters.get("stock_status"),
        price_min=filters.get("price_min"),
        price_max=filters.get("price_max"),
    )
