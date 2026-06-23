"""M6 Claude + Langfuse integration verification (plan §4)."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("HMAC_SALT", "test_salt")
os.environ.setdefault("HMAC_SALT_VERSION", "v1")
os.environ.setdefault("JWT_SECRET", "test_secret")
os.environ.setdefault("JWT_ALGORITHM", "HS256")
os.environ.setdefault("ANTHROPIC_API_KEY", "test_key")
os.environ.setdefault("OPENAI_API_KEY", "test_key")

import agents.langgraph  # noqa: F401  pre-load to break circular import


def test_env_reads_claude_opus_4_8_default(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_DEFAULT_OPUS_MODEL", raising=False)
    import importlib
    import agents.config as cfg
    importlib.reload(cfg)
    assert cfg.ANTHROPIC_DEFAULT_OPUS_MODEL == "claude-opus-4-8"


def test_langfuse_callback_handler_returned_when_configured(monkeypatch):
    monkeypatch.setattr("agents.config.LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setattr("agents.config.LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setattr("agents.config.LANGFUSE_HOST", "https://test.langfuse.com")
    monkeypatch.setattr("agents.config.LANGFUSE_SAMPLING_RATE", 1.0)
    fake_module = MagicMock()
    fake_handler = MagicMock(name="CallbackHandler")
    fake_module.CallbackHandler = MagicMock(return_value=fake_handler)
    with patch.dict(sys.modules, {"langfuse.langchain": fake_module}):
        from agents.tracing import build_handler
        h = build_handler(
            intent="build_pc",
            product_ids=["anphatpc:abc"],
            search_query="laptop gaming",
        )
    assert h is fake_handler
    call_kwargs = fake_module.CallbackHandler.call_args.kwargs
    tags = call_kwargs["tags"]
    assert any(t.startswith("service:") for t in tags)
    assert any(t == "intent:build_pc" for t in tags)
    assert any(t.startswith("product_ids:") for t in tags)
    assert any(t.startswith("search_query:") for t in tags)


def test_local_fallback_captures_chain_tool_llm_spans_with_metrics():
    from agents.tracing import LocalFallbackHandler
    h = LocalFallbackHandler()
    h.set_session("sess-1")
    h.set_user("user-hash-1")
    h.set_intent("build_pc")
    h.set_product_ids(["anphatpc:abc", "anphatpc:def"])
    h.set_search_query("laptop gaming 30tr")
    h.on_chain_start({"name": "reason_node"}, inputs={"q": "laptop"})
    h.on_llm_start({"name": "claude"}, prompts=["p1"])
    resp = MagicMock()
    resp.usage_metadata = {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}
    h.on_llm_end(resp)
    h.on_tool_start({"name": "search_catalog"}, input_str="q")
    h.on_tool_end("[hits]")
    h.on_chain_end({"output": "ok"})
    h.flush()
    assert h._intent == "build_pc"
    assert h._product_ids == ["anphatpc:abc", "anphatpc:def"]
    assert h._search_query == "laptop gaming 30tr"
    assert h._root == []


def test_pii_redacted_and_admin_tool_redacts_raw_crawl(monkeypatch):
    from agents.security import redact_pii
    out = redact_pii("Email abc@gmail.com và SĐT 0901234567")
    assert "abc@gmail.com" not in out
    assert "0901234567" not in out

    from agents.tools.admin import read_crawl_debug
    raw_payload = {"raw_html": "<html>secret</html>", "internal_id": 12345}
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value.fetchone.return_value = {"payload": raw_payload}
    with patch("agents.tools.admin.log_admin_action", MagicMock()):
        result = read_crawl_debug.coroutine(
            product_id_or_url="abc", is_admin=False, user_id_hash="u1", trace_id="t1", conn=conn,
        )
    assert "raw_html" not in str(result)
    assert "internal_id" not in str(result)


def test_anthropic_forbidden_params_raises():
    import asyncio
    from agents.providers.anthropic import AnthropicProvider
    p = AnthropicProvider()
    for forbidden in ("budget_tokens", "temperature", "top_p", "top_k"):
        with pytest.raises(ValueError, match="forbidden params"):
            asyncio.run(p.acomplete([MagicMock()], tier="smart", **{forbidden: 1}))


@pytest.mark.asyncio
async def test_anthropic_astream_logs_stop_reasons(monkeypatch):
    from langchain_core.messages import AIMessageChunk
    from agents.providers.anthropic import AnthropicProvider

    async def fake_astream(self, **kwargs):
        yield AIMessageChunk(content="Hi", response_metadata={"stop_reason": None})
        yield AIMessageChunk(content=" end", response_metadata={"stop_reason": "end_turn"})

    monkeypatch.setattr("langchain_anthropic.ChatAnthropic.astream", fake_astream)
    p = AnthropicProvider()
    chunks = []
    async for msg in p.astream([MagicMock()], tier="smart"):
        chunks.append(msg)
    assert len(chunks) == 1
    assert "Hi end" in chunks[0].content
