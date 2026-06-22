"""GET /api/search — Meilisearch with Postgres FTS fallback."""
from __future__ import annotations

import asyncio
import logging
import math
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, Query, Request

from api.dependencies import (
    get_fallback_enabled,
    get_http_client,
    get_meili_host_from_state,
    get_meili_index_from_state,
    get_search_max_limit,
)
from api.schemas import Pagination, SearchHit, SearchResponse

logger = logging.getLogger("api.search")
router = APIRouter(prefix="/api", tags=["search"])

SortKey = Literal["relevance", "price_asc", "price_desc", "newest", "name_asc"]

SORT_MAP: dict[str, str | None] = {
    "relevance": None,
    "price_asc": "price_vnd:asc",
    "price_desc": "price_vnd:desc",
    "newest": "updated_at:desc",
    "name_asc": "name:asc",
}

FACETS = ["brand", "category", "ram_gb"]


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _build_meili_filter(
    brand_csv: str | None,
    category: str | None,
    price_min: int | None,
    price_max: int | None,
    ram_gb_min: int | None,
    ram_gb_max: int | None,
    storage_gb_min: int | None,
    storage_gb_max: int | None,
    source: str,
) -> str:
    parts: list[str] = [f"source = {_quote(source)}"]

    if category:
        parts.append(f"category = {_quote(category)}")

    if brand_csv:
        brands = [b.strip() for b in brand_csv.split(",") if b.strip()]
        if len(brands) == 1:
            parts.append(f"brand = {_quote(brands[0])}")
        elif len(brands) > 1:
            joined = " OR ".join(f"brand = {_quote(b)}" for b in brands)
            parts.append(f"({joined})")

    if price_min is not None:
        parts.append(f"price_vnd >= {int(price_min)}")
    if price_max is not None:
        parts.append(f"price_vnd <= {int(price_max)}")
    if ram_gb_min is not None:
        parts.append(f"ram_gb >= {int(ram_gb_min)}")
    if ram_gb_max is not None:
        parts.append(f"ram_gb <= {int(ram_gb_max)}")
    if storage_gb_min is not None:
        parts.append(f"storage_gb >= {int(storage_gb_min)}")
    if storage_gb_max is not None:
        parts.append(f"storage_gb <= {int(storage_gb_max)}")

    return " AND ".join(parts)


def _hit_from_meili(hit: dict[str, Any]) -> SearchHit:
    raw_id = hit.get("product_id") or hit.get("id") or ""
    return SearchHit(
        id=str(raw_id),
        slug=hit.get("slug"),
        name=hit.get("name") or "",
        brand=hit.get("brand"),
        category=hit.get("category"),
        thumbnail_url=hit.get("thumbnail_url"),
        price_vnd=hit.get("price_vnd"),
        list_price_vnd=hit.get("list_price_vnd"),
        sale_price_vnd=hit.get("sale_price_vnd"),
        stock_status=hit.get("stock_status"),
        spec_summary=hit.get("spec_summary"),
    )


def _hit_from_fallback(row: dict[str, Any]) -> SearchHit:
    return SearchHit(
        id=str(row.get("id") or ""),
        slug=row.get("slug"),
        name=row.get("name") or "",
        brand=row.get("brand"),
        category=row.get("category"),
        thumbnail_url=row.get("thumbnail_url"),
        price_vnd=row.get("price_vnd"),
        list_price_vnd=None,
        sale_price_vnd=None,
        stock_status=row.get("stock_status"),
        spec_summary=None,
    )


