"""M5 read_crawl_debug tool — admin-only, audit-logged, stripped from Langfuse."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from agents.tools.admin_audit import log_admin_action

logger = logging.getLogger("agents.tools.admin")


class ReadCrawlDebugInput(BaseModel):
    product_id_or_url: str = Field(description="anphatpc:123 ID or source_url")


@tool("read_crawl_debug", args_schema=ReadCrawlDebugInput)
async def read_crawl_debug(
    product_id_or_url: str,
    *,
    is_admin: bool = False,
    user_id_hash: str | None = None,
    trace_id: str = "",
    conn: Any | None = None,
) -> dict[str, Any]:
    """(Admin-only) Trả về dữ liệu crawl thô cho debugging. Payload bị strip khỏi Langfuse và ghi vào `admin_audit`."""
    if not is_admin:
        return {"error": "forbidden", "reason": "admin_only"}

    if conn is None:
        return {"error": "missing_connection", "tool": "read_crawl_debug"}

    sql = (
        "SELECT id, source_url, source_file, payload, payload_hash, line_number "
        "FROM raw_data WHERE source_url = %s ORDER BY created_at DESC LIMIT 1"
    )
    try:
        async with conn.cursor() as cur:
            await cur.execute(sql, (product_id_or_url,))
            row = await cur.fetchone()
    except Exception as exc:
        logger.exception("read_crawl_debug failed: %s", exc)
        return {"error": "db_error", "message": str(exc)}

    if not row:
        result: dict[str, Any] = {"product_id_or_url": product_id_or_url, "found": False}
    else:
        payload = row.get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                pass
        result = {
            "product_id_or_url": product_id_or_url,
            "found": True,
            "raw_id": row.get("id"),
            "payload": payload,
            "payload_hash": row.get("payload_hash"),
            "source_file": row.get("source_file"),
            "line_number": row.get("line_number"),
        }

    response_size = len(json.dumps(result, default=str).encode("utf-8"))
    await asyncio.shield(
        log_admin_action(
            conn,
            user_id_hash=user_id_hash or "unknown",
            action="read_crawl_debug",
            target_id=product_id_or_url,
            response_size_bytes=response_size,
            trace_id=trace_id,
        )
    )
    return result
