"""M5b xhigh effort pass-through tests for Anthropic + OpenAI providers."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("HMAC_SALT", "test_salt")
os.environ.setdefault("HMAC_SALT_VERSION", "v1")
os.environ.setdefault("JWT_SECRET", "test_secret")
os.environ.setdefault("JWT_ALGORITHM", "HS256")
os.environ.setdefault("OPENAI_API_KEY", "test_key")


def _mock_ainvoke_response():
    resp = MagicMock()
    resp.content = "ok"
    resp.tool_calls = []
    resp.response_metadata = {"model_name": "test"}
    resp.usage_metadata = {"total_tokens": 10}
    return resp


@pytest.mark.asyncio
async def test_anthropic_effort_passed_to_extra_body(monkeypatch):
    from agents import config as cfg
    from agents.providers.anthropic import AnthropicProvider

    monkeypatch.setattr(cfg, "AGENT_MAX_OUTPUT_TOKENS_SMART", 2000)
    monkeypatch.setattr(cfg, "ANTHROPIC_DEFAULT_OPUS_MODEL", "claude-opus-4-1")

    captured: dict = {}

    async def fake_ainvoke(self, **kwargs):
        captured.update(kwargs)
        return _mock_ainvoke_response()

    monkeypatch.setattr(
        "langchain_anthropic.ChatAnthropic.ainvoke", fake_ainvoke
    )

    from langchain_core.messages import HumanMessage

    provider = AnthropicProvider()
    await provider.acomplete(
        messages=[HumanMessage(content="build me a pc")],
        tier="smart",
        effort="xhigh",
    )
    assert captured["extra_body"]["output_config"]["effort"] == "xhigh"


@pytest.mark.asyncio
async def test_anthropic_default_effort_high_when_not_specified(monkeypatch):
    from agents import config as cfg
    from agents.providers.anthropic import AnthropicProvider

    monkeypatch.setattr(cfg, "AGENT_MAX_OUTPUT_TOKENS_SMART", 2000)
    monkeypatch.setattr(cfg, "ANTHROPIC_DEFAULT_OPUS_MODEL", "claude-opus-4-1")

    captured: dict = {}

    async def fake_ainvoke(self, **kwargs):
        captured.update(kwargs)
        return _mock_ainvoke_response()

    monkeypatch.setattr(
        "langchain_anthropic.ChatAnthropic.ainvoke", fake_ainvoke
    )

    from langchain_core.messages import HumanMessage

    provider = AnthropicProvider()
    await provider.acomplete(
        messages=[HumanMessage(content="hello")],
        tier="smart",
    )
    assert captured["extra_body"]["output_config"]["effort"] == "high"


@pytest.mark.asyncio
async def test_anthropic_non_reasoning_skips_effort(monkeypatch):
    from agents import config as cfg
    from agents.providers.anthropic import AnthropicProvider

    monkeypatch.setattr(cfg, "AGENT_MAX_OUTPUT_TOKENS_SMART", 2000)
    monkeypatch.setattr(cfg, "ANTHROPIC_DEFAULT_HAIKU_MODEL", "claude-haiku-4")
    monkeypatch.setattr(cfg, "LLM_DEFAULT_FAST_MODEL", "")

    captured: dict = {}

    async def fake_ainvoke(self, **kwargs):
        captured.update(kwargs)
        return _mock_ainvoke_response()

    monkeypatch.setattr(
        "langchain_anthropic.ChatAnthropic.ainvoke", fake_ainvoke
    )

    from langchain_core.messages import HumanMessage

    provider = AnthropicProvider()
    await provider.acomplete(
        messages=[HumanMessage(content="hello")],
        tier="fast",
        effort="xhigh",
    )
    assert "extra_body" not in captured


@pytest.mark.asyncio
async def test_openai_reasoning_effort_passed(monkeypatch):
    from agents import config as cfg
    from agents.providers.openai import OpenAIProvider

    monkeypatch.setattr(cfg, "AGENT_MAX_OUTPUT_TOKENS_SMART", 2000)
    monkeypatch.setattr(cfg, "LLM_DEFAULT_SMART_MODEL", "o3-mini")

    captured: dict = {}

    async def fake_ainvoke(self, **kwargs):
        captured.update(kwargs)
        return _mock_ainvoke_response()

    monkeypatch.setattr(
        "langchain_openai.ChatOpenAI.ainvoke", fake_ainvoke
    )

    from langchain_core.messages import HumanMessage

    provider = OpenAIProvider()
    await provider.acomplete(
        messages=[HumanMessage(content="build me a pc")],
        tier="smart",
        effort="xhigh",
    )
    assert captured["reasoning_effort"] == "xhigh"


@pytest.mark.asyncio
async def test_openai_non_reasoning_skips_effort(monkeypatch):
    from agents import config as cfg
    from agents.providers.openai import OpenAIProvider

    monkeypatch.setattr(cfg, "AGENT_MAX_OUTPUT_TOKENS_FAST", 512)
    monkeypatch.setattr(cfg, "LLM_DEFAULT_FAST_MODEL", "gpt-4o-mini")

    captured: dict = {}

    async def fake_ainvoke(self, **kwargs):
        captured.update(kwargs)
        return _mock_ainvoke_response()

    monkeypatch.setattr(
        "langchain_openai.ChatOpenAI.ainvoke", fake_ainvoke
    )

    from langchain_core.messages import HumanMessage

    provider = OpenAIProvider()
    await provider.acomplete(
        messages=[HumanMessage(content="hello")],
        tier="fast",
        effort="xhigh",
    )
    assert "reasoning_effort" not in captured
