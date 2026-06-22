"""Search query helpers for Meilisearch product search."""
from __future__ import annotations

import math
from typing import Any

from scripts.m3_search.config import get_search_max_limit

FACETS_DEFAULT = ["brand", "category", "ram_gb"]

SORT_MAP = {
    "relevance": None,
    "price_asc": "price_vnd:asc",
    "price_desc": "price_vnd:desc",
    "newest": "updated_at:desc",
    "name_asc": "name:asc",
    "ram_desc": "ram_gb:desc",
    "storage_desc": "storage_gb:desc",
    "refresh_rate_desc": "refresh_rate_hz:desc",
}

_STRING_FILTERS = {
    "category": "category",
    "subcategory": "subcategory",
    "brand": "brand",
    "stock_status": "stock_status",
    "gpu_model": "gpu_model",
    "cpu_model": "cpu_model",
    "socket": "socket",
}

_NUMERIC_FILTERS = {
    "price_min": ("price_vnd", ">="),
    "price_max": ("price_vnd", "<="),
    "ram_min": ("ram_gb", ">="),
    "ram_max": ("ram_gb", "<="),
    "storage_min": ("storage_gb", ">="),
    "storage_max": ("storage_gb", "<="),
    "screen_min": ("screen_inches", ">="),
    "refresh_rate_min": ("refresh_rate_hz", ">="),
}


def quote_filter_value(value: str) -> str:
    text = str(value)
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _coerce_number(name: str, value: Any) -> int | float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be numeric") from None
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    if number.is_integer():
        return int(number)
    return number


def build_filter(params: dict[str, Any] | None, source: str) -> str:
    params = params or {}
    unknown = set(params) - set(_STRING_FILTERS) - set(_NUMERIC_FILTERS)
    if unknown:
        raise ValueError(f"Unknown filter(s): {', '.join(sorted(unknown))}")

    parts = [f"source = {quote_filter_value(source)}"]
    for param, field in _STRING_FILTERS.items():
        value = params.get(param)
        if value is not None and value != "":
            parts.append(f"{field} = {quote_filter_value(str(value))}")
    for param, (field, op) in _NUMERIC_FILTERS.items():
        value = params.get(param)
        if value is not None and value != "":
            parts.append(f"{field} {op} {_coerce_number(param, value)}")
    return " AND ".join(parts)


def build_sort(sort_key: str | None) -> list[str] | None:
    key = sort_key or "relevance"
    if key not in SORT_MAP:
        raise ValueError(f"Unknown sort: {key}")
    value = SORT_MAP[key]
    return [value] if value else None


def normalize_limit(limit: int | None) -> int:
    if limit is None:
        return 24
    if limit <= 0:
        raise ValueError("limit must be > 0")
    return min(limit, get_search_max_limit())


def _get_result(res: Any, key: str, default=None):
    if isinstance(res, dict):
        return res.get(key, default)
    snake = "".join(["_" + c.lower() if c.isupper() else c for c in key]).lstrip("_")
    return getattr(res, snake, getattr(res, key, default))


def search_products(
    index,
    query: str,
    source: str,
    filters: dict[str, Any] | None = None,
    sort: str | None = None,
    page: int = 1,
    limit: int | None = None,
    facets: list[str] | None = None,
) -> dict:
    if page <= 0:
        raise ValueError("page must be > 0")
    limit_n = normalize_limit(limit)
    offset = (page - 1) * limit_n
    options: dict[str, Any] = {
        "filter": build_filter(filters, source),
        "limit": limit_n,
        "offset": offset,
        "facets": facets or FACETS_DEFAULT,
    }
    sort_value = build_sort(sort)
    if sort_value:
        options["sort"] = sort_value

    res = index.search(query or "", options)
    hits = _get_result(res, "hits", []) or []
    total = int(_get_result(res, "estimatedTotalHits", 0) or 0)
    processing = _get_result(res, "processingTimeMs", None)
    facets_out = _get_result(res, "facetDistribution", {}) or {}
    return {
        "query": query or "",
        "source": "meilisearch",
        "fallback": False,
        "hits": hits,
        "facets": facets_out,
        "pagination": {
            "page": page,
            "limit": limit_n,
            "total_hits": total,
            "total_pages": math.ceil(total / limit_n) if limit_n else 0,
        },
        "processing_time_ms": processing,
    }
