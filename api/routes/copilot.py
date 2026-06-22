"""M5 CopilotKit endpoint — POST /api/copilotkit.

Per vast-painting-sparkle plan §6:
- Streams LangGraph execution via SSE with a 5s heartbeat (suppressed when
  the LLM emits the first token).
- Step events (`step_start` / `step_end` / `step_metadata`) plumbed via
  `asyncio.Queue` injected through `config["configurable"]["emit_fn"]` (per
  plan §5.3).
- Honors `asyncio.CancelledError` on client disconnect (partial state
  committed by the checkpointer).
- Returns 503+Retry-After on daily-budget kill switch.
- Event ordering contract (per plan §6.1):
  1. `state`  → 2. `step_start` → 3. `step_metadata` (interleaved)
  → 4. `step_end` → 5. `message` → 6. `citations` → 7. `error` (any time)
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, AsyncIterator

import jwt as pyjwt
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from agents import config
from agents.budget import (
    current_usage,
    is_killed,
    record_usage,
    seconds_until_midnight_utc,
)
from agents.langgraph.state import AgentState, RunContext
from agents.security import verify_token
from agents.tracing import build_handler

logger = logging.getLogger("api.copilot")

# Queue max size for step events (per plan §6.2: backpressure handling).
_STEP_QUEUE_MAXSIZE = 100

router = APIRouter(prefix="/api", tags=["copilot"])


class CopilotRequest(BaseModel):
    messages: list[dict[str, Any]] = Field(default_factory=list)
    session_id: str | None = None


async def _authenticate(request: Request) -> tuple[str, str | None, bool]:
    """Pull JWT from `Authorization: Bearer ...` or `?token=...` query."""
    auth = request.headers.get("authorization") or ""
    token = ""
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    if not token:
        token = request.query_params.get("token", "")
    if not token:
        raise HTTPException(status_code=401, detail="missing_token")
    try:
        claims = verify_token(token)
    except pyjwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail=f"invalid_token: {exc}") from exc
    return claims.user_id, claims.user_id_hash, claims.is_admin


async def _maybe_kill_switch(conn: Any) -> JSONResponse | None:
    used = await current_usage(conn)
    if is_killed(used):
        return JSONResponse(
            status_code=503,
            content={"error": "daily_budget_exceeded", "used_tokens": used},
            headers={"Retry-After": str(seconds_until_midnight_utc())},
        )
    return None


def _build_input_state(req: CopilotRequest, user_id_hash: str | None, is_admin: bool, session_id: str) -> AgentState:
    from langchain_core.messages import HumanMessage

    messages = []
    for m in req.messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "user":
            messages.append(HumanMessage(content=content))
        else:
            from langchain_core.messages import AIMessage

            messages.append(AIMessage(content=content))
    return AgentState(
        messages=messages,
        session_id=session_id,
        user_id_hash=user_id_hash,
        is_admin=is_admin,
    )


def _sse(event: str, data: Any) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, default=str, ensure_ascii=False)}\n\n".encode("utf-8")


def _sse_heartbeat() -> bytes:
    # SSE comment line keeps the connection alive without disturbing the client UI.
    return b": heartbeat\n\n"


async def _heartbeat_task(event: asyncio.Event, interval: int) -> AsyncIterator[bytes]:
    while not event.is_set():
        await asyncio.sleep(interval)
        if event.is_set():
            return
        yield _sse_heartbeat()


@router.post("/copilotkit", response_model=None)
async def copilotkit(
    body: CopilotRequest,
    request: Request,
) -> StreamingResponse:
    user_id, user_id_hash, is_admin = await _authenticate(request)
    session_id = body.session_id or str(uuid.uuid4())
    trace_id = uuid.uuid4().hex

    db_pool = getattr(request.app.state, "db_pool", None)
    if db_pool is None:
        raise HTTPException(status_code=503, detail="db_unavailable")
    graph = getattr(request.app.state, "agent_graph", None)
    if graph is None:
        raise HTTPException(status_code=503, detail="agent_unavailable")

    # Budget kill switch check (best-effort; budget counter increments below).
    async with db_pool.connection() as conn:
        kill_resp = await _maybe_kill_switch(conn)
        if kill_resp is not None:
            return kill_resp

    handler = build_handler()
    if hasattr(handler, "set_session"):
        handler.set_session(session_id)
    if hasattr(handler, "set_user"):
        handler.set_user(user_id_hash)

    state = _build_input_state(body, user_id_hash, is_admin, session_id)
    run_ctx = RunContext(
        stream_started=asyncio.Event(),
        trace_id=trace_id,
        session_id=session_id,
        user_id_hash=user_id_hash,
        is_admin=is_admin,
    )

    async def event_stream() -> AsyncIterator[bytes]:
        nonlocal state
        try:
            yield _sse("state", {"session_id": session_id, "trace_id": trace_id, "is_admin": is_admin})

            # Step event queue (plan §5.3, §6.1).
            step_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=_STEP_QUEUE_MAXSIZE)

            def _emit_step(payload: dict[str, Any]) -> None:
                # Synchronous wrapper for `StepTracker.emit_fn`; bridge to async queue.
                try:
                    step_queue.put_nowait(payload)
                except asyncio.QueueFull:
                    logger.warning("step event dropped (queue full, maxsize=%d)", _STEP_QUEUE_MAXSIZE)

            async def _runner() -> AgentState:
                async with db_pool.connection() as conn:
                    result = await graph.ainvoke(
                        state,
                        config={
                            "configurable": {
                                "thread_id": session_id,
                                "run_context": run_ctx,
                                "db_conn": conn,
                                "emit_fn": _emit_step,  # plan §5.3: inject emit_fn
                            },
                            "recursion_limit": config.AGENT_RECURSION_LIMIT,
                            "callbacks": [handler],
                        },
                    )
                if isinstance(result, AgentState):
                    return result
                return AgentState(**result)

            runner_task: asyncio.Task[AgentState] = asyncio.create_task(_runner())
            try:
                while not runner_task.done():
                    try:
                        # Wait briefly for either a step event or the runner to finish.
                        item = await asyncio.wait_for(
                            step_queue.get(), timeout=config.AGENT_HEARTBEAT_S
                        )
                        # Drain all currently-queued step events.
                        if item is not None:
                            yield _sse(item["event"], item["data"])
                            while True:
                                try:
                                    next_item = step_queue.get_nowait()
                                except asyncio.QueueEmpty:
                                    break
                                if next_item is not None:
                                    yield _sse(next_item["event"], next_item["data"])
                    except asyncio.TimeoutError:
                        if not run_ctx.stream_started.is_set():
                            yield _sse_heartbeat()
                        continue
                # Drain remaining step events after runner completes.
                while True:
                    try:
                        item = step_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    if item is not None:
                        yield _sse(item["event"], item["data"])
                state = runner_task.result()
            except asyncio.CancelledError:
                runner_task.cancel()
                # Drain and discard pending events (plan §6.2: don't emit after disconnect).
                while True:
                    try:
                        step_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                logger.info("client disconnected, partial state committed for session=%s", session_id)
                raise
            except Exception as exc:
                logger.exception("graph run failed: %s", exc)
                yield _sse("error", {"message": "graph_run_failed", "trace_id": trace_id})
                return
            finally:
                handler.flush()

            final_text = _extract_final_text(state) or (
                f"Xin lỗi, hệ thống đang bận. trace_id: {trace_id}"
            )
            yield _sse("message", {"role": "assistant", "content": final_text})
            yield _sse("citations", {"items": [c.model_dump() for c in state.citations]})

            # Record successful request (1 unit) for the daily budget counter.
            try:
                async with db_pool.connection() as conn:
                    await record_usage(conn, tokens=0)
            except Exception as exc:
                logger.warning("budget counter increment failed: %s", exc)
        except asyncio.CancelledError:
            logger.info("client disconnected mid-stream, session=%s", session_id)
            raise
        except Exception as exc:
            logger.exception("stream error: %s", exc)
            yield _sse("error", {"message": str(exc), "trace_id": trace_id})

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "X-Trace-Id": trace_id,
    }
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers=headers,
    )


def _extract_final_text(state: AgentState) -> str | None:
    if getattr(state, "final_response", None):
        return state.final_response
    from langchain_core.messages import AIMessage

    for m in reversed(state.messages):
        if isinstance(m, AIMessage) and isinstance(m.content, str) and m.content.strip():
            return m.content
    return None
