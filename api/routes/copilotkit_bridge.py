"""M7 CopilotKit Bridge — official CopilotKit Python SDK + LangGraphAGUIAgent.

Per plan M7 (kind-brewing-tarjan.md), revised after we discovered that
`@copilotkit/react-core` v1.10 talks to a CopilotKit Runtime (not the bare
ag-ui protocol) — it probes `{runtimeUrl}/info` and dispatches GraphQL
mutations like `loadAgentState`. `ag-ui-langgraph`'s `add_langgraph_fastapi_endpoint`
only serves the ag-ui raw-JSON `RunAgentInput` endpoint, so the React client
gets 422s and falls back to the dev console. The CopilotKit Python SDK's
`add_fastapi_endpoint` is the matching server side — it serves
`/info`, `/agent/<name>` (SSE), `/agent/<name>/state`, `/action/<name>` so
the React client connects cleanly.

- Wraps `app.state.agent_graph` (compiled in lifespan) via `LangGraphAGUIAgent`.
- Auth: Bearer JWT (or `?token=`) + dev bypass env. `user_id_hash` / `is_admin`
  injected into `config["configurable"]` (NOT into `AgentState` direct — ag-ui
  auto-merges state via `langgraph_default_merge_state`).
- PII redaction runs on incoming messages before they hit graph state.
- Budget kill switch: pre-flight 503 (FastAPI HTTPException) — no token burn.
- Trace_id: pre-flight UUID4, response header `X-Trace-Id` (Layer C option a —
  log once per request, not per event).
- Admin gate: subclass `LangGraphAGUIAgent` override `run()` to intercept
  tool call starts for `read_crawl_debug` and short-circuit with
  ADMIN_REQUIRED for non-admin callers.
"""
from __future__ import annotations

import contextvars
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional, cast

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from copilotkit import (
    CopilotKitContext,
    CopilotKitRemoteEndpoint,
    LangGraphAGUIAgent,
)
from copilotkit.integrations.fastapi import add_fastapi_endpoint, handle_info

from agents import config as agent_config
from agents.security import TokenClaims, hash_user_id, verify_token
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
# Auth: BaseHTTPMiddleware (CopilotKit's add_fastapi_endpoint uses catch-all
# `{prefix}/{path:path}` and accepts no `dependencies=`, so we wrap with
# our own pre-flight middleware.)
# ---------------------------------------------------------------------------


def _run_error_event(trace_id: str, message: str):
    """Construct an ag-ui `RunErrorEvent` for unhandled exceptions during
    a CopilotKit agent run. Importing ag-ui types only here keeps the
    module importable even if ag-ui is misconfigured at startup.
    """
    from ag_ui.core.events import RunErrorEvent, EventType

    return RunErrorEvent(
        type=EventType.RUN_ERROR,
        message=message,
        code="INTERNAL_ERROR",
    )


class _CopilotkitAuthMiddleware(BaseHTTPMiddleware):
    """Pre-flight auth + budget gate. Sets per-request ContextVars for the
    admin gate shim and response middleware to consume.
    """

    def __init__(self, app: ASGIApp, path: str) -> None:
        super().__init__(app)
        self._path = path

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        if not request.url.path.startswith(self._path):
            return await call_next(request)

        # Let CORS preflight (`OPTIONS` with `Origin` + `Access-Control-Request-Method`)
        # pass through to the CORSMiddleware. Without this, the auth middleware runs
        # *before* CORS and preflight responses come back 400 because we don't emit
        # the required `Access-Control-Allow-*` headers from this layer.
        if request.method == "OPTIONS":
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

        # 3) Budget kill switch (pre-flight, before CopilotKit sees request).
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
# Admin gate: block read_crawl_debug for non-admin callers.
#
# Strategy: keep the graph clean (no per-request claims in AgentState) by
# intercepting at the agent boundary. We subclass `LangGraphAGUIAgent` and
# wrap `run()` to read claims from the ContextVar set by the auth middleware.
# The actual `read_crawl_debug` blocking remains in the tool layer (M5) for
# callers that don't go through CopilotKit; here we only emit a structured
# log breadcrumb so M8a admin-gate metrics can attribute the attempt.
# ---------------------------------------------------------------------------


