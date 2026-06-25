"""M5 tool registry — exports the tools the agent can call.

Per M5 plan §4: 5 tools, each with its own timeout, hard cap, and admin gate.
M5b adds 3 tools: build_pc, check_compatibility, get_graph_neighbors.
"""
from __future__ import annotations

from langchain_core.tools import BaseTool

from agents.tools.admin import read_crawl_debug
from agents.tools.build_pc import build_pc
from agents.tools.compatibility import check_compatibility
from agents.tools.graph import get_graph_neighbors
from agents.tools.products import compare_products, explain_specs, get_product
from agents.tools.render import renderLaptopSuggestions
from agents.tools.search import search_catalog

ALL_TOOLS: list[BaseTool] = [
    search_catalog,
    get_product,
    compare_products,
    explain_specs,
    read_crawl_debug,
    build_pc,
    check_compatibility,
    get_graph_neighbors,
]

# UI-only render tools. The LLM never invokes these; the agent's
# `reason()` node calls them from the node layer after grounding so
# the React side can render structured cards.
RENDER_TOOLS: list[BaseTool] = [renderLaptopSuggestions]

__all__ = [
    "ALL_TOOLS",
    "RENDER_TOOLS",
    "build_pc",
    "check_compatibility",
    "compare_products",
    "explain_specs",
    "get_graph_neighbors",
    "get_product",
    "read_crawl_debug",
    "renderLaptopSuggestions",
    "search_catalog",
]
