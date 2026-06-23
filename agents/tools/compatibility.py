"""M5b tools: check_compatibility, build_pc, get_graph_neighbors."""
from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from agents.compat.rules import evaluate as evaluate_compat

logger = logging.getLogger("agents.tools.compatibility")


class CheckCompatibilityInput(BaseModel):
    items: list[str] = Field(min_length=2, max_length=10, description="product_id hoặc slug (2-10 items)")


_SQL_ITEMS = """
SELECT
    p.id,
    p.slug,
    p.name,
    p.category,
    p.subcategory,
    p.brand,
    ps.socket,
    ps.cpu_cores,
    ps.cpu_model,
    ps.cpu_threads,
    ps.ram_type,
    ps.ram_gb,
    ps.ram_slots,
    ps.max_ram_gb,
    ps.form_factor,
    ps.supported_mainboard_form_factors,
    ps.psu_wattage_w,
    ps.recommended_psu_w,
    ps.gpu_model,
    ps.gpu_vram_gb,
    ps.warnings,
    COALESCE(cp.price_vnd, p.price_vnd) AS price_vnd
FROM products p
LEFT JOIN product_specs ps ON ps.product_id = p.id
LEFT JOIN product_current_prices cp ON cp.product_id = p.id
WHERE p.status = 'active' AND p.deleted_at IS NULL
  AND (p.id = ANY(%s) OR p.slug = ANY(%s))
"""


def _resolve_items_sync(conn: Any, items: list[str]) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(_SQL_ITEMS, (items, items))
        return [dict(r) for r in cur.fetchall()]


async def _resolve_items_async(conn: Any, items: list[str]) -> list[dict[str, Any]]:
    async with conn.cursor() as cur:
        await cur.execute(_SQL_ITEMS, (items, items))
        return [dict(r) async for r in cur]


def _missing(items_requested: list[str], items_resolved: list[dict]) -> list[str]:
    found = {i["id"] for i in items_resolved} | {i["slug"] for i in items_resolved}
    return [k for k in items_requested if k not in found]


@tool("check_compatibility", args_schema=CheckCompatibilityInput)
async def check_compatibility(items: list[str], conn: Any | None = None) -> dict[str, Any]:
    """Kiểm tra tương thích giữa các linh kiện PC (CPU, Mobo, RAM, GPU, PSU, Case).

    Trả về `{compatible, issues, warnings, psu_wattage_required, psu_wattage_recommended, total_price_vnd, items}`.
    Mỗi issue chứa `pair: (id_a, id_b)`, `rule`, `detail`, `severity`.
    """
    if conn is None:
        return {"error": "no_db_conn"}

    try:
        resolved = await _resolve_items_async(conn, items)
    except Exception as exc:
        logger.warning("check_compatibility query failed: %s", exc)
        return {"error": "db_query_failed", "detail": str(exc)}

    missing = _missing(items, resolved)
    if missing:
        return {"error": "items_not_found", "missing": missing}
    if len(resolved) < 2:
        return {"error": "need_at_least_2_items", "resolved": len(resolved)}

    result = evaluate_compat(resolved)
    return result.model_dump()
