"""M4 test bootstrap: env vars must be present before `api.*` imports."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("MEILI_HOST", "http://localhost:7700")
os.environ.setdefault("MEILI_MASTER_KEY", "test_master_key")
os.environ.setdefault("MEILI_PRODUCTS_INDEX", "products_test")
os.environ.setdefault("MEILI_TIMEOUT_SECONDS", "5")
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:5173")
os.environ.setdefault("SEARCH_FALLBACK_ENABLED", "true")
os.environ.setdefault("SEARCH_MAX_LIMIT", "100")


class _FakeCursor:
    def __init__(
        self,
        fetchone_value: Any = None,
        fetchall_value: list | None = None,
    ) -> None:
        self._fetchone = fetchone_value
        self._fetchall = fetchall_value or []

    async def __aenter__(self) -> "_FakeCursor":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def execute(self, sql: str, params: tuple | None = None) -> None:
        return None

    async def fetchone(self) -> Any:
        return self._fetchone

    async def fetchall(self) -> list:
        return self._fetchall


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor

    def cursor(self) -> _FakeCursor:
        return self._cursor


class _FakePool:
    def __init__(self, conn: _FakeConnection) -> None:
        self._conn = conn

    def connection(self) -> "_FakePoolContext":
        return _FakePoolContext(self._conn)


class _FakePoolContext:
    def __init__(self, conn: _FakeConnection) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConnection:
        return self._conn

    async def __aexit__(self, *exc: Any) -> None:
        return None


@pytest.fixture
def make_fake_conn():
    def _make(fetchone_value: Any = None, fetchall_value: list | None = None) -> _FakeConnection:
        return _FakeConnection(_FakeCursor(fetchone_value=fetchone_value, fetchall_value=fetchall_value))
    return _make


@pytest.fixture
def app():
    from api.main import create_app
    application = create_app()
    application.state.meili_host = "http://meili.test"
    application.state.meili_index = "products_test"
    application.state.db_pool = _FakePool(_FakeConnection(_FakeCursor()))
    application.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(_passthrough))
    return application


async def _passthrough(request: httpx.Request) -> httpx.Response:
    raise RuntimeError(f"Unhandled mock request: {request.method} {request.url}")


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac
    await app.state.http_client.aclose()


@pytest.fixture
def mounted_app(stub_graph):
    """FastAPI app with the CopilotKit bridge mounted (uses real stub graph).

    The default `app` fixture leaves `app.state.agent_graph = None`, so the
    bridge's no-op path keeps `/api/copilotkit` unregistered. This fixture
    sets the graph and calls `mount_copilotkit_bridge()` so the route IS
    registered — required for tests that hit auth/budget/trace_id without
    going through `app_with_stub`.
    """
    from api.main import create_app
    from api.routes.copilotkit_bridge import mount_copilotkit_bridge

    app = create_app()
    app.state.agent_graph = stub_graph()
    mount_copilotkit_bridge(app)
    return app


@pytest.fixture
async def mounted_client(mounted_app):
    """AsyncClient bound to `mounted_app`."""
    transport = httpx.ASGITransport(app=mounted_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac, mounted_app
    # http_client may not be initialized (mounted_app has no lifespan).
    hc = getattr(mounted_app.state, "http_client", None)
    if hc is not None:
        await hc.aclose()


@pytest.fixture
def override_db(app):
    from api.dependencies import get_db_conn

    def _set(conn: _FakeConnection) -> None:
        async def _gen() -> AsyncIterator[_FakeConnection]:
            yield conn
        app.dependency_overrides[get_db_conn] = _gen

    yield _set
    app.dependency_overrides.pop(get_db_conn, None)


@pytest.fixture
def override_http(app):
    from api.dependencies import get_http_client

    def _set(handler) -> None:
        app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        def _dep():
            return app.state.http_client

        app.dependency_overrides[get_http_client] = _dep

    yield _set
    app.dependency_overrides.pop(get_http_client, None)


class _StubGraph:
    """Real CompiledStateGraph that yields a canned AIMessage. Used by M7
    bridge tests (was a plain stub in test_copilot.py before M7 deletion;
    ag-ui `LangGraphAgent` requires a graph with `.nodes` attribute).

    Tests can override the canned response by subclassing or by providing a
    `response_text` arg.
    """

    def __init__(self, response_text: str = "stub reply") -> None:
        self._response_text = response_text
        # Build a real CompiledStateGraph with one node.
        from langchain_core.messages import AIMessage
        from langgraph.graph import END, START, MessagesState, StateGraph

        def _call_model(state):
            return {"messages": [AIMessage(content=self._response_text)]}

        builder = StateGraph(MessagesState)
        builder.add_node("agent", _call_model)
        builder.add_edge(START, "agent")
        builder.add_edge("agent", END)
        self._graph = builder.compile()

    def __getattr__(self, name: str) -> Any:
        # Delegate graph-level access to the real CompiledStateGraph.
        return getattr(self._graph, name)


@pytest.fixture
def stub_graph():
    """Factory for `_StubGraph` — test instantiates with custom response text."""
    def _make(response_text: str = "stub reply") -> _StubGraph:
        return _StubGraph(response_text=response_text)
    return _make
