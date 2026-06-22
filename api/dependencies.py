"""M4 lifespan and FastAPI dependencies (DB pool, HTTPx client, settings)."""
from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from psycopg_pool import AsyncConnectionPool
from psycopg.rows import dict_row

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

load_dotenv(override=False)


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL not set. Copy .env.example to .env.")
    return url


def get_cors_allowed_origins() -> list[str]:
    raw = os.environ.get("CORS_ALLOWED_ORIGINS", "")
    return [o.strip() for o in raw.split(",") if o.strip()]


def get_meili_host() -> str:
    host = os.environ.get("MEILI_HOST")
    if not host:
        raise RuntimeError("MEILI_HOST not set. Copy .env.example to .env.")
    return host


def get_meili_master_key() -> str:
    key = os.environ.get("MEILI_MASTER_KEY")
    if not key:
        raise RuntimeError("MEILI_MASTER_KEY not set. Copy .env.example to .env.")
    return key


def get_meili_index() -> str:
    return os.environ.get("MEILI_PRODUCTS_INDEX") or "products"


def get_meili_timeout_seconds() -> float:
    return float(os.environ.get("MEILI_TIMEOUT_SECONDS") or "30")


def get_search_max_limit() -> int:
    return int(os.environ.get("SEARCH_MAX_LIMIT") or "100")


def get_fallback_enabled() -> bool:
    raw = os.environ.get("SEARCH_FALLBACK_ENABLED", "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    pool = AsyncConnectionPool(
        conninfo=get_database_url(),
        kwargs={"row_factory": dict_row},
        open=False,
    )
    await pool.open()
    http_client = httpx.AsyncClient(
        timeout=get_meili_timeout_seconds(),
        headers={"Authorization": f"Bearer {get_meili_master_key()}"},
    )
    app.state.db_pool = pool
    app.state.http_client = http_client
    app.state.meili_host = get_meili_host()
    app.state.meili_index = get_meili_index()
    try:
        yield
    finally:
        await http_client.aclose()
        await pool.close()


async def get_db_conn(request: Request) -> AsyncIterator[Any]:
    pool: AsyncConnectionPool = request.app.state.db_pool
    async with pool.connection() as conn:
        yield conn


def get_http_client(request: Request) -> httpx.AsyncClient:
    return request.app.state.http_client


def get_meili_host_from_state(request: Request) -> str:
    return request.app.state.meili_host


def get_meili_index_from_state(request: Request) -> str:
    return request.app.state.meili_index
