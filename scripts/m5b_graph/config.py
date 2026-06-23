"""M5b graph configuration (env loading, source resolution)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=False)

ROOT = Path(__file__).resolve().parent.parent.parent

if sys.platform == "win32":
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL not set. Copy .env.example to .env.")
    return url


def get_default_source() -> str:
    for env in ("M2_TEST_SOURCE", "M2_SOURCE", "ANPHATPC_SOURCE"):
        v = os.environ.get(env)
        if v:
            return v
    return "anphatpc"


PRICE_DELTA_DEFAULT = 0.20
