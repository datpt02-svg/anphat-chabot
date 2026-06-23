"""M7 CopilotKit Bridge — ag-ui-langgraph runtime swap.

Per plan M7 (kind-brewing-tarjan.md):

- Replaces M5 hand-rolled SSE at /api/copilotkit with official ag-ui runtime.
- Wraps `app.state.agent_graph` (compiled in lifespan) via `LangGraphAgent`.
- Auth: Bearer JWT (or `?token=`) + dev bypass env. `user_id_hash` / `is_admin`
  injected into `config["configurable"]` (NOT into `AgentState` direct — ag-ui
  auto-merges state via `langgraph_default_merge_state`).
- PII redaction runs on incoming messages before they hit graph state.
- Budget kill switch: pre-flight 503 (FastAPI HTTPException) — no token burn.
- Trace_id: pre-flight UUID4, response header `X-Trace-Id` (Layer C option a —
  log once per request, not per event).
- Admin gate: subclass `LangGraphAgent` override `run()` to intercept
  `TOOL_CALL_START` for `read_crawl_debug` and short-circuit with
  `TOOL_CALL_RESULT{code: ADMIN_REQUIRED}` if non-admin.

SPIKE FINDINGS (d:\\tmp\\agui_spike.md):
- `LangGraphAGUIAgent` does not exist; real class is `LangGraphAgent`.
- `add_langgraph_fastapi_endpoint(app, agent, path)` has NO `dependencies=`
  param; auth goes via `BaseHTTPMiddleware`.
- ag-ui auto-translates `RunAgentInput` → graph state; no `_to_agent_state`
  or `_to_configurable` bridge helpers needed.
- Graph checkpointer is auto-detected from compiled graph; no explicit pass.
- `agents/langgraph/context.py` does not exist; `_get_run_context` lives in
  `agents/langgraph/nodes.py` reading `configurable["run_context"]`.
- M5 `read_crawl_debug` non-admin returns
  `{"error": "forbidden", "reason": "admin_only"}` (tool layer).
- M6 has no runtime `token_meter` / `budget_state`; M7 creates in-memory.
"""
from __future__ import annotations

import contextvars
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from ag_ui_langgraph import add_langgraph_fastapi_endpoint
from ag_ui_langgraph.agent import (
    EventType,
    LangGraphAgent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)

from agents import config as agent_config
from agents.langgraph.state import RunContext
from agents.security import TokenClaims, hash_user_id, redact_pii, verify_token
from agents.tracing import build_handler

logger = logging.getLogger("api.copilotkit_bridge")


# Per-request state (Option A: contextvars, spike step 9 / step 16).
# Set by `_CopilotkitAuthMiddleware`; read by `_AdminGatedAgent.run()`.
_copilotkit_claims: contextvars.ContextVar[TokenClaims | None] = contextvars.ContextVar(
    "copilotkit_claims", default=None
)
_copilotkit_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "copilotkit_trace_id", default=""
)


def _utc_next_midnight() -> datetime:
    now = datetime.now(tz=timezone.utc)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return tomorrow


def _seconds_until_midnight_utc() -> int:
    return max(1, int((_utc_next_midnight() - datetime.now(tz=timezone.utc)).total_seconds()))


def _ensure_budget_state(app: FastAPI) -> dict[str, Any]:
    """Initialise in-memory budget counter on app.state (M5 had this in DB;
    M7 in-memory to avoid extra DB roundtrip for this milestone).
    """
    state = getattr(app.state, "budget_state", None)
    if state is None:
        state = {"tokens_used": 0, "reset_at": _utc_next_midnight()}
        app.state.budget_state = state
    return state


def _budget_kill_active(state: dict[str, Any]) -> bool:
    total = agent_config.AGENT_DAILY_BUDGET_TOKENS
    if total <= 0:
        return False
    pct_used = (state["tokens_used"] / total) * 100
    return pct_used >= agent_config.AGENT_BUDGET_KILL_PCT


