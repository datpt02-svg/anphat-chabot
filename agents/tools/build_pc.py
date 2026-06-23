"""M5b build_pc tool — entry point calling greedy algorithm."""
from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool
from pydantic import Field

from agents.compat.build_pc_algorithm import build_greedy
from agents.compat.schemas import BuildRequirements

logger = logging.getLogger("agents.tools.build_pc")


@tool("build_pc", args_schema=BuildRequirements)
async def build_pc(
    use_case: str = "general",
    budget_vnd: int = Field(gt=0),
    cpu_preference: str = "any",
    gpu_preference: str = "any",
    ram_min_gb: int | None = None,
    priority: str = "balanced",
    include_overclock: bool = False,
    pinned: dict[str, str] | None = None,
    conn: Any | None = None,
    source: str = "anphatpc",
) -> dict[str, Any]:
    """Đề xuất cấu hình PC hoàn chỉnh theo budget + use case.

    Trả về `PCBuild` gồm build (6 components: cpu/mobo/ram/gpu/storage/psu), tổng giá,
    kết quả compatibility, reasoning, và 1-2 alternatives.
    """
    if conn is None:
        return {"error": "no_db_conn"}
    req = BuildRequirements(
        use_case=use_case,
        budget_vnd=budget_vnd,
        cpu_preference=cpu_preference,
        gpu_preference=gpu_preference,
        ram_min_gb=ram_min_gb,
        priority=priority,
        include_overclock=include_overclock,
        pinned=pinned or {},
    )
    try:
        result = await build_greedy(conn, req, source=source)
    except Exception as exc:
        logger.exception("build_pc failed: %s", exc)
        return {"error": "build_failed", "detail": str(exc)}
    return result.model_dump()