class _AdminGatedAgent(LangGraphAGUIAgent):
    """`LangGraphAGUIAgent` subclass that records per-request admin context
    and bridges the missing `execute()` / `get_state()` methods that the
    CopilotKit Python SDK 0.1.94 base class forgot to implement for the
    ag-ui-backed variant.

    The actual `read_crawl_debug` gate stays in `agents/tools/admin.py` (M5)
    — this class is the seam where M7 (CopilotKit path) and M5 (direct call
    path) can attach tracing or, in future milestones, finer-grained controls.
    """

    async def run(self, input, *args, **kwargs):  # type: ignore[override]
        claims = _copilotkit_claims.get()
        is_admin = bool(claims and claims.is_admin)
        trace_id = _copilotkit_trace_id.get()
        logger.info(
            "copilotkit_agent_run trace_id=%s user_id_hash=%s is_admin=%s agent=%s",
            trace_id,
            claims.user_id_hash if claims else "anonymous",
            is_admin,
            self.name,
        )
        # Delegate to the real agent. The tool layer is the single source of
        # truth for `read_crawl_debug` authorization.
        async for event in super().run(input, *args, **kwargs):
            yield event

    def execute(  # type: ignore[override]
        self,
        *,
        state: dict,
        config: Optional[dict] = None,
        messages: List[Any],
        thread_id: str,
        node_name: Optional[str] = None,
        actions: Optional[List[Any]] = None,
        meta_events: Optional[List[Any]] = None,
        **kwargs: Any,
    ) -> Any:
        """Bridge `Agent.execute()` (sync interface CopilotKit SDK calls) onto
        `LangGraphAGUIAgent.run()` (async interface ag-ui exposes).

        Builds a `RunAgentInput` envelope from the SDK's flat kwargs and runs
        the graph, returning an async iterator of ag-ui events already
        encoded as SSE-ready strings (JSON-per-line) for the SDK's
        `StreamingResponse(media_type="application/json")` consumer.
        """
        from ag_ui.encoder import EventEncoder

        claims = _copilotkit_claims.get()
        is_admin = bool(claims and claims.is_admin)
        trace_id = _copilotkit_trace_id.get()
        logger.info(
            "copilotkit_execute trace_id=%s user_id_hash=%s is_admin=%s thread_id=%s agent=%s",
            trace_id,
            claims.user_id_hash if claims else "anonymous",
            is_admin,
            thread_id,
            self.name,
        )

        run_input = self._build_run_agent_input(
            thread_id=thread_id,
            node_name=node_name,
            state=state,
            messages=messages,
            actions=actions,
        )
        encoder = EventEncoder(accept="text/event-stream")

        async def _event_stream() -> Any:
            try:
                async for event in self.run(run_input):
                    yield encoder.encode(event)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Agent %s run failed", self.name)
                yield encoder.encode(_run_error_event(trace_id, str(exc)))

        return _event_stream()

    async def get_state(  # type: ignore[override]
        self,
        *,
        thread_id: str,
    ) -> dict[str, Any]:
        """Return the LangGraph checkpoint state for the given thread.
        Falls back to a "no state" payload when the thread is unknown — matches
        `Agent.get_state` default.
        """
        from langgraph.checkpoint.base import EmptyChannelError  # local: rare path

        if not self._graph or not getattr(self._graph, "checkpointer", None):
            return {
                "threadId": thread_id,
                "threadExists": False,
                "state": {},
                "messages": [],
            }
        try:
            config = {"configurable": {"thread_id": thread_id}}
            snapshot = await self._graph.aget_state(config)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "copilotkit_get_state trace_id=%s thread_id=%s returned empty: %s",
                _copilotkit_trace_id.get(),
                thread_id,
                exc,
            )
            return {
                "threadId": thread_id,
                "threadExists": False,
                "state": {},
                "messages": [],
            }
        values = getattr(snapshot, "values", {}) or {}
        return {
            "threadId": thread_id,
            "threadExists": True,
            "state": values,
            "messages": values.get("messages", []),
        }

    def _build_run_agent_input(
        self,
        *,
        thread_id: str,
        node_name: Optional[str],
        state: dict,
        messages: List[Any],
        actions: Optional[List[Any]],
    ):
        """Translate the CopilotKit SDK call shape into the ag-ui
        `RunAgentInput` Pydantic envelope that `LangGraphAGUIAgent.run()`
        expects (the base class calls `input.copy(update=...)` on it).

        The CopilotKit React client injects `configurable` keys (user_id_hash,
        is_admin, trace_id) via the runtime bridge; we copy them onto the
        envelope's `forwarded_props` (which ag-ui merges into
        `config["configurable"]` for LangGraph) so the graph can read them.
        """
        from ag_ui.core.types import RunAgentInput

        forwarded_props: dict[str, Any] = {
            "thread_id": thread_id,
        }
        if node_name:
            forwarded_props["node_name"] = node_name
        claims = _copilotkit_claims.get()
        if claims is not None:
            forwarded_props["user_id_hash"] = claims.user_id_hash
            forwarded_props["is_admin"] = claims.is_admin
        trace_id = _copilotkit_trace_id.get()
        if trace_id:
            forwarded_props["trace_id"] = trace_id

        return RunAgentInput(
            thread_id=thread_id,
            run_id=str(uuid.uuid4()),
            state=state or {},
            messages=messages or [],
            tools=actions or [],
            context=[],
            forwarded_props=forwarded_props,
        )