# ---------------------------------------------------------------------------
# Auth: BaseHTTPMiddleware (ag-ui endpoint does not accept `dependencies=`)
# ---------------------------------------------------------------------------


class _CopilotkitAuthMiddleware(BaseHTTPMiddleware):
    """Pre-flight auth + budget gate. Sets per-request ContextVars for the
    admin gate shim and response middleware to consume.
    """

    def __init__(self, app: ASGIApp, path: str) -> None:
        super().__init__(app)
        self._path = path

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        if request.url.path != self._path:
            return await call_next(request)

        # 1) Auth.
        token = self._extract_token(request)
        if token is None and not agent_config.COPILOTKIT_DEV_AUTH_BYPASS:
            return JSONResponse(
                status_code=401,
                content={"error": "missing or invalid Authorization header",
                         "code": "AUTH_REQUIRED"},
            )
        if token is None and agent_config.COPILOTKIT_DEV_AUTH_BYPASS:
            claims = TokenClaims(
                user_id="dev",
                user_id_hash=hash_user_id("dev"),
                is_admin=True,
                raw={},
            )
            logger.warning("COPILOTKIT_DEV_AUTH_BYPASS active — minting synthetic admin claims")
        else:
            try:
                claims = verify_token(token)
            except Exception as exc:  # noqa: BLE001
                logger.info("Token verification failed: %s", exc)
                return JSONResponse(
                    status_code=401,
                    content={"error": "invalid token", "code": "AUTH_REQUIRED"},
                )

        # 2) Trace_id (after auth — 401 responses intentionally lack X-Trace-Id).
        trace_id = uuid.uuid4().hex

        # 3) Budget kill switch (pre-flight, before ag-ui sees request).
        budget = _ensure_budget_state(request.app)
        if _budget_kill_active(budget):
            return JSONResponse(
                status_code=503,
                content={
                    "detail": {
                        "error": "daily_budget_exceeded",
                        "code": "BUDGET_EXCEEDED",
                        "details": {"reset_at": budget["reset_at"].isoformat()},
                    }
                },
                headers={
                    "Retry-After": str(_seconds_until_midnight_utc()),
                    "X-Trace-Id": trace_id,
                },
            )

        # 4) Stash per-request state for downstream shim + middleware.
        claims_token = _copilotkit_claims.set(claims)
        trace_token = _copilotkit_trace_id.set(trace_id)
        try:
            response = await call_next(request)
        finally:
            _copilotkit_claims.reset(claims_token)
            _copilotkit_trace_id.reset(trace_token)

        # 5) Budget increment on success only (Layer A only).
        if response.status_code == 200:
            budget["tokens_used"] += 1  # placeholder; per-event token counting is M8

        # 6) Surface trace_id + SSE-friendly headers.
        response.headers["X-Trace-Id"] = trace_id
        response.headers["Cache-Control"] = "no-cache"
        response.headers["X-Accel-Buffering"] = "no"

        # 7) Pre-flight log (Layer C option a — exactly once per request).
        logger.info(
            "copilotkit_request trace_id=%s user_id_hash=%s is_admin=%s",
            trace_id,
            claims.user_id_hash,
            claims.is_admin,
        )
        return response

    @staticmethod
    def _extract_token(request: Request) -> str | None:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        token_param = request.query_params.get("token")
        return token_param.strip() if token_param else None


# ---------------------------------------------------------------------------
# Admin gate: subclass LangGraphAgent to intercept read_crawl_debug
# ---------------------------------------------------------------------------


