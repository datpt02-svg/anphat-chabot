"""UI-only render tools.

`renderLaptopSuggestions` is a placeholder for the CopilotKit React
client: the agent emits it as a tool call after grounding a search query
against the catalog; the React side listens via `useCopilotAction` and
renders the structured `products` payload as cards.

We expose it as a regular `@tool` so the LangChain call loop in
`call_tool` produces a normal `ToolMessage` for it. The actual
side-effect lives on the frontend — this tool is a no-op on the
backend, but it must still be runnable end-to-end so the `tool_call_id`
contract with the provider is preserved.
"""
from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field


class ProductCard(BaseModel):
    title: str
    slug: str
    price_text: str
    stock_text: str
    cpu: str | None = None
    ram: str | None = None
    storage: str | None = None
    gpu: str | None = None
    screen: str | None = None
    url: str


class RenderLaptopSuggestionsInput(BaseModel):
    intro: str = Field(default="", description="Short lead-in text the chat panel shows above the card list")
    products: list[ProductCard] = Field(default_factory=list)


@tool("renderLaptopSuggestions", args_schema=RenderLaptopSuggestionsInput)
async def renderLaptopSuggestions(intro: str = "", products: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Hiển thị danh sách laptop gợi ý dưới dạng card trên UI. Trả về echo để hoàn tất tool cycle."""
    return {
        "rendered": True,
        "count": len(products or []),
    }
