from __future__ import annotations

import json
import os
from typing import Any
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END

os.environ["LLM_PROVIDER"] = "anthropic"
os.environ["OPENAI_API_KEY"] = "test_key"
os.environ["HMAC_SALT"] = "test_salt"
os.environ["HMAC_SALT_VERSION"] = "v1"
os.environ["JWT_SECRET"] = "test_secret"
os.environ["JWT_ALGORITHM"] = "HS256"
os.environ["DATABASE_URL"] = "postgresql://test:test@localhost:5432/test"
os.environ["LLM_DEFAULT_FAST_MODEL"] = "claude-haiku-4-5-20251001"
os.environ["LLM_DEFAULT_BALANCED_MODEL"] = "claude-sonnet-4-5-20250929"
os.environ["LLM_DEFAULT_SMART_MODEL"] = "claude-opus-4-8"
os.environ["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = "claude-haiku-4-5-20251001"
os.environ["ANTHROPIC_DEFAULT_SONNET_MODEL"] = "claude-sonnet-4-5-20250929"
os.environ["ANTHROPIC_DEFAULT_OPUS_MODEL"] = "claude-opus-4-8"

from agents.langgraph.graph import _route_from_retrieve_catalog, _route_from_verify
from agents.langgraph.nodes import call_tool, reason, retrieve_catalog
from agents.langgraph.state import AgentState, ProductSummary, RunContext
from agents.tools.search import _normalize_search_filters


PRODUCT_SUMMARIES = [
    ProductSummary(
        product_id="anphatpc:laptop-a",
        slug="laptop-a",
        title="Laptop A",
        price=18_000_000,
        in_stock=True,
    ),
    ProductSummary(
        product_id="anphatpc:laptop-b",
        slug="laptop-b",
        title="Laptop B",
        price=19_500_000,
        in_stock=True,
    ),
]

PRODUCT_DETAILS = [
    {
        "slug": "laptop-a",
        "current_price": {"price_vnd": 18_000_000},
        "specs_summary": {
            "cpu_model": "Ryzen 5",
            "ram_gb": 16,
            "storage_gb": 512,
            "gpu_model": "RTX 4050",
        },
    },
    {
        "slug": "laptop-b",
        "current_price": {"price_vnd": 19_500_000},
        "specs_summary": {
            "cpu_model": "Core i5",
            "ram_gb": 16,
            "storage_gb": 512,
            "gpu_model": "RTX 3050",
        },
    },
]


class _ProviderStub:
    def __init__(self, response: AIMessage) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    async def acomplete(self, **kwargs: Any) -> AIMessage:
        self.calls.append(kwargs)
        return self._response


class _FakeSearchTool:
    def __init__(self, result: list[dict[str, Any]]) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def ainvoke(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        self.calls.append(args)
        return self.result


class _FakeGetProductTool:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self.items = list(items)
        self.calls: list[dict[str, Any]] = []

    async def ainvoke(self, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(args)
        return self.items.pop(0)


class _NoopStep:
    def add_metadata(self, key: str, value: Any) -> None:
        return None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _NoopTracker:
    def __call__(self, *args: Any, **kwargs: Any) -> _NoopStep:
        return _NoopStep()


def test_normalize_search_filters_maps_aliases_and_drops_unknowns() -> None:
    out = _normalize_search_filters(
        {
            "category": "laptop",
            "min_price": 10_000_000,
            "max_price": 20_000_000,
            "source": "anphatpc",
            "budget_vnd": 20_000_000,
        }
    )
    assert out == {
        "category": "laptop",
        "price_min": 10_000_000,
        "price_max": 20_000_000,
    }


def test_normalize_search_filters_keeps_allowed_keys() -> None:
    out = _normalize_search_filters({"brand": "ASUS", "price_max": 20_000_000})
    assert out == {"brand": "ASUS", "price_max": 20_000_000}


@pytest.mark.asyncio
async def test_retrieve_catalog_no_hits_returns_fallback_message(monkeypatch) -> None:
    fake_search = _FakeSearchTool([])
    monkeypatch.setattr("agents.langgraph.nodes.search_catalog", fake_search)

    state = AgentState(
        user_intent="search",
        messages=[HumanMessage(content="tôi muốn mua laptop 20 triệu")],
    )

    result = await retrieve_catalog(state, config={"configurable": {"run_context": RunContext()}})

    assert result["goto"] == "verify_grounding"
    assert result["early_response"] == "chưa có dữ liệu"
    assert result["messages"][0].content == "chưa có dữ liệu"
    assert fake_search.calls[0]["limit"] > 0


@pytest.mark.asyncio
async def test_retrieve_catalog_biases_budget_laptop_queries(monkeypatch) -> None:
    fake_search = _FakeSearchTool([p.model_dump() for p in PRODUCT_SUMMARIES])
    monkeypatch.setattr("agents.langgraph.nodes.search_catalog", fake_search)

    state = AgentState(
        user_intent="search",
        filters={"category": "laptop", "price_max": 20_000_000},
        messages=[HumanMessage(content="tôi muốn mua laptop 20 triệu")],
    )

    result = await retrieve_catalog(state, config={"configurable": {"run_context": RunContext()}})

    assert result["goto"] == "reason"
    assert fake_search.calls[0]["sort"] == "price_asc"
    assert fake_search.calls[0]["limit"] == 8


def test_graph_routes_empty_search_to_verify_then_end() -> None:
    state = AgentState(user_intent="search", retrieved_products=[])
    assert _route_from_retrieve_catalog(state) == "verify_grounding"

    state.messages = [AIMessage(content="chưa có dữ liệu")]
    assert _route_from_verify(state) == END


@pytest.mark.asyncio
async def test_reason_drops_malformed_tool_calls(monkeypatch) -> None:
    provider = _ProviderStub(
        AIMessage(
            content="",
            tool_calls=[
                {"id": None, "name": "search_catalog", "args": {}},
                {"id": "", "name": "get_product", "args": {}},
            ],
        )
    )
    monkeypatch.setattr("agents.langgraph.nodes.get_provider", lambda: provider)

    state = AgentState(
        user_intent="compare",
        messages=[HumanMessage(content="so sánh 2 laptop")],
    )

    result = await reason(state, config={"configurable": {"run_context": RunContext()}})

    assert result["goto"] == "verify_grounding"
    assert result["messages"][0].tool_calls == []


@pytest.mark.asyncio
async def test_reason_rewrites_classifier_json_into_laptop_suggestions(monkeypatch) -> None:
    provider = _ProviderStub(
        AIMessage(
            content='{"intent":"search","filters":{"price_max":20000000,"category":"laptop"},"product_ids":[]}'
        )
    )
    fake_get_product = _FakeGetProductTool(PRODUCT_DETAILS)
    monkeypatch.setattr("agents.langgraph.nodes.get_provider", lambda: provider)
    monkeypatch.setattr("agents.langgraph.nodes.get_product", fake_get_product)
    monkeypatch.setattr("agents.langgraph.nodes._track_step", _NoopTracker())

    state = AgentState(
        user_intent="search",
        retrieved_products=PRODUCT_SUMMARIES,
        messages=[HumanMessage(content="tôi muốn mua laptop 20 triệu")],
    )

    result = await reason(state, config={"configurable": {"run_context": RunContext()}})

    # The new wiring forces a `renderLaptopSuggestions` tool call when
    # grounded products exist, so the node now goes through `call_tool`.
    assert result["goto"] == "call_tool"
    pending = result.get("_pending_tool_calls") or []
    assert any(call.get("name") == "renderLaptopSuggestions" for call in pending)
    render_call = next(c for c in pending if c.get("name") == "renderLaptopSuggestions")
    args = json.loads(render_call["args"]) if isinstance(render_call.get("args"), str) else render_call["args"]
    products = args.get("products") or []
    titles = [p.get("title") for p in products]
    assert "Laptop A" in titles
    assert "Laptop B" in titles
    assert all(p.get("url") for p in products)
    assert provider.calls[0]["tier"] == "balanced"
    assert provider.calls[0]["tools"] == []


@pytest.mark.asyncio
async def test_call_tool_skips_missing_tool_call_id() -> None:
    state = AgentState(
        messages=[AIMessage(content="", tool_calls=[{"id": None, "name": "search_catalog", "args": {}}])],
    )

    result = await call_tool(state, config={"configurable": {"run_context": RunContext()}})

    assert result == {"goto": "verify_grounding"}


def test_provider_sanitize_drops_orphan_tool_messages() -> None:
    from langchain_core.messages import HumanMessage, ToolMessage

    from agents.providers.openai import _sanitize_messages

    known_id = "call_known_1"
    orphan_id = "call_orphan_99"
    messages = [
        HumanMessage(content="hi"),
        AIMessage(
            content="",
            tool_calls=[{"id": known_id, "name": "search_catalog", "args": {}}],
        ),
        ToolMessage(content="ok", tool_call_id=known_id),
        # Orphan result that the upstream server never saw an assistant
        # tool_call for. Without scrubbing the next provider call fails
        # with `tool result's tool id ... not found (2013)`.
        ToolMessage(content="stale", tool_call_id=orphan_id),
    ]
    sanitized = _sanitize_messages(messages)
    surviving_ids = [getattr(m, "tool_call_id", None) for m in sanitized]
    assert orphan_id not in surviving_ids
    assert known_id in surviving_ids
