"""M5b build_pc algorithm unit tests (DB mocked via async cursor fake)."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("HMAC_SALT", "test_salt")
os.environ.setdefault("HMAC_SALT_VERSION", "v1")
os.environ.setdefault("JWT_SECRET", "test_secret")
os.environ.setdefault("JWT_ALGORITHM", "HS256")


class _FakeCursor:
    def __init__(self, queue: list[list[dict[str, Any]]]) -> None:
        self._queue = list(queue)
        self._exec_count = 0
        self._iter_rows: list[dict[str, Any]] = []
        self._iter_idx = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def execute(self, sql, params=None):
        self._exec_count += 1

    async def fetchall(self):
        if self._queue:
            return self._queue.pop(0)
        return []

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._iter_rows and self._queue:
            self._iter_rows = self._queue.pop(0)
        if self._iter_idx >= len(self._iter_rows):
            raise StopAsyncIteration
        r = self._iter_rows[self._iter_idx]
        self._iter_idx += 1
        return r


class _FakeConn:
    def __init__(self, cursor_queue: list[list[list[dict[str, Any]]]]) -> None:
        self._queue = list(cursor_queue)
        self._idx = 0
        self._overflow = _FakeCursor([])

    def cursor(self) -> _FakeCursor:
        if self._idx < len(self._queue):
            c = _FakeCursor(self._queue[self._idx])
            self._idx += 1
            return c
        return self._overflow


def _candidate(
    id_: str,
    price: int,
    *,
    cpu_cores: int | None = None,
    socket: str | None = None,
    ram_gb: int | None = None,
    ram_type: str | None = None,
    form_factor: str | None = None,
    gpu_model: str | None = None,
    psu_wattage_w: int | None = None,
    storage_gb: int | None = None,
    brand: str | None = "TestBrand",
) -> dict[str, Any]:
    return {
        "id": id_, "slug": id_, "name": f"Test {id_}", "brand": brand,
        "price_vnd": price,
        "cpu_cores": cpu_cores, "cpu_model": "X", "socket": socket,
        "ram_gb": ram_gb, "ram_type": ram_type, "max_ram_gb": 64,
        "gpu_model": gpu_model, "gpu_vram_gb": 8,
        "psu_wattage_w": psu_wattage_w, "recommended_psu_w": 200,
        "storage_gb": storage_gb, "storage_type": "SSD",
        "form_factor": form_factor,
        "stock_status": "in_stock",
    }


@pytest.mark.asyncio
async def test_build_greedy_allocates_all_categories():
    from agents.compat.build_pc_algorithm import build_greedy
    from agents.compat.schemas import BuildRequirements

    cpu = _candidate("cpu1", 5_000_000, cpu_cores=8, socket="AM5")
    mobo = _candidate("mobo1", 3_000_000, socket="AM5", ram_type="DDR5", form_factor="ATX")
    ram = _candidate("ram1", 1_500_000, ram_gb=16, ram_type="DDR5")
    gpu = _candidate("gpu1", 8_000_000, gpu_model="RTX 4060")
    storage = _candidate("ssd1", 1_500_000, storage_gb=512)
    psu = _candidate("psu1", 1_500_000, psu_wattage_w=650)

    conn = _FakeConn([
        [[cpu]],
        [[mobo]],
        [[ram]],
        [[gpu]],
        [[storage]],
        [[psu]],
    ])
    req = BuildRequirements(use_case="gaming", budget_vnd=30_000_000)
    build = await build_greedy(conn, req, source="anphatpc")
    cats = [c.category for c in build.build]
    assert "cpu" in cats and "mobo" in cats and "ram" in cats
    assert "gpu" in cats and "storage" in cats and "psu" in cats
    assert len(build.build) == 6


@pytest.mark.asyncio
async def test_build_greedy_picks_cheapest_when_over_budget():
    from agents.compat.build_pc_algorithm import build_greedy
    from agents.compat.schemas import BuildRequirements

    expensive_cpu = _candidate("cpu1", 50_000_000, cpu_cores=8, socket="AM5")
    cheap_cpu = _candidate("cpu2", 3_000_000, cpu_cores=4, socket="AM5")

    conn = _FakeConn([
        [[expensive_cpu]],
        [[cheap_cpu]],
        [],
        [],
        [],
        [],
        [],
    ])
    req = BuildRequirements(use_case="general", budget_vnd=10_000_000)
    build = await build_greedy(conn, req)
    cpu_component = next(c for c in build.build if c.category == "cpu")
    assert cpu_component.product_id == "cpu2"


@pytest.mark.asyncio
async def test_build_greedy_alternatives_swaps_gpu():
    from agents.compat.build_pc_algorithm import build_greedy
    from agents.compat.schemas import BuildRequirements

    cpu = _candidate("cpu1", 5_000_000, cpu_cores=8, socket="AM5")
    mobo = _candidate("m1", 3_000_000, socket="AM5", ram_type="DDR5", form_factor="ATX")
    ram = _candidate("r1", 1_500_000, ram_gb=16, ram_type="DDR5")
    gpu1 = _candidate("gpu1", 8_000_000, gpu_model="RTX 4060")
    gpu2 = _candidate("gpu2", 9_000_000, gpu_model="RTX 4070")
    storage = _candidate("s1", 1_500_000, storage_gb=512)
    psu = _candidate("p1", 1_500_000, psu_wattage_w=650)

    conn = _FakeConn([
        [[cpu]],
        [[mobo]],
        [[ram]],
        [[gpu1, gpu2]],
        [[storage]],
        [[psu]],
        [[gpu1, gpu2]],
        [[gpu1, gpu2]],
    ])
    req = BuildRequirements(use_case="gaming", budget_vnd=30_000_000)
    build = await build_greedy(conn, req)
    assert len(build.alternatives) >= 1
    alt = build.alternatives[0]
    alt_gpu = next(c for c in alt.build if c.category == "gpu")
    assert alt_gpu.product_id == "gpu2"


@pytest.mark.asyncio
async def test_score_candidate_prefers_higher_vram_for_gaming():
    from agents.compat.build_pc_algorithm import _score_candidate

    a = _candidate("a", 8_000_000, gpu_model="RTX 4060"); a["gpu_vram_gb"] = 8
    b = _candidate("b", 12_000_000, gpu_model="RTX 4070"); b["gpu_vram_gb"] = 12
    assert _score_candidate(b, "gaming") > _score_candidate(a, "gaming")
