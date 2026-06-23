"""M5b get_graph_neighbors tool — recursive CTE traversal of `graph_edges`."""
from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from agents.compat.schemas import GetGraphNeighborsInput

logger = logging.getLogger("agents.tools.graph")


_SQL_NEIGHBORS = """
WITH RECURSIVE neighbors AS (
    SELECT src, dst, relation, 1 AS depth
    FROM graph_edges
    WHERE src = %s AND (%s = 'all' OR relation = %s)
    UNION ALL
    SELECT e.src, e.dst, e.relation, n.depth + 1
    FROM graph_edges e
    JOIN neighbors n ON (e.src = n.dst OR e.dst = n.src)
    WHERE n.depth < %s AND (%s = 'all' OR e.relation = %s)
)
SELECT src, dst, relation, MIN(depth) AS depth
FROM neighbors
GROUP BY src, dst, relation
ORDER BY depth ASC, src ASC, dst ASC
LIMIT 200
"""


@tool("get_graph_neighbors", args_schema=GetGraphNeighborsInput)
async def get_graph_neighbors(
    product_id: str,
    relation: str = "all",
    max_depth: int = 1,
    conn: Any | None = None,
) -> dict[str, Any]:
    """Traverse graph_edges tìm sản phẩm liên quan (compatible_with, substitutes, uses_socket, fits_in).

    `max_depth` 1-3. Trả về `{product_id, relation, max_depth, neighbors: [{src, dst, relation, depth}, ...]}`.
    """
    if conn is None:
        return {"error": "no_db_conn"}
    if not 1 <= max_depth <= 3:
        return {"error": "invalid_max_depth", "min": 1, "max": 3}
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                _SQL_NEIGHBORS,
                (product_id, relation, relation, max_depth, relation, relation),
            )
            rows = await cur.fetchall()
    except Exception as exc:
        logger.warning("get_graph_neighbors query failed: %s", exc)
        return {"error": "db_query_failed", "detail": str(exc)}

    neighbors = [
        {"src": r["src"], "dst": r["dst"], "relation": r["relation"], "depth": int(r["depth"])}
        for r in rows
    ]
    return {
        "product_id": product_id,
        "relation": relation,
        "max_depth": max_depth,
        "neighbors": neighbors,
    }
