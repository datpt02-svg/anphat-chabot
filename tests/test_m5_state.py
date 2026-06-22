"""Unit tests for M5 state models and RunContext."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

# Set env defaults before importing agents modules.
os.environ.setdefault("HMAC_SALT", "test")
os.environ.setdefault("HMAC_SALT_VERSION", "v1")
os.environ.setdefault("JWT_SECRET", "test")
os.environ.setdefault("JWT_ALGORITHM", "HS256")

import pytest  # noqa: E402

from agents.langgraph.state import (  # noqa: E402
    AgentState,
    Citation,
    ProductSummary,
    RetrievedChunk,
    RunContext,
)


def test_agent_state_defaults() -> None:
    s = AgentState()
    assert s.state_version == 1
    assert s.messages == []
    assert s.is_admin is False
    assert s.clarify_count == 0
    assert s.citations == []


def test_product_summary_compact() -> None:
    p = ProductSummary(product_id="anphatpc:1", slug="abc", title="Laptop", price=15000000)
    assert p.price == 15000000
    assert p.in_stock is None


def test_citation_has_url() -> None:
    c = Citation(product_id="anphatpc:1", slug="abc", url="https://x/abc.html", claim="15tr")
    assert c.url.endswith(".html")


def test_retrieved_chunk_max_length() -> None:
    c = RetrievedChunk(product_id="anphatpc:1", content="x" * 5000, source="fts")
    assert c.source == "fts"
    assert len(c.content) == 5000


def test_run_context_event_is_per_instance() -> None:
    rc1 = RunContext()
    rc2 = RunContext()
    assert rc1.stream_started is not rc2.stream_started


def test_run_context_event_set_resolves_wait() -> None:
    rc = RunContext()
    async def driver() -> None:
        await asyncio.sleep(0.01)
        rc.stream_started.set()
    async def waiter() -> bool:
        try:
            await asyncio.wait_for(rc.stream_started.wait(), timeout=1)
        except asyncio.TimeoutError:
            return False
        return True
    asyncio.run(driver())
    assert asyncio.run(waiter()) is True
