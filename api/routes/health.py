"""GET /api/health — container orchestration healthcheck."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from httpx import HTTPError

from api.dependencies import get_db_conn, get_http_client
from api.schemas import HealthStatus

router = APIRouter(prefix="/api", tags=["health"])
logger = logging.getLogger("api.health")


async def _ping_postgres(conn: Any) -> bool:
    try:
        async with conn.cursor() as cur:
            await cur.execute("SELECT 1")
            await cur.fetchone()
        return True
    except Exception:
        logger.exception("Postgres ping failed")
        return False


async def _ping_meili(http_client: Any, meili_host: str) -> bool:
    try:
        resp = await http_client.get(f"{meili_host.rstrip('/')}/health")
        return resp.status_code == 200
    except HTTPError:
        return False
    except Exception:
        logger.exception("Meilisearch ping failed")
        return False


@router.get("/health", response_model=HealthStatus)
async def health_check(
    request: Request,
    conn: Any = Depends(get_db_conn),
    http_client: Any = Depends(get_http_client),
) -> JSONResponse:
    meili_host: str = request.app.state.meili_host
    pg_ok, meili_ok = await _ping_postgres(conn), await _ping_meili(http_client, meili_host)
    healthy = pg_ok and meili_ok
    body = HealthStatus(
        status="ok" if healthy else "degraded",
        postgres=pg_ok,
        meilisearch=meili_ok,
    )
    return JSONResponse(status_code=200 if healthy else 503, content=body.model_dump())
