"""M3 search configuration."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from scripts.m2_pipeline.config import get_database_url, resolve_source as resolve_m2_source

load_dotenv(override=False)

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_BATCH_SIZE = 5000
DEFAULT_SEARCH_LIMIT = 24
DEFAULT_MAX_LIMIT = 100


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


def get_products_index() -> str:
    return os.environ.get("MEILI_PRODUCTS_INDEX") or "products"


def get_meili_timeout_seconds() -> int:
    raw = os.environ.get("MEILI_TIMEOUT_SECONDS") or "30"
    return int(raw)


def get_sync_batch_size() -> int:
    raw = os.environ.get("MEILI_SYNC_BATCH_SIZE") or str(DEFAULT_BATCH_SIZE)
    return int(raw)


def get_search_max_limit() -> int:
    raw = os.environ.get("SEARCH_MAX_LIMIT") or str(DEFAULT_MAX_LIMIT)
    return int(raw)


def fallback_enabled() -> bool:
    raw = os.environ.get("SEARCH_FALLBACK_ENABLED", "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def resolve_source(cli_value: Optional[str]) -> str:
    """Resolve source for M3 without duplicating M2's source logic.

    1. CLI --source wins.
    2. M3_TEST_SOURCE is honored only when M3_TESTING=1.
    3. Otherwise reuse M2 resolve_source(None).
    """
    if cli_value:
        return cli_value
    if os.environ.get("M3_TESTING") == "1":
        v = os.environ.get("M3_TEST_SOURCE")
        if v:
            return v
    return resolve_m2_source(None)
