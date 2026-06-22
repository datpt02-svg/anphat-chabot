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
from api.routes import copilot, health, products, search
from api.schemas import APIError, ErrorResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("api")


def create_app() -> FastAPI:
    app = FastAPI(
        title="An Phat Catalog API",
        version="0.4.0",
        lifespan=lifespan,
    )

    origins = get_cors_allowed_origins()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["X-Trace-Id"],
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
    app.include_router(copilot.router)

    return app


app = create_app()
