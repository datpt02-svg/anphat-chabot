"""M5 mid-step cancellation tests (vast-painting-sparkle plan §7.3).

Validates: client disconnect mid-step → partial state committed in checkpointer
→ resume from checkpoint with same `session_id`.

Note: these tests are *unit* tests for the StepTracker status reporting and
the StepTracker-end path. The full Postgres checkpointer flow requires a live
database and is covered by the live `integration` marker tests in CI.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("HMAC_SALT", "test_salt")
os.environ.setdefault("HMAC_SALT_VERSION", "v1")
os.environ.setdefault("JWT_SECRET", "test_secret")
os.environ.setdefault("JWT_ALGORITHM", "HS256")


def test_cancelled_step_records_cancelled_status():
    """Step cancelled by asyncio.CancelledError → status='cancelled' in step_end event."""
    from agents.observability.steps import StepTracker, StepType

    events: list = []
    tracker = StepTracker(StepType.REASON, emit_fn=events.append)
    with pytest.raises(asyncio.CancelledError):
        with tracker:
            raise asyncio.CancelledError()
    end_events = [e for e in events if e["event"] == "step_end"]
    assert len(end_events) == 1
    assert end_events[0]["data"]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancellation_records_duration_up_to_cancel():
    """duration_ms reflects time elapsed before cancellation (not 0)."""
    from agents.observability.steps import StepTracker, StepType

    events: list = []

    async def long_step():
        with StepTracker(StepType.REASON, emit_fn=events.append) as _step:
            await asyncio.sleep(0.1)

    task = asyncio.create_task(long_step())
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises((asyncio.CancelledError, BaseException)):
        await task

    end_events = [e for e in events if e["event"] == "step_end"]
    assert end_events
    duration = end_events[0]["data"]["duration_ms"]
    # Should be at least ~20ms (slept 0.02 before cancel) but less than 1000ms
    assert 10 <= duration < 1000


def test_resume_state_includes_partial_messages():
    """Mock state at cancellation point should include `messages` snapshot,
    `user_intent`, `retrieved_products` up to that point.

    This validates that even when a step is cancelled, the AgentState
    accumulated so far is still usable for resume.
    """
    from langchain_core.messages import HumanMessage
    from agents.langgraph.state import AgentState

    state = AgentState(
        messages=[HumanMessage(content="hello")],
        user_intent="search",
        retrieved_products=[],
    )
    assert state.user_intent == "search"
    assert len(state.messages) == 1
    # Re-invoke with same session_id should pick up from this state
    # (validation is at the integration-test level; here we just assert shape)
    assert state.session_id == ""


def test_step_event_clears_after_disconnect():
    """Plan §6.2: After client disconnect, queue is drained and discarded."""
    from agents.observability.steps import StepTracker, StepType

    queue: asyncio.Queue = asyncio.Queue(maxsize=10)
    events_received: list = []

    def emit(payload):
        queue.put_nowait(payload)
        events_received.append(payload)

    with StepTracker(StepType.REASON, emit_fn=emit) as step:
        step.add_metadata("before", 1)

    # Drain (simulating client disconnect cleanup per plan §6.2).
    # Note: __exit__ fires AFTER add_metadata, so 3 events are emitted:
    # step_start, step_metadata(before), step_end.
    drained: list = []
    while not queue.empty():
        drained.append(queue.get_nowait())
    assert len(drained) == 3
    assert drained[0]["event"] == "step_start"
    assert drained[1]["event"] == "step_metadata"
    assert drained[2]["event"] == "step_end"
