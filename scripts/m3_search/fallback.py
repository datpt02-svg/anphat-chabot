"""PostgreSQL fallback search for degraded Meilisearch mode."""
from __future__ import annotations

import math

from scripts.m3_search.config import DEFAULT_SEARCH_LIMIT
from scripts.m3_search.db import connect

SQL_BM25_FALLBACK = """
WITH best_chunk_per_product AS (
  SELECT DISTINCT ON (p.id)
    p.id AS product_id,
    paradedb.score(pc.id) AS score
  FROM product_chunks pc
  JOIN products p ON p.id = pc.product_id
  WHERE pc.content @@@ %s
    AND p.source = %s
    AND p.status = 'active'
    AND p.deleted_at IS NULL
  ORDER BY p.id, paradedb.score(pc.id) DESC
), ranked AS (
  SELECT
    b.product_id,
    b.score,
    count(*) OVER () AS total_hits
  FROM best_chunk_per_product b
  ORDER BY b.score DESC, b.product_id ASC
  LIMIT %s OFFSET %s
)
SELECT
  p.id,
  p.slug,
  p.name,
  p.brand,
  p.category,
  p.thumbnail_url,
  COALESCE(cp.price_vnd, p.price_vnd) AS price_vnd,
  COALESCE(cp.stock_status, p.stock_status) AS stock_status,
  ranked.score,
  ranked.total_hits
FROM ranked
JOIN products p ON p.id = ranked.product_id
LEFT JOIN product_current_prices cp ON cp.product_id = p.id
ORDER BY ranked.score DESC, p.id ASC
"""

SQL_FTS_FALLBACK = """
WITH ranked_chunks AS (
  SELECT
    pc.product_id,
    max(ts_rank(pc.search_vector, plainto_tsquery('simple', %s))) AS score
  FROM product_chunks pc
  JOIN products p ON p.id = pc.product_id
  WHERE pc.search_vector @@ plainto_tsquery('simple', %s)
    AND p.source = %s
    AND p.status = 'active'
    AND p.deleted_at IS NULL
  GROUP BY pc.product_id
), ranked AS (
  SELECT
    product_id,
    score,
    count(*) OVER () AS total_hits
  FROM ranked_chunks
  ORDER BY score DESC, product_id ASC
  LIMIT %s OFFSET %s
)
SELECT
  p.id,
  p.slug,
  p.name,
  p.brand,
  p.category,
  p.thumbnail_url,
  COALESCE(cp.price_vnd, p.price_vnd) AS price_vnd,
  COALESCE(cp.stock_status, p.stock_status) AS stock_status,
  ranked.score,
  ranked.total_hits
FROM ranked
JOIN products p ON p.id = ranked.product_id
LEFT JOIN product_current_prices cp ON cp.product_id = p.id
ORDER BY ranked.score DESC, p.id ASC
"""


def _format(rows: list[dict], query: str, page: int, limit: int, engine: str) -> dict:
    total = int(rows[0]["total_hits"]) if rows else 0
    hits = []
    for row in rows:
        hits.append({
            "id": row["id"],
            "slug": row["slug"],
            "name": row["name"],
            "brand": row["brand"],
            "category": row["category"],
            "thumbnail_url": row["thumbnail_url"],
            "price_vnd": row["price_vnd"],
            "stock_status": row["stock_status"],
            "score": float(row["score"] or 0),
        })
    return {
        "query": query,
        "source": "postgres_fallback",
        "fallback": True,
        "fallback_engine": engine,
        "hits": hits,
        "facets": {},
        "pagination": {
            "page": page,
            "limit": limit,
            "total_hits": total,
            "total_pages": math.ceil(total / limit) if limit else 0,
        },
        "processing_time_ms": None,
    }


def fallback_search(query: str, source: str, page: int = 1, limit: int | None = None) -> dict:
    if page <= 0:
        raise ValueError("page must be > 0")
    limit_n = limit or DEFAULT_SEARCH_LIMIT
    if limit_n <= 0:
        raise ValueError("limit must be > 0")
    offset = (page - 1) * limit_n
    with connect() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(SQL_BM25_FALLBACK, (query, source, limit_n, offset))
                return _format([dict(r) for r in cur.fetchall()], query, page, limit_n, "bm25")
            except Exception:
                conn.rollback()
                with conn.cursor() as cur2:
                    cur2.execute(SQL_FTS_FALLBACK, (query, query, source, limit_n, offset))
                    return _format([dict(r) for r in cur2.fetchall()], query, page, limit_n, "fts")
