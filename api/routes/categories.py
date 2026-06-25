"""M8a GET /api/categories — public category discovery with in-memory TTL.

Plan M8a §7.2: SELECT category, count(*) FROM products WHERE active
GROUP BY category ORDER BY count DESC LIMIT 20. 5 min cache.
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends

from api.dependencies import get_db_conn

router = APIRouter(prefix="/api", tags=["categories"])

_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {"data": (0.0, [])}
CACHE_TTL_S = 300


@router.get("/categories")
async def get_categories(
    conn: Any = Depends(get_db_conn),
) -> list[dict[str, Any]]:
    now = time.time()
    cached_ts, cached_data = _cache["data"]
    if cached_data and now - cached_ts < CACHE_TTL_S:
        return cached_data
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT category, count(*) AS cnt
            FROM products
            WHERE status = 'active' AND deleted_at IS NULL
            GROUP BY category
            ORDER BY cnt DESC
            LIMIT 20
            """
        )
        rows = await cur.fetchall()
    data: list[dict[str, Any]] = [
        {"name": r["category"], "count": r["cnt"]} for r in rows
    ]
    _cache["data"] = (now, data)
    return data
