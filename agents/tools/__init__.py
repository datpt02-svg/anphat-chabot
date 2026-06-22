"""M5 tool registry — exports the tools the agent can call.

Per M5 plan §4: 5 tools, each with its own timeout, hard cap, and admin gate.
"""
from __future__ import annotations

from langchain_core.tools import BaseTool

from agents.tools.admin import read_crawl_debug
from agents.tools.products import compare_products, explain_specs, get_product
from agents.tools.search import search_catalog

ALL_TOOLS: list[BaseTool] = [
    search_catalog,
    get_product,
    compare_products,
    explain_specs,
    read_crawl_debug,
]

__all__ = [
    "ALL_TOOLS",
    "compare_products",
    "explain_specs",
    "get_product",
    "read_crawl_debug",
    "search_catalog",
]