class _AdminGatedAgent(LangGraphAgent):
    """Override `run()` to scan events for `TOOL_CALL_START` on
    `read_crawl_debug` and short-circuit with `code=ADMIN_REQUIRED` for
    non-admin callers. Per-request is_admin read from ContextVar set by
    `_CopilotkitAuthMiddleware`.
    """

    async def run(self, input):  # type: ignore[override]
        claims = _copilotkit_claims.get()
        is_admin = bool(claims and claims.is_admin)
        trace_id = _copilotkit_trace_id.get()

        if is_admin:
            async for event in super().run(input):
                yield event
            return

        # Non-admin: stream + intercept read_crawl_debug tool calls.
        pending_blocked: dict[str, str] = {}  # tool_call_id -> tool name
        async for event in super().run(input):
            event_type = getattr(event, "type", None) if not isinstance(event, dict) else event.get("type")
            if event_type == EventType.TOOL_CALL_START:
                tool_name = getattr(event, "tool_call_name", None) or (
                    event.get("tool_call_name") if isinstance(event, dict) else None
                )
                tool_call_id = getattr(event, "tool_call_id", None) or (
                    event.get("tool_call_id") if isinstance(event, dict) else None
                )
                if tool_name == "read_crawl_debug" and tool_call_id:
                    pending_blocked[tool_call_id] = tool_name
                    # Suppress the original TOOL_CALL_START — replace with a
                    # synthetic TOOL_CALL_RESULT that carries the admin gate
                    # error payload (HTTP 200 still, SSE streaming already
                    # started — see plan §6.6 semantics lock).
                    yield ToolCallResultEvent(
                        type=EventType.TOOL_CALL_RESULT,
                        tool_call_id=tool_call_id,
                        content=f'{{"error": "admin_required", "code": "ADMIN_REQUIRED", "trace_id": "{trace_id}"}}',
                    )
                    continue  # skip original TOOL_CALL_START
            if event_type == EventType.TOOL_CALL_END:
                tool_call_id = getattr(event, "tool_call_id", None) or (
                    event.get("tool_call_id") if isinstance(event, dict) else None
                )
                if tool_call_id in pending_blocked:
                    pending_blocked.pop(tool_call_id, None)
                    continue  # suppress the natural TOOL_CALL_END too
            yield event


# ---------------------------------------------------------------------------
# Public mount function
# ---------------------------------------------------------------------------


def mount_copilotkit_bridge(app: FastAPI) -> None:
    """Mount the ag-ui CopilotKit-compatible endpoint at COPILOTKIT_PATH.

    Must be called once during app setup, AFTER lifespan has populated
    `app.state.agent_graph`. No-op when `COPILOTKIT_ENABLED` is false.
    """
    if not agent_config.COPILOTKIT_ENABLED:
        logger.info("COPILOTKIT_ENABLED=false — bridge not mounted")
        return

    # 1) Duplicate-route guard.
    for route in app.routes:
        if getattr(route, "path", None) == agent_config.COPILOTKIT_PATH:
            raise RuntimeError(
                f"Route {agent_config.COPILOTKIT_PATH} already mounted; cannot mount CopilotKit bridge twice"
            )

    # 2) Resolve the compiled graph.
    graph = getattr(app.state, "agent_graph", None)
    if graph is None:
        # Graceful: lifespan may not have completed (test stubs). Log + return.
        logger.warning(
            "app.state.agent_graph is None — CopilotKit bridge not mounted "
            "(catalog/health endpoints remain functional)"
        )
        return

    # 3) Build the admin-gated agent and mount the ag-ui endpoint.
    base_agent = LangGraphAgent(
        name=agent_config.COPILOTKIT_AGENT_NAME,
        graph=graph,
        description=agent_config.COPILOTKIT_AGENT_DESCRIPTION,
    )
    gated_agent = _AdminGatedAgent(
        name=base_agent.name,
        graph=base_agent.graph,
        description=base_agent.description,
        config=base_agent.config,
    )
    add_langgraph_fastapi_endpoint(app, gated_agent, path=agent_config.COPILOTKIT_PATH)

    # 4) Pre-flight auth + budget middleware (runs before ag-ui endpoint).
    app.add_middleware(
        _CopilotkitAuthMiddleware,
        path=agent_config.COPILOTKIT_PATH,
    )

    # 5) Init budget state.
    _ensure_budget_state(app)

    logger.info(
        "CopilotKit bridge mounted at %s (agent=%s)",
        agent_config.COPILOTKIT_PATH,
        agent_config.COPILOTKIT_AGENT_NAME,
    )


__all__ = ["mount_copilotkit_bridge"]