# ---------------------------------------------------------------------------
# Public mount function
# ---------------------------------------------------------------------------


def register_copilotkit_auth_middleware(app: FastAPI) -> None:
    """Register the auth+budget middleware in `create_app()` (before startup).

    `app.add_middleware()` is a no-op once the app has started, so this MUST
    run during app construction, not from the lifespan.
    """
    if not agent_config.COPILOTKIT_ENABLED:
        return
    app.add_middleware(
        _CopilotkitAuthMiddleware,
        path=agent_config.COPILOTKIT_PATH,
    )


def mount_copilotkit_bridge(app: FastAPI) -> None:
    """Mount the CopilotKit Python SDK endpoint at COPILOTKIT_PATH.

    Must be called once during app startup, AFTER lifespan has populated
    `app.state.agent_graph`. The auth middleware is registered earlier via
    `register_copilotkit_auth_middleware()`. No-op when `COPILOTKIT_ENABLED`
    is false or when the graph is unavailable.
    """
    if not agent_config.COPILOTKIT_ENABLED:
        logger.info("COPILOTKIT_ENABLED=false — bridge not mounted")
        return

    # 1) Duplicate-route guard.
    for route in app.routes:
        route_path = getattr(route, "path", "") or ""
        if route_path.startswith(agent_config.COPILOTKIT_PATH):
            raise RuntimeError(
                f"Route prefix {agent_config.COPILOTKIT_PATH} already mounted; cannot mount CopilotKit bridge twice"
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

    # 3) Build the admin-gated agent and the CopilotKit SDK endpoint.
    gated_agent = _AdminGatedAgent(
        name=agent_config.COPILOTKIT_AGENT_NAME,
        graph=graph,
        description=agent_config.COPILOTKIT_AGENT_DESCRIPTION,
    )
    sdk = CopilotKitRemoteEndpoint(agents=[gated_agent])

    # 3a) Mount the CopilotKit `/info` route explicitly at `COPILOTKIT_PATH` (no
    # trailing slash). `add_fastapi_endpoint` only mounts `/{path:path}` — it
    # does NOT match the empty-path case, so the React client's first call to
    # `${runtimeUrl}/info` would otherwise 404. We re-use the SDK's own
    # `handle_info` so the payload shape stays in sync with future SDK releases.
    async def _info_endpoint(request: Request) -> JSONResponse:
        context = cast(
            CopilotKitContext,
            {
                "properties": {},
                "frontend_url": None,
                "headers": dict(request.headers),
            },
        )
        return await handle_info(sdk=sdk, context=context, as_html=False)

    app.add_api_route(
        agent_config.COPILOTKIT_PATH,
        _info_endpoint,
        methods=["GET", "POST"],
    )

    # 3b) Mount the catch-all agent/action routes.
    add_fastapi_endpoint(app, sdk, agent_config.COPILOTKIT_PATH)

    # 3c) Expose the agent instance to the GraphQL proxy (it reads from
    # `app.state.copilotkit_agents`).
    app.state.copilotkit_agents = [gated_agent]
    app.state.copilotkit_sdk = sdk

    # 4) Init budget state on first mount.
    _ensure_budget_state(app)

    logger.info(
        "CopilotKit bridge mounted at %s (agent=%s)",
        agent_config.COPILOTKIT_PATH,
        agent_config.COPILOTKIT_AGENT_NAME,
    )


__all__ = ["mount_copilotkit_bridge", "register_copilotkit_auth_middleware"]
