"""M8a REST wrapper for the M5b build_pc tool.

Plan M8a §7.1: POST /api/build_pc, Bearer auth, 30s timeout, 403 if
non-admin pins products, Pydantic revalidate the PCBuild response.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from agents.compat.build_pc_algorithm import build_greedy
from agents.compat.schemas import BuildRequirements, PCBuild
from agents.security import TokenClaims, verify_token
from api.dependencies import get_db_conn

logger = logging.getLogger("api.build_pc")

router = APIRouter(prefix="/api", tags=["build_pc"])


async def _require_user(claims: TokenClaims = Depends(verify_token)) -> TokenClaims:
    if not claims:
        raise HTTPException(status_code=401, detail="unauthorized")
    return claims


@router.post("/build_pc", response_model=PCBuild)
async def build_pc_endpoint(
    req: BuildRequirements,
    claims: TokenClaims = Depends(_require_user),
    conn: Any = Depends(get_db_conn),
) -> PCBuild:
    # 403: non-admin cannot pin specific products.
    if req.pinned and not claims.is_admin:
        raise HTTPException(
            status_code=403,
            detail={"error": "pinned requires admin role", "code": "ADMIN_REQUIRED"},
        )

    # Direct call to build_greedy — bypass LangChain tool overhead.
    try:
        result: PCBuild = await asyncio.wait_for(
            build_greedy(conn, req, source="anphatpc"),
            timeout=30.0,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail={"error": "build_pc timeout (30s)", "code": "BUILD_PC_TIMEOUT"},
        )
    except Exception as exc:
        logger.exception("build_pc failed")
        raise HTTPException(
            status_code=500,
            detail={"error": f"build_pc failed: {exc}", "code": "BUILD_PC_ERROR"},
        )

    # Re-validate with Pydantic — fail loud if shape drift.
    return PCBuild.model_validate(result.model_dump())
