"""M5b get_graph_neighbors tool unit tests (DB mocked)."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("HMAC_SALT", "test_salt")

import agents.langgraph  # noqa: F401  pre-load to break circular import


class _FakeCursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def execute(self, sql, params=None):
        return None

    async def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self._rows)


@pytest.mark.asyncio
async def test_graph_neighbors_returns_depth_1_results():
    from agents.tools.graph import get_graph_neighbors

    rows = [
        {"src": "cpu1", "dst": "mobo1", "relation": "compatible_with", "depth": 1},
        {"src": "cpu1", "dst": "mobo2", "relation": "compatible_with", "depth": 1},
    ]
    conn = _FakeConn(rows)
    fn = get_graph_neighbors.coroutine
    result = await fn(product_id="cpu1", relation="compatible_with", max_depth=1, conn=conn)
    assert result["product_id"] == "cpu1"
    assert result["relation"] == "compatible_with"
    assert result["max_depth"] == 1
    assert len(result["neighbors"]) == 2
    assert all(n["depth"] == 1 for n in result["neighbors"])


@pytest.mark.asyncio
async def test_graph_neighbors_with_relation_all():
    from agents.tools.graph import get_graph_neighbors

    rows = [
        {"src": "cpu1", "dst": "mobo1", "relation": "compatible_with", "depth": 1},
        {"src": "cpu1", "dst": "cpu2", "relation": "substitutes", "depth": 1},
    ]
    conn = _FakeConn(rows)
    fn = get_graph_neighbors.coroutine
    result = await fn(product_id="cpu1", relation="all", max_depth=2, conn=conn)
    assert len(result["neighbors"]) == 2


@pytest.mark.asyncio
async def test_graph_neighbors_no_db_conn():
    from agents.tools.graph import get_graph_neighbors

    fn = get_graph_neighbors.coroutine
    result = await fn(product_id="cpu1", relation="all", max_depth=1, conn=None)
    assert "error" in result
    assert result["error"] == "no_db_conn"


@pytest.mark.asyncio
async def test_graph_neighbors_empty_result():
    from agents.tools.graph import get_graph_neighbors

    conn = _FakeConn([])
    fn = get_graph_neighbors.coroutine
    result = await fn(product_id="lonely_node", relation="all", max_depth=1, conn=conn)
    assert result["neighbors"] == []
