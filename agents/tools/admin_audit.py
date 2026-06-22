"""M5 admin audit logger.

Writes to `admin_audit` for every `read_crawl_debug` execution. The insert
is wrapped in `asyncio.shield()` at the call site to guarantee durability
even when the parent task is being cancelled.
"""
from __future__ import annotations

import logging
from typing import Any

from agents.security import redact_pii

logger = logging.getLogger("agents.tools.admin")

_SQL_INSERT = """
INSERT INTO admin_audit (user_id_hash, action, target_id, response_size_bytes, trace_id)
VALUES (%s, %s, %s, %s, %s);
"""


async def log_admin_action(
    conn: Any,
    *,
    user_id_hash: str,
    action: str,
    target_id: str | None,
    response_size_bytes: int,
    trace_id: str,
) -> None:
    redacted_target = redact_pii(target_id) if target_id else None
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                _SQL_INSERT,
                (user_id_hash, action, redacted_target, response_size_bytes, trace_id),
            )
    except Exception as exc:
        logger.error("admin_audit insert failed (target=%s): %s", redacted_target, exc)
