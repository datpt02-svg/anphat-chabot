"""M2 pipeline configuration: env loading, paths, source resolution, constants."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Load .env from CWD once at import.
load_dotenv(override=False)


ROOT = Path(__file__).resolve().parent.parent.parent

DEFAULT_INPUT_PATH = "data/anphat/products_final.json"
DEFAULT_BATCH_SIZE = 500
DEFAULT_COMMIT_EVERY = 1
DEFAULT_MAX_ROWS: Optional[int] = None  # None = unlimited

# Per-batch statement timeouts.
DEFAULT_TIMEOUT_NORMAL = "300s"
DEFAULT_TIMEOUT_LARGE = "600s"  # stage 5 spec_values large DELETE/INSERT
DEFAULT_LOCK_TIMEOUT = "5s"

# M2 file-size warning threshold (above this: consider ijson streaming).
MAX_FILE_SIZE_WARN_BYTES = 200 * 1024 * 1024

# Specs chunk content - top-N wide columns in fixed order from
# db/migrations/001_init.sql. Only non-null keys are emitted.
SPECS_CHUNK_KEYS: list[str] = [
    "product_type", "model",
    "cpu_model", "cpu_cores", "cpu_threads",
    "cpu_base_clock_ghz", "cpu_boost_clock_ghz", "socket",
    "ram_gb", "ram_type", "ram_speed_mhz", "max_ram_gb",
    "storage_gb", "storage_type",
    "gpu_model", "gpu_vram_gb",
    "screen_inches", "refresh_rate_hz", "panel_type", "os",
]

# Stock status enum - per plan §"Decisions locked for M2".
STOCK_STATUSES = {"in_stock", "out_of_stock", "preorder", "unknown", "contact"}


def get_database_url() -> str:
    """Read DATABASE_URL from env. Raise if missing."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL not set. Copy .env.example to .env.")
    return url


def resolve_source(cli_value: Optional[str]) -> str:
    """Resolve the `crawl_runs.source` value per locked contract (m4 round 2):

    1. CLI flag `--source` (highest override)
    2. M2_TEST_SOURCE  (test conftest)
    3. M2_SOURCE       (CI / manual override)
    4. ANPHATPC_SOURCE (backward-compat alias)
    5. default 'anphatpc'
    """
    if cli_value:
        return cli_value
    for env_name in ("M2_TEST_SOURCE", "M2_SOURCE", "ANPHATPC_SOURCE"):
        v = os.environ.get(env_name)
        if v:
            return v
    return "anphatpc"
