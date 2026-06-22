"""M5 search_catalog tool — Meilisearch with hard cap of 50 product summaries."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from agents import config
from agents.langgraph.state import ProductSummary

logger = logging.getLogger("agents.tools.search")


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
    from scripts.m3_search.search import (
        build_filter,
        build_sort,
        normalize_limit,
        search_products as m3_search,
    )
    from scripts.m3_search.meili import ensure_index, get_client
    from scripts.m3_search.config import get_products_index

    effective_limit = min(max(1, limit), config.MAX_RETRIEVED_PRODUCTS)
    try:
        client = get_client()
        index = ensure_index(client, get_products_index())
        filter_str = build_filter(filters or {}, source=(filters or {}).get("source", "anphatpc"))
        sort_list = build_sort(sort)
        result = await asyncio.to_thread(
            _search_sync,
            index=index,
            query=query,
            filter_str=filter_str,
            sort=sort_list,
            page=1,
            limit=effective_limit,
        )
    except Exception as exc:
        logger.exception("search_catalog failed: %s", exc)
        return []

    summaries: list[dict[str, Any]] = []
    for hit in result.get("hits", [])[: effective_limit]:
        raw_id = hit.get("product_id") or hit.get("id") or ""
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


def _search_sync(*, index: Any, query: str, filter_str: str, sort: list[str] | None, page: int, limit: int) -> dict:
    return m3_search(
        index=index,
        query=query,
        source="anphatpc",
        page=page,
        limit=normalize_limit(limit),
        sort=sort[0] if sort else None,
    )
