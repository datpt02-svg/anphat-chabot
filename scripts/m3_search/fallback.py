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
  LEFT JOIN product_current_prices cp ON cp.product_id = p.id
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
  LEFT JOIN product_current_prices cp ON cp.product_id = p.id
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


def _coerce_int(value, name: str) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc


def _build_product_filter_clause(
    params: list,
    *,
    category: str | None,
    brand: str | None,
    subcategory: str | None,
    stock_status: str | None,
    price_min: int | None,
    price_max: int | None,
) -> str:
    """Build a SQL fragment that filters the `p` product row. Empty
    fragment means "no extra filter". Numeric ranges use inclusive bounds
    to match the Meili contract (`price_min: >=`, `price_max: <=`).

    Rows with an obviously broken `price_vnd` (0 or 1) are excluded so
    the chat agent never returns "1 VND" recommendations from a partial
    sync or fixture seed. Real prices start in the millions.
    """
    clauses: list[str] = [
        "COALESCE(cp.price_vnd, p.price_vnd) > 1"
    ]
    if category:
        params.append(category)
        clauses.append(f"p.category = %s")
    if subcategory:
        params.append(subcategory)
        clauses.append(f"p.subcategory = %s")
    if brand:
        params.append(brand)
        clauses.append(f"p.brand = %s")
    if stock_status:
        params.append(stock_status)
        clauses.append("COALESCE(cp.stock_status, p.stock_status) = %s")
    if price_min is not None:
        params.append(price_min)
        clauses.append("COALESCE(cp.price_vnd, p.price_vnd) >= %s")
    if price_max is not None:
        params.append(price_max)
        clauses.append("COALESCE(cp.price_vnd, p.price_vnd) <= %s")
    return " AND ".join(clauses)


def fallback_search(
    query: str,
    source: str,
    page: int = 1,
    limit: int | None = None,
    *,
    category: str | None = None,
    brand: str | None = None,
    subcategory: str | None = None,
    stock_status: str | None = None,
    price_min: int | None = None,
    price_max: int | None = None,
) -> dict:
    """Postgres-only search. Use this for the agent path so chatbot
    catalog queries go through the FTS engine (and BM25 when available)
    instead of Meilisearch.

    `query` is the free-text term. `category` / `brand` / `subcategory`
    / `stock_status` are exact-match filters. `price_min` / `price_max`
    are inclusive bounds applied on `COALESCE(current_price, list_price)`.
    """
    if page <= 0:
        raise ValueError("page must be > 0")
    limit_n = limit or DEFAULT_SEARCH_LIMIT
    if limit_n <= 0:
        raise ValueError("limit must be > 0")
    offset = (page - 1) * limit_n

    text_query = (query or "").strip()

    if not text_query:
        return _products_only_search(
            source=source,
            page=page,
            limit=limit_n,
            offset=offset,
            category=category,
            brand=brand,
            subcategory=subcategory,
            stock_status=stock_status,
            price_min=price_min,
            price_max=price_max,
        )

    return _text_search(
        text_query=text_query,
        source=source,
        page=page,
        limit=limit_n,
        offset=offset,
        category=category,
        brand=brand,
        subcategory=subcategory,
        stock_status=stock_status,
        price_min=price_min,
        price_max=price_max,
    )


def _products_only_search(
    *,
    source: str,
    page: int,
    limit: int,
    offset: int,
    category: str | None,
    brand: str | None,
    subcategory: str | None,
    stock_status: str | None,
    price_min: int | None,
    price_max: int | None,
) -> dict:
    filter_params: list = []
    product_clause = _build_product_filter_clause(
        filter_params,
        category=category,
        brand=brand,
        subcategory=subcategory,
        stock_status=stock_status,
        price_min=_coerce_int(price_min, "price_min"),
        price_max=_coerce_int(price_max, "price_max"),
    )
    product_where = (" AND " + product_clause) if product_clause else ""

    sql = f"""
    WITH paged AS (
      SELECT
        p.id,
        p.slug,
        p.name,
        p.brand,
        p.category,
        p.thumbnail_url,
        COALESCE(cp.price_vnd, p.price_vnd) AS price_vnd,
        COALESCE(cp.stock_status, p.stock_status) AS stock_status,
        0.0 AS score,
        count(*) OVER () AS total_hits
      FROM products p
      LEFT JOIN product_current_prices cp ON cp.product_id = p.id
      WHERE p.source = %s
        AND p.status = 'active'
        AND p.deleted_at IS NULL
        {product_where}
      ORDER BY
        COALESCE(cp.price_vnd, p.price_vnd) ASC NULLS LAST,
        p.id ASC
      LIMIT %s OFFSET %s
    )
    SELECT * FROM paged
    """
    params: list = [source, *filter_params, limit, offset]
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()]
    return _format(rows, "", page, limit, "products")


def _text_search(
    *,
    text_query: str,
    source: str,
    page: int,
    limit: int,
    offset: int,
    category: str | None,
    brand: str | None,
    subcategory: str | None,
    stock_status: str | None,
    price_min: int | None,
    price_max: int | None,
) -> dict:
    filter_params: list = []
    product_clause = _build_product_filter_clause(
        filter_params,
        category=category,
        brand=brand,
        subcategory=subcategory,
        stock_status=stock_status,
        price_min=_coerce_int(price_min, "price_min"),
        price_max=_coerce_int(price_max, "price_max"),
    )
    product_where = (" AND " + product_clause) if product_clause else ""

    with connect() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    SQL_BM25_FALLBACK.replace(
                        "AND p.status = 'active'\n    AND p.deleted_at IS NULL",
                        f"AND p.status = 'active' AND p.deleted_at IS NULL AND COALESCE(cp.price_vnd, p.price_vnd) > 1{product_where}",
                    ),
                    (text_query, source, *filter_params, limit, offset),
                )
                rows = [dict(r) for r in cur.fetchall()]
                return _format(rows, text_query, page, limit, "bm25")
            except Exception:
                conn.rollback()
                with conn.cursor() as cur2:
                    cur2.execute(
                        SQL_FTS_FALLBACK.replace(
                            "AND p.status = 'active'\n    AND p.deleted_at IS NULL",
                            f"AND p.status = 'active' AND p.deleted_at IS NULL AND COALESCE(cp.price_vnd, p.price_vnd) > 1{product_where}",
                        ),
                        (text_query, text_query, source, *filter_params, limit, offset),
                    )
                    rows = [dict(r) for r in cur2.fetchall()]
                    return _format(rows, text_query, page, limit, "fts")
