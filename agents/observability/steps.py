"""M5 Step Streaming Observability (vast-painting-sparkle plan §4).

Defines:
- `StepType` enum (12 step kinds spanning M5a, M5b, M6+)
- SSE event models (`StepStartEvent`, `StepEndEvent`, `StepMetadataEvent`)
- Icon / label lookup tables (M8 frontend contract)
- `StepTracker` context manager (start → end with status + duration)
- `STEP_DISPLAY_ENABLED` module-level flag (re-exported from `agents.config`)
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from enum import Enum
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from agents import config as _config

logger = logging.getLogger("agents.observability.steps")

# Re-export the config flag (plan §1: read once at startup, hardcode into module).
STEP_DISPLAY_ENABLED: bool = _config.STEP_DISPLAY_ENABLED


# ---------------------------------------------------------------------------
# StepType enum (plan §4.1)
# ---------------------------------------------------------------------------
class StepType(str, Enum):
    # M5a
    CLASSIFY = "classify"
    RETRIEVE_CATALOG = "retrieve_catalog"
    RETRIEVE_CHUNKS = "retrieve_chunks"
    TOOL_CALL = "tool_call"
    REASON = "reason"
    VERIFY = "verify"
    RESPOND = "respond"
    # M5b
    COMPATIBILITY_CHECK = "compatibility_check"
    GRAPH_EVALUATE = "graph_evaluate"
    # M6+
    RAG = "rag"
    DB_QUERY = "db_query"
    AGENT_SUBTASK = "agent_subtask"


# ---------------------------------------------------------------------------
# Icon + label lookup tables (plan §4.3)
# ---------------------------------------------------------------------------
STEP_ICONS: dict[StepType, str] = {
    StepType.CLASSIFY: "🔍",
    StepType.RETRIEVE_CATALOG: "📚",
    StepType.RETRIEVE_CHUNKS: "📄",
    StepType.TOOL_CALL: "🔧",
    StepType.REASON: "🧠",
    StepType.VERIFY: "✅",
    StepType.RESPOND: "✍️",
    StepType.COMPATIBILITY_CHECK: "🔗",
    StepType.GRAPH_EVALUATE: "🕸️",
    StepType.RAG: "🔎",
    StepType.DB_QUERY: "🗄️",
    StepType.AGENT_SUBTASK: "🤖",
}

STEP_LABELS: dict[StepType, str] = {
    StepType.CLASSIFY: "Đang phân tích câu hỏi...",
    StepType.RETRIEVE_CATALOG: "Đang tìm trong catalog...",
    StepType.RETRIEVE_CHUNKS: "Đang tìm trong tài liệu...",
    StepType.TOOL_CALL: "Đang thực thi tool...",
    StepType.REASON: "Đang suy nghĩ...",
    StepType.VERIFY: "Đang kiểm tra...",
    StepType.RESPOND: "Đang viết câu trả lời...",
    StepType.COMPATIBILITY_CHECK: "Đang kiểm tra tương thích...",
    StepType.GRAPH_EVALUATE: "Đang phân tích đồ thị sản phẩm...",
    StepType.RAG: "Đang truy xuất tài liệu liên quan...",
    StepType.DB_QUERY: "Đang truy vấn database...",
    StepType.AGENT_SUBTASK: "Đang chạy sub-agent...",
}

# Tool name → label mapping for `tool_call` step (plan §4.3.1)
TOOL_LABELS: dict[str, str] = {
    "search_catalog": "Đang tìm sản phẩm...",
    "get_product": "Đang tra cứu sản phẩm...",
    "compare_products": "Đang so sánh...",
    "explain_specs": "Đang giải thích thông số...",
    "read_crawl_debug": "Đang truy xuất dữ liệu nội bộ...",
}


def label_for(step_type: StepType, metadata: dict[str, Any] | None = None) -> str:
    """Resolve a human-readable label for a step.

    For `tool_call`, prefer `TOOL_LABELS[metadata.tool_name]` if present.
    """
    if step_type == StepType.TOOL_CALL:
        tool_name = (metadata or {}).get("tool_name")
        if tool_name and tool_name in TOOL_LABELS:
            return TOOL_LABELS[tool_name]
    if step_type == StepType.AGENT_SUBTASK:
        subagent = (metadata or {}).get("subagent_name")
        if subagent:
            return f"Đang chạy sub-agent: {subagent}..."
    return STEP_LABELS[step_type]


# ---------------------------------------------------------------------------
# SSE event models (plan §4.2)
# ---------------------------------------------------------------------------
class StepStartEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str
    step_type: StepType
    label: str
    parent_step_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class StepEndEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str
    status: Literal["completed", "failed", "cancelled"]
    duration_ms: int
    output_summary: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class StepMetadataEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str
    key: str
    value: Any


# ---------------------------------------------------------------------------
# StepTracker context manager (plan §4.4)
# ---------------------------------------------------------------------------
EmitFn = Callable[[dict[str, Any]], None]


def _no_op_emit(_payload: dict[str, Any]) -> None:
    return None


class StepTracker:
    """Context manager that emits `StepStartEvent` / `StepEndEvent` / `StepMetadataEvent`.

    Lifecycle:
        with StepTracker(StepType.REASON, emit_fn=queue.put_nowait) as step:
            ...
            step.add_metadata("tokens", 123)

    Behavior:
    - `__enter__` records `start_time` and emits `StepStartEvent` (when `STEP_DISPLAY_ENABLED`).
    - `__exit__` computes `duration_ms` and emits `StepEndEvent` with status:
      * `completed` if no exception
      * `cancelled` if `asyncio.CancelledError` (Python 3.8+: subclass of `BaseException`)
      * `failed` otherwise
    - `add_metadata(key, value)` is only valid between `__enter__` and `__exit__`:
      * Before `__enter__` → `RuntimeError`
      * After `__exit__` → log warning + ignore
    - Backpressure: catches `queue.Full` from the emit fn, logs warning, drops event.
    - When `STEP_DISPLAY_ENABLED=False`: skip emit, still track duration + log nội bộ.
    """

    def __init__(
        self,
        step_type: StepType,
        metadata: dict[str, Any] | None = None,
        parent_step_id: str | None = None,
        emit_fn: EmitFn | None = None,
    ) -> None:
        self.step_type = step_type
        self.metadata: dict[str, Any] = dict(metadata or {})
        self.parent_step_id = parent_step_id
        self._emit: EmitFn = emit_fn or _no_op_emit
        self.step_id: str = uuid.uuid4().hex
        self._start: float | None = None
        self._entered: bool = False
        self._exited: bool = False
        self.label: str = label_for(step_type, self.metadata)

    def __enter__(self) -> "StepTracker":
        if self._entered:
            raise RuntimeError("StepTracker already entered")
        self._entered = True
        self._start = time.perf_counter()
        self._safe_emit(
            {
                "event": "step_start",
                "data": StepStartEvent(
                    step_id=self.step_id,
                    step_type=self.step_type,
                    label=self.label,
                    parent_step_id=self.parent_step_id,
                    metadata=self.metadata,
                ).model_dump(),
            }
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> Literal[False]:
        if not self._entered:
            raise RuntimeError("StepTracker.__exit__ called without __enter__")
        if self._exited:
            return False
        self._exited = True
        duration_ms = int((time.perf_counter() - (self._start or time.perf_counter())) * 1000)

        if exc_type is None:
            status: Literal["completed", "failed", "cancelled"] = "completed"
        elif exc_type is asyncio.CancelledError:
            status = "cancelled"
        else:
            status = "failed"
            # Don't suppress the exception; just record it.
            self.metadata.setdefault("error", str(exc))

        self._safe_emit(
            {
                "event": "step_end",
                "data": StepEndEvent(
                    step_id=self.step_id,
                    status=status,
                    duration_ms=duration_ms,
                    metadata=self.metadata,
                ).model_dump(),
            }
        )
        return False

    def add_metadata(self, key: str, value: Any) -> None:
        if not self._entered:
            raise RuntimeError("StepTracker.add_metadata called outside `with` block")
        if self._exited:
            logger.warning("StepTracker.add_metadata called after __exit__ (step_id=%s)", self.step_id)
            return
        self.metadata[key] = value
        self._safe_emit(
            {
                "event": "step_metadata",
                "data": StepMetadataEvent(step_id=self.step_id, key=key, value=value).model_dump(),
            }
        )

    def _safe_emit(self, payload: dict[str, Any]) -> None:
        if not STEP_DISPLAY_ENABLED:
            return
        try:
            self._emit(payload)
        except Exception as exc:  # noqa: BLE001
            # Backpressure: queue.Full from `put_nowait`, or any emit failure. Drop, don't fail node.
            logger.warning("StepTracker emit dropped (step_id=%s): %s", self.step_id, exc)


__all__ = [
    "EmitFn",
    "STEP_DISPLAY_ENABLED",
    "STEP_ICONS",
    "STEP_LABELS",
    "StepEndEvent",
    "StepMetadataEvent",
    "StepStartEvent",
    "StepTracker",
    "StepType",
    "TOOL_LABELS",
    "label_for",
]