async def _search_meili(
    http_client: httpx.AsyncClient,
    meili_host: str,
    meili_index: str,
    *,
    q: str,
    page: int,
    limit: int,
    sort: str,
    brand: str | None,
    category: str | None,
    price_min: int | None,
    price_max: int | None,
    ram_gb_min: int | None,
    ram_gb_max: int | None,
    storage_gb_min: int | None,
    storage_gb_max: int | None,
    source: str,
) -> SearchResponse:
    filter_str = _build_meili_filter(
        brand, category, price_min, price_max,
        ram_gb_min, ram_gb_max, storage_gb_min, storage_gb_max, source,
    )
    body: dict[str, Any] = {
        "q": q,
        "filter": filter_str,
        "limit": limit,
        "offset": (page - 1) * limit,
        "facets": FACETS,
    }
    sort_value = SORT_MAP.get(sort)
    if sort_value:
        body["sort"] = [sort_value]

    url = f"{meili_host.rstrip('/')}/indexes/{meili_index}/search"
    resp = await http_client.post(url, json=body)
    resp.raise_for_status()
    data = resp.json()

    hits = [_hit_from_meili(h) for h in data.get("hits", [])]
    total = int(data.get("estimatedTotalHits") or 0)
    processing = data.get("processingTimeMs")
    facets = data.get("facetDistribution") or {}

    return SearchResponse(
        query=q,
        source="meilisearch",
        fallback=False,
        hits=hits,
        facets=facets,
        pagination=Pagination(
            page=page,
            limit=limit,
            total_hits=total,
            total_pages=math.ceil(total / limit) if limit else 0,
        ),
        processing_time_ms=int(processing) if processing is not None else None,
    )


def _run_fallback_sync(
    q: str, source: str, page: int, limit: int
) -> dict[str, Any]:
    from scripts.m3_search.fallback import fallback_search
    return fallback_search(query=q, source=source, page=page, limit=limit)


async def _search_fallback(
    q: str, source: str, page: int, limit: int
) -> SearchResponse:
    data = await asyncio.to_thread(_run_fallback_sync, q, source, page, limit)
    hits = [_hit_from_fallback(h) for h in data.get("hits", [])]
    pagination = data.get("pagination") or {}
    return SearchResponse(
        query=data.get("query", q),
        source="postgres",
        fallback=True,
        hits=hits,
        facets=data.get("facets") or {},
        pagination=Pagination(
            page=int(pagination.get("page", page)),
            limit=int(pagination.get("limit", limit)),
            total_hits=int(pagination.get("total_hits", 0)),
            total_pages=int(pagination.get("total_pages", 0)),
        ),
        processing_time_ms=data.get("processing_time_ms"),
    )


@router.get("/search", response_model=SearchResponse)
async def search_products(
    request: Request,
    q: str = Query("", description="Search query string"),
    page: int = Query(1, ge=1),
    limit: int | None = Query(None, ge=1),
    sort: SortKey = Query("relevance"),
    brand: str | None = Query(None, description="Comma-separated brand names"),
    category: str | None = Query(None),
    price_min: int | None = Query(None, ge=0),
    price_max: int | None = Query(None, ge=0),
    ram_gb_min: int | None = Query(None, ge=0),
    ram_gb_max: int | None = Query(None, ge=0),
    storage_gb_min: int | None = Query(None, ge=0),
    storage_gb_max: int | None = Query(None, ge=0),
    source: str = Query("anphatpc"),
    http_client: httpx.AsyncClient = Depends(get_http_client),
    meili_host: str = Depends(get_meili_host_from_state),
    meili_index: str = Depends(get_meili_index_from_state),
) -> SearchResponse:
    max_limit = get_search_max_limit()
    effective_limit = min(limit, max_limit) if limit is not None else 24
    if effective_limit <= 0:
        effective_limit = 24

    try:
        return await _search_meili(
            http_client, meili_host, meili_index,
            q=q, page=page, limit=effective_limit, sort=sort,
            brand=brand, category=category,
            price_min=price_min, price_max=price_max,
            ram_gb_min=ram_gb_min, ram_gb_max=ram_gb_max,
            storage_gb_min=storage_gb_min, storage_gb_max=storage_gb_max,
            source=source,
        )
    except (httpx.HTTPError, httpx.RequestError, Exception) as exc:
        if not get_fallback_enabled():
            logger.exception("Meilisearch failed and fallback disabled")
            raise
        logger.warning("Meilisearch failed (%s), using Postgres fallback", exc)
        return await _search_fallback(q, source, page, effective_limit)
