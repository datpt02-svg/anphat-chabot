"""M5 Postgres checkpointer setup.

The checkpointer uses a separate async connection pool so a slow graph
execution never starves the main API DB pool. Tables are pre-created in
`db/migrations/005_m5_agent_infra.sql`; `setup()` is a no-op when they exist.
"""
from __future__ import annotations

import logging

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger("agents.langgraph.checkpointer")


async def build_checkpointer(conninfo: str) -> tuple[AsyncPostgresSaver, AsyncConnectionPool]:
    """Create a checkpointer + its dedicated pool. The pool must outlive the
    graph. Callers should `await pool.close()` during application shutdown.
    """
    pool = AsyncConnectionPool(conninfo=conninfo, open=False, min_size=1, max_size=5)
    await pool.open()
    checkpointer = AsyncPostgresSaver(pool)
    await checkpointer.setup()
    logger.info("Postgres checkpointer ready")
    return checkpointer, pool


__all__ = ["build_checkpointer"]
