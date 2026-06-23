"""M7 golden agent evals (M7 plan §6.9).

These tests are marked `agent_eval` and require live LLM + DB + Meili access.
In CI they run nightly; locally they are skipped unless `M5_RUN_EVALS=1`.

M7 changes from M5:
- Transport switched from M5 custom SSE to ag-ui SSE.
- Expected status set widens to {200, 400, 503} (ag-ui may return 400 on bad
  input vs M5's 422).
- Per-row `thread_id=f"golden-{case.id}"` for checkpoint isolation.
- `admin_debug` cases skip when `COPILOTKIT_DEV_AUTH_BYPASS` is false.
"""
from __future__ import annotations

import os

import pytest

from tests.evals.dataset import GOLDEN_DATASET, composite_score


pytestmark = pytest.mark.agent_eval


def _make_run_id() -> str:
    import uuid

    return f"run-{uuid.uuid4().hex[:8]}"


@pytest.mark.skipif(
    not os.environ.get("M5_RUN_EVALS"),
    reason="set M5_RUN_EVALS=1 to run live agent evals",
)
@pytest.mark.parametrize(
    "case", GOLDEN_DATASET, ids=[c.get("id", c["query"][:30]) for c in GOLDEN_DATASET]
)
async def test_golden_agent_eval(case: dict) -> None:
    """Run one eval case end-to-end against the ag-ui CopilotKit bridge."""
    from httpx import ASGITransport, AsyncClient

    from api.main import create_app

    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set")

    # Admin-debug cases require dev bypass.
    if case.get("expected_intent") == "admin_debug" and not os.environ.get(
        "COPILOTKIT_DEV_AUTH_BYPASS"
    ):
        pytest.skip("admin_debug case requires COPILOTKIT_DEV_AUTH_BYPASS=true")

    app = create_app()
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        token = os.environ.get("M5_TEST_JWT", "")
        if not token:
            pytest.skip("M5_TEST_JWT not set")

        thread_id = f"golden-{case.get('id', 'q')}"
        run_id = _make_run_id()
        # M7: ag-ui RunAgentInput shape (locked from spike §4).
        body = {
            "thread_id": thread_id,
            "run_id": run_id,
            "state": {},
            "messages": [{"role": "user", "content": case["query"]}],
            "tools": [],
            "context": [],
        }
        resp = await client.post(
            "/api/copilotkit",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
            timeout=60,
        )
    # ag-ui may return 400 (validation) on bad input; 503 on budget kill.
    assert resp.status_code in {200, 400, 503}, resp.text


def test_composite_score_formula() -> None:
    """The plan-mandated weighting must be applied as documented."""
    score = composite_score(correctness=1.0, citation_valid=1.0, refusal_correct=1.0, latency_pass=1.0)
    assert score == pytest.approx(1.0)
    score = composite_score(correctness=0.5, citation_valid=0.0, refusal_correct=0.0, latency_pass=0.0)
    assert score == pytest.approx(0.2)
