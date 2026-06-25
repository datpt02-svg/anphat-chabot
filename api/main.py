"""M4 catalog API application entry point."""
from __future__ import annotations

import logging
import time

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.dependencies import get_cors_allowed_origins, lifespan
from api.routes import health, products, search
from api.routes.copilotkit_bridge import register_copilotkit_auth_middleware
from api.schemas import APIError, ErrorResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("api")


def create_app() -> FastAPI:
    app = FastAPI(
        title="An Phat Catalog API",
        version="0.5.0",
        lifespan=lifespan,
        # `add_fastapi_endpoint` mounts `/api/copilotkit/{path:path}`, so
        # `/api/copilotkit` (no path) gets a 307 redirect to `/api/copilotkit/`.
        # Browsers honour that redirect for the CORS-preflighted POST and it
        # works locally, but dev tools + some CopilotKit client paths trip
        # on it. Disable trailing-slash redirects — the SDK accepts both.
        redirect_slashes=False,
    )

    origins = get_cors_allowed_origins()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["X-Trace-Id", "X-Run-Id", "X-Thread-Id", "Content-Type"],
    )

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "%s %s -> %d (%.1fms)",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
        )
        return response

    @app.exception_handler(APIError)
    async def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
        body = ErrorResponse(error=exc.message, code=exc.code, details=exc.details)
        return JSONResponse(status_code=exc.status_code, content=body.model_dump())

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        body = ErrorResponse(
            error="Validation error",
            code="VALIDATION_ERROR",
            details={"errors": exc.errors()},
        )
        return JSONResponse(status_code=422, content=body.model_dump())

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        body = ErrorResponse(
            error=str(exc.detail) if exc.detail else "HTTP error",
            code=f"HTTP_{exc.status_code}",
            details={},
        )
        return JSONResponse(status_code=exc.status_code, content=body.model_dump())

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
        body = ErrorResponse(
            error="Internal server error",
            code="INTERNAL_ERROR",
            details={},
        )
        return JSONResponse(status_code=500, content=body.model_dump())

    app.include_router(health.router)
    app.include_router(search.router)
    app.include_router(products.router)
    from api.routes import build_pc as build_pc_route
    from api.routes import categories as categories_route

    app.include_router(build_pc_route.router)
    app.include_router(categories_route.router)
    # Auth+budget middleware for /api/copilotkit must be registered here
    # (before app start). The ag-ui endpoint itself is mounted from the
    # lifespan after the M5 graph compiles — see api/dependencies.py.
    register_copilotkit_auth_middleware(app)

    # Mount the CopilotKit GraphQL proxy here so the route is registered
    # at app construction. The GraphQL resolver looks up the agent
    # instance from `app.state.copilotkit_agents` at request time, so
    # the M5 graph doesn't need to be compiled here — it will be
    # populated by the lifespan.
    from api.routes.copilotkit_graphql import mount_copilotkit_graphql
    from agents import config as agent_config
    mount_copilotkit_graphql(app, agent_config.COPILOTKIT_PATH)

    return app


app = create_app()
