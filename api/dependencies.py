"""M4/M5 lifespan and FastAPI dependencies (DB pool, HTTPx client, settings)."""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from psycopg_pool import AsyncConnectionPool
from psycopg.rows import dict_row


# Import the CopilotKit GraphQL proxy module up-front so the import-time
# parser patch in `copilotkit_graphql._install_noop_directives()` runs
# before any request handler is invoked. Without this, the patch is
# only applied when the lifespan handler reaches the GraphQL mount
# step, which may not happen for non-GraphQL probes and still leaves
# the request handler using a pristine graphql.parse for the first
# chat request.
from api.routes import copilotkit_graphql as _copilotkit_graphql_module  # noqa: F401, E402

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

load_dotenv(override=False)

from agents import config as agent_config  # noqa: E402  (after load_dotenv)

logger = logging.getLogger("api.dependencies")


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL not set. Copy .env.example to .env.")
    return url


def get_database_ro_url() -> str:
    """Read-only URL used by the agent (DB_RO_URL). Falls back to DATABASE_URL for dev."""
    return os.environ.get("DATABASE_RO_URL") or get_database_url()


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

    # M5: optional checkpointer + agent dependencies. Skipped when the package
    # isn't fully installed (e.g. running M4 alone) to keep the catalog API runnable.
    try:
        from agents.langgraph.checkpointer import build_checkpointer
        from agents.langgraph.graph import compile
        from agents.tools import ALL_TOOLS

        checkpointer, ckpt_pool = await build_checkpointer(get_database_url())
        app.state.checkpoint_pool = ckpt_pool
        app.state.checkpointer = checkpointer
        app.state.agent_graph = compile(checkpointer=checkpointer)
        app.state.agent_tools = ALL_TOOLS
        logger.info("M5 agent graph compiled")
        # M7: mount CopilotKit bridge AFTER graph is compiled so the runtime
        # endpoint sees a non-None `app.state.agent_graph`.
        from api.routes.copilotkit_bridge import mount_copilotkit_bridge

        mount_copilotkit_bridge(app)
        # Note: `mount_copilotkit_graphql` is invoked from `create_app()`
        # in api/main.py so the GraphQL route is registered at
        # construction time. The GraphQL resolver reads
        # `app.state.copilotkit_agents` lazily at request time, so we
        # just need to populate that list once the bridge is mounted.
    except Exception as exc:
        logger.warning("M5 agent graph disabled: %s", exc)
        app.state.checkpoint_pool = None
        app.state.checkpointer = None
        app.state.agent_graph = None
        app.state.agent_tools = []

    try:
        yield
    finally:
        await http_client.aclose()
        await pool.close()
        ckpt_pool = getattr(app.state, "checkpoint_pool", None)
        if ckpt_pool is not None:
            try:
                await ckpt_pool.close()
            except Exception as exc:
                logger.warning("checkpoint pool close failed: %s", exc)


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


def get_agent_graph(request: Request) -> Any:
    graph = getattr(request.app.state, "agent_graph", None)
    if graph is None:
        raise RuntimeError("M5 agent graph not initialized")
    return graph


def get_agent_tools(request: Request) -> list[Any]:
    return list(getattr(request.app.state, "agent_tools", []) or [])
