"""M5 Step Streaming tests (vast-painting-sparkle plan §7.2).

Covers StepTracker: duration accuracy (±10ms tolerance), schema validation,
failure states, metadata appending, SHOW_AGENT_STEPS toggle, queue backpressure
(QueueFull → drop + log), tool_call granularity (3 tools = 3 events).
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("HMAC_SALT", "test_salt")
os.environ.setdefault("HMAC_SALT_VERSION", "v1")
os.environ.setdefault("JWT_SECRET", "test_secret")
os.environ.setdefault("JWT_ALGORITHM", "HS256")


# ---------------------------------------------------------------------------
# Step event schema tests
# ---------------------------------------------------------------------------
def test_step_start_event_shape():
    from agents.observability.steps import StepStartEvent, StepType

    evt = StepStartEvent(
        step_id="abc",
        step_type=StepType.REASON,
        label="thinking",
        parent_step_id=None,
        metadata={"k": "v"},
    )
    assert evt.step_id == "abc"
    assert evt.step_type == StepType.REASON
    assert evt.label == "thinking"
    assert evt.parent_step_id is None
    assert evt.metadata == {"k": "v"}


def test_step_end_event_shape():
    from agents.observability.steps import StepEndEvent

    evt = StepEndEvent(step_id="abc", status="completed", duration_ms=123, output_summary="ok")
    assert evt.status == "completed"
    assert evt.duration_ms == 123


def test_step_metadata_event_shape():
    from agents.observability.steps import StepMetadataEvent

    evt = StepMetadataEvent(step_id="abc", key="tokens", value=100)
    assert evt.key == "tokens"
    assert evt.value == 100


# ---------------------------------------------------------------------------
# StepTracker lifecycle
# ---------------------------------------------------------------------------
def test_step_tracker_emits_start_and_end():
    from agents.observability.steps import StepTracker, StepType

    events: list = []
    tracker = StepTracker(StepType.REASON, emit_fn=events.append)
    with tracker as step:
        assert step.step_id
        time.sleep(0.02)
    assert len(events) == 2
    assert events[0]["event"] == "step_start"
    assert events[1]["event"] == "step_end"
    assert events[1]["data"]["status"] == "completed"
    assert events[1]["data"]["duration_ms"] >= 20  # tolerance ±10ms


def test_step_tracker_duration_under_10ms_tolerance():
    from agents.observability.steps import StepTracker, StepType

    events: list = []
    tracker = StepTracker(StepType.CLASSIFY, emit_fn=events.append)
    with tracker:
        pass
    # Wall-clock ≈ 0-2ms; duration_ms must be within ±10ms
    d = events[1]["data"]["duration_ms"]
    assert 0 <= d <= 10


def test_step_tracker_status_failed_on_exception():
    from agents.observability.steps import StepTracker, StepType

    events: list = []
    tracker = StepTracker(StepType.REASON, emit_fn=events.append)
    with pytest.raises(RuntimeError, match="boom"):
        with tracker:
            raise RuntimeError("boom")
    assert events[1]["data"]["status"] == "failed"
    assert events[1]["data"]["metadata"]["error"] == "boom"


def test_step_tracker_status_cancelled_on_cancelled_error():
    from agents.observability.steps import StepTracker, StepType

    events: list = []
    tracker = StepTracker(StepType.REASON, emit_fn=events.append)
    with pytest.raises(asyncio.CancelledError):
        with tracker:
            raise asyncio.CancelledError()
    assert events[1]["data"]["status"] == "cancelled"


def test_step_tracker_add_metadata_outside_block_raises():
    from agents.observability.steps import StepTracker, StepType

    tracker = StepTracker(StepType.REASON)
    with pytest.raises(RuntimeError, match="outside"):
        tracker.add_metadata("k", "v")


def test_step_tracker_add_metadata_after_exit_warns(caplog):
    from agents.observability.steps import StepTracker, StepType

    tracker = StepTracker(StepType.REASON, emit_fn=lambda _p: None)
    with tracker:
        pass
    # After exit: log warning, ignore
    import logging
    with caplog.at_level(logging.WARNING):
        tracker.add_metadata("late", "value")
    assert any("after __exit__" in r.message for r in caplog.records)


def test_step_tracker_metadata_appends():
    from agents.observability.steps import StepTracker, StepType

    events: list = []
    with StepTracker(StepType.TOOL_CALL, emit_fn=events.append, metadata={"tool_name": "search"}) as step:
        step.add_metadata("status", "ok")
        step.add_metadata("hits", 3)
    # events: [step_start, step_metadata(status), step_metadata(hits), step_end]
    end = next(e for e in events if e["event"] == "step_end")
    assert end["data"]["metadata"] == {
        "tool_name": "search",
        "status": "ok",
        "hits": 3,
    }


# ---------------------------------------------------------------------------
# Queue backpressure
# ---------------------------------------------------------------------------
def test_step_tracker_drops_event_on_queue_full(caplog):
    from agents.observability.steps import StepTracker, StepType

    full_queue: asyncio.Queue = asyncio.Queue(maxsize=1)
    full_queue.put_nowait({"prefilled": True})

    def emit(payload):
        full_queue.put_nowait(payload)  # raises QueueFull

    import logging
    tracker = StepTracker(StepType.REASON, emit_fn=emit)
    with caplog.at_level(logging.WARNING):
        with tracker:
            pass
    assert any("emit dropped" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# SHOW_AGENT_STEPS toggle
# ---------------------------------------------------------------------------
def test_step_tracker_skips_emit_when_disabled(monkeypatch):
    from agents import observability
    from agents.observability import steps as steps_mod

    monkeypatch.setattr(steps_mod, "STEP_DISPLAY_ENABLED", False)

    events: list = []
    tracker = steps_mod.StepTracker(steps_mod.StepType.REASON, emit_fn=events.append)
    with tracker:
        tracker.add_metadata("k", "v")
    assert events == []


# ---------------------------------------------------------------------------
# Tool call granularity
# ---------------------------------------------------------------------------
def test_tool_call_granularity_three_separate_events():
    """Plan §4.5: 3 tool calls in 1 reason = 3 separate `tool_call` step events."""
    from agents.observability.steps import StepTracker, StepType

    events: list = []
    tool_names = ["search_catalog", "get_product", "compare_products"]
    for name in tool_names:
        with StepTracker(StepType.TOOL_CALL, emit_fn=events.append, metadata={"tool_name": name}):
            pass
    # 3 starts + 3 ends = 6 events
    starts = [e for e in events if e["event"] == "step_start"]
    ends = [e for e in events if e["event"] == "step_end"]
    assert len(starts) == 3
    assert len(ends) == 3
    # Each start has metadata.tool_name
    for start, name in zip(starts, tool_names):
        assert start["data"]["metadata"]["tool_name"] == name


# ---------------------------------------------------------------------------
# Icon set + label lookup (M8 contract)
# ---------------------------------------------------------------------------
def test_icon_set_complete():
    from agents.observability.steps import STEP_ICONS, STEP_LABELS, StepType

    for st in StepType:
        assert st in STEP_ICONS, f"missing icon for {st}"
        assert st in STEP_LABELS, f"missing label for {st}"


def test_tool_labels_resolve():
    from agents.observability.steps import StepType, label_for, TOOL_LABELS

    assert label_for(StepType.TOOL_CALL, {"tool_name": "search_catalog"}) == TOOL_LABELS["search_catalog"]
    assert label_for(StepType.TOOL_CALL, {"tool_name": "unknown"}) == "Đang thực thi tool..."
    assert label_for(StepType.REASON) == "Đang suy nghĩ..."
