"""M7b — CopilotKit GraphQL proxy.

The CopilotKit React client (`@copilotkit/react-core` 1.10) talks to a CopilotKit
Runtime via GraphQL — it issues `loadAgentState`, `generateCopilotResponse`,
and `availableAgents` queries/mutations against the runtime URL. The CopilotKit
Python SDK 0.1.94 only ships the REST portion of the protocol, so the React
client's GraphQL requests 404/422 against our ag-ui endpoint and the dev
console's "GET API KEY" prompt is shown.

This module mounts a small Strawberry GraphQL schema that satisfies the
subset of the CopilotKit Runtime contract that `@copilotkit/react-core` 1.10
actually issues, and routes every resolver through the existing
`_AdminGatedAgent` (M7) so the agent logic + auth + budget + trace_id stay
in one place.

Wire-up: `mount_copilotkit_graphql(app)` is called from the lifespan, after
`app.state.agent_graph` is compiled. It mounts `GraphQLRouter` at the
`COPILOTKIT_PATH` with the same path as the REST `/info` endpoint — Strawberry
handles GraphQL POST only, so the REST `add_fastapi_endpoint` continues to
serve `/api/copilotkit/agent/<name>` for any other consumer.
"""
from __future__ import annotations

import logging
import time
import uuid
from enum import Enum
from typing import Annotated, AsyncGenerator, List, Optional, Union

import strawberry
from strawberry.fastapi import GraphQLRouter
from strawberry.schema.config import StrawberryConfig
from strawberry.scalars import JSON
# `JSONObject` is the GraphQL scalar CopilotKit's React client uses for the
# `properties: JSONObject` argument on `generateCopilotResponse`. Strawberry
# only ships `JSON` in this version, so we register a thin scalar with the
# name `JSONObject` so the schema matches the React client's query exactly.
JSONObject = strawberry.scalar(
    JSON,
    name="JSONObject",
    description="Arbitrary JSON object passed to the agent as opaque metadata.",
)

from agents import config as agent_config
from agents.langgraph.state import AgentState
from agents.security import TokenClaims, hash_user_id, verify_token

logger = logging.getLogger("api.copilotkit_graphql")


# ---------------------------------------------------------------------------
# Strip `@defer` / `@stream` directives from the schema so the CopilotKit
# React 1.10 client can issue its standard GraphQL query without the server
# refusing with "Unknown directive '@defer'". Strawberry still imports the
# directive classes by default, so we monkey-patch the lookup tuple to be
# empty before `strawberry.Schema(...)` is constructed below.
# ---------------------------------------------------------------------------
try:
    import strawberry.schema._graphql_core as _strawberry_core

    _strawberry_core.incremental_execution_directives = ()
except Exception:  # pragma: no cover - older Strawberry
    pass

# In-process context for the GraphQL request: claims + trace_id, set by
# `_GraphQLContextMiddleware` so resolvers can read them without parsing
# the request twice. The REST middleware uses the same contextvar names.
from api.routes.copilotkit_bridge import (
    _copilotkit_claims,
    _copilotkit_trace_id,
    _run_error_event,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_token_from_headers(headers) -> Optional[str]:
    auth = headers.get("authorization", "") if headers else ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def _resolve_claims_from_request(info) -> TokenClaims:
    """Read per-request claims from the contextvar set by
    `register_copilotkit_auth_middleware` (in the REST bridge). The middleware
    runs on every FastAPI request — including GraphQL POSTs — so by the time
    a resolver fires, the contextvar is populated.
    """
    return _copilotkit_claims.get() or TokenClaims(
        user_id="dev",
        user_id_hash=hash_user_id("dev"),
        is_admin=True,
        raw={},
    )


# ---------------------------------------------------------------------------
# GraphQL types
# ---------------------------------------------------------------------------


@strawberry.enum
class MessageRole(Enum):
    user = "user"
    assistant = "assistant"
    system = "system"
    tool = "tool"
    developer = "developer"


# CopilotKit uses `__typename` polymorphism for messages (TextMessage /
# ActionExecutionMessage / ResultMessage / AgentStateMessage / ImageMessage).
# We implement the minimum — TextMessage — plus ActionExecutionMessage +
# ResultMessage so tool calls render correctly. Other types fall back to
# `BaseMessageOutput` (the interface).
# ActionExecutionMessage / ResultMessage / AgentStateMessage / ImageMessage).
# We implement the minimum — TextMessage — plus ActionExecutionMessage +
# ResultMessage so tool calls render correctly. Other types fall back to
# `BaseMessageOutput` (the interface) which CopilotKit queries with
# `... on BaseMessageOutput { id createdAt }`.
@strawberry.interface
class BaseMessageOutput:
    id: strawberry.ID
    createdAt: str
    status: Optional["MessageStatusUnion"] = None


@strawberry.type
class SuccessMessageStatus:
    code: str = "Success"


@strawberry.type
class PendingMessageStatus:
    code: str = "Pending"


@strawberry.type
class FailedMessageStatus:
    code: str = "Failed"
    reason: Optional[str] = None
    details: Optional[str] = None


MessageStatusUnion = Annotated[
    Union[SuccessMessageStatus, PendingMessageStatus, FailedMessageStatus],
    strawberry.union("MessageStatusUnion"),
]


@strawberry.type
class TextMessageOutput(BaseMessageOutput):
    role: MessageRole
    # Plain `str` instead of `strawberry.Streamable[str]`. The CopilotKit
    # React 1.10 client subscribes to `messages @stream { content @stream }`
    # and consumes each emitted chunk as a separate message list. When the
    # backend yields a fresh `TextMessageOutput` per `TEXT_MESSAGE_CONTENT`
    # event, the client only keeps the latest chunk and drops the rest.
    # Sending the **full** accumulated text in a single non-streamed field
    # makes the client render the complete assistant turn in one go.
    content: str = ""
    parentMessageId: Optional[strawberry.ID] = None


@strawberry.type
class ActionExecutionMessageOutput(BaseMessageOutput):
    name: str
    arguments: str = "{}"
    parentMessageId: Optional[strawberry.ID] = None


@strawberry.type
class ResultMessageOutput(BaseMessageOutput):
    result: str
    actionName: str
    actionExecutionId: str


@strawberry.type
class AgentStateMessageOutput(BaseMessageOutput):
    threadId: str
    state: str
    agentName: str
    nodeName: Optional[str] = None
    runId: Optional[str] = None
    active: bool = True
    role: MessageRole = MessageRole.assistant
    running: bool = False


@strawberry.type
class ImageMessageOutput(BaseMessageOutput):
    format: str
    bytes: str
    role: MessageRole
    parentMessageId: Optional[strawberry.ID] = None


@strawberry.interface
class BaseResponseStatus:
    code: str


@strawberry.type
class SuccessResponseStatus(BaseResponseStatus):
    code: str = "Success"


@strawberry.type
class FailedResponseStatus(BaseResponseStatus):
    code: str = "Failed"
    reason: Optional[str] = None
    details: Optional[str] = None


# `BaseResponseStatus` is the abstract supertype of SuccessResponseStatus +
# FailedResponseStatus — CopilotKit queries `... on BaseResponseStatus { code }`
# then narrows with `... on FailedResponseStatus { reason details }`.
@strawberry.interface
class BaseResponseStatus:
    code: str


ResponseStatusUnion = Annotated[
    Union[SuccessResponseStatus, FailedResponseStatus],
    strawberry.union("ResponseStatusUnion"),
]


# LangGraph interrupt event — emitted when a node calls `interrupt()`. CopilotKit
# uses this to surface human-in-the-loop prompts in the chat UI.
@strawberry.type
class LangGraphInterruptEvent:
    type: str
    name: str
    value: str


# CopilotKit-specific interrupt wrapper. The `data` field carries the messages
# that were active at the interrupt point — CopilotKit React queries
# `data { messages { ... } }` so we model it as a structured type.
@strawberry.type
class CopilotKitLangGraphInterruptData:
    messages: List[BaseMessageOutput] = strawberry.field(default_factory=list)
    value: str = ""


@strawberry.type
class CopilotKitLangGraphInterruptEvent:
    type: str
    name: str
    data: Optional[CopilotKitLangGraphInterruptData] = None
    value: str = ""


MetaEvent = Annotated[
    Union[LangGraphInterruptEvent, CopilotKitLangGraphInterruptEvent],
    strawberry.union("MetaEvent"),
]


@strawberry.type
class CopilotResponse:
    threadId: strawberry.ID
    runId: Optional[strawberry.ID] = None
    status: ResponseStatusUnion = strawberry.field(
        default_factory=lambda: SuccessResponseStatus()
    )
    # The CopilotKit client queries
    #   messages @stream {
    #     ... on TextMessageOutput { content @stream }
    #     ... on ImageMessageOutput { ... }
    #     ... on ActionExecutionMessageOutput { arguments @stream }
    #     ... on ResultMessageOutput { ... }
    #     ... on AgentStateMessageOutput { ... }
    #     ... on BaseMessageOutput { id createdAt }
    #   }
    # We type `messages` as a plain list (not `Streamable`) so the response
    # is sent as a single non-incremental payload. The `@stream` directive
    # is harmless on a list — graphql-core just expands the full list once.
    # This eliminates the `data: { status: null }` incremental patches that
    # were breaking the v1.10 client parser.
    messages: List[BaseMessageOutput] = strawberry.field(default_factory=list)
    # Strawberry excludes concrete types from the schema unless they are
    # referenced directly by a field. The `messages` field only references
    # the interface, so the concrete `TextMessageOutput` is excluded. The
    # field below forces it into the schema; in practice the client never
    # queries it (it goes through `messages`).
    textMessages: List[TextMessageOutput] = strawberry.field(default_factory=list)
    toolMessages: List[ActionExecutionMessageOutput] = strawberry.field(default_factory=list)
    # Sibling fields for the other message kinds — these are kept in the schema
    # so the inline-fragment queries (`... on ActionExecutionMessageOutput`)
    # resolve instead of returning "Unknown type".
    toolCalls: List[ActionExecutionMessageOutput] = strawberry.field(default_factory=list)
    toolResults: List[ResultMessageOutput] = strawberry.field(default_factory=list)
    agentStateMessages: List[AgentStateMessageOutput] = strawberry.field(default_factory=list)
    images: List[ImageMessageOutput] = strawberry.field(default_factory=list)
    metaEvents: List[MetaEvent] = strawberry.field(default_factory=list)
    # `BaseMessageOutput` is the abstract supertype used by the CopilotKit
    # client (`... on BaseMessageOutput { id createdAt }`). We expose it via
    # a list so Strawberry registers the interface in the schema; in
    # practice the client never reads from this field.
    baseMessages: List[BaseMessageOutput] = strawberry.field(default_factory=list)
    extensions: Optional["ExtensionsResponse"] = None


@strawberry.type
class ExtensionsResponse:
    openaiAssistantAPI: Optional["OpenAIAssistantAPIExtensions"] = None


@strawberry.type
class OpenAIAssistantAPIExtensions:
    runId: str
    threadId: str


@strawberry.type
class AgentDescription:
    name: strawberry.ID
    id: strawberry.ID
    description: Optional[str] = None


@strawberry.type
class AgentsResponse:
    agents: List[AgentDescription]


@strawberry.type
class LoadAgentStateResponse:
    threadId: strawberry.ID
    threadExists: bool
    state: str
    messages: str


# Input types ----------------------------------------------------------------


@strawberry.input
class TextMessageContent:
    """Nested object the CopilotKit React client puts inside `TextMessageInput`.

    The v1.10 client sends `parentMessageId: null` even though the schema
    doesn't declare it. Strawberry rejects unknown fields on input by
    default, which crashes `generateCopilotResponse` with a GraphQL
    validation error. Accept the field as optional and ignore it server-side.
    """
    content: str
    role: MessageRole = MessageRole.user
    parentMessageId: Optional[strawberry.ID] = None


@strawberry.input
class TextMessageInput:
    id: strawberry.ID
    createdAt: str
    textMessage: TextMessageContent
    parentMessageId: Optional[strawberry.ID] = None


@strawberry.input
class ActionExecutionContent:
    name: str
    arguments: str = "{}"


@strawberry.input
class ActionExecutionMessageInput:
    id: strawberry.ID
    createdAt: str
    actionExecutionMessage: ActionExecutionContent
    parentMessageId: Optional[strawberry.ID] = None


@strawberry.input
class ResultMessageInput:
    id: strawberry.ID
    createdAt: str
    resultMessage: Optional[JSONObject] = None
    actionName: str = ""
    actionExecutionId: str = ""


@strawberry.input
class ImageMessageInput:
    id: strawberry.ID
    createdAt: str
    image: Optional[JSONObject] = None  # {format, bytes, role, parentMessageId?}
    role: MessageRole
    parentMessageId: Optional[strawberry.ID] = None


# Discriminated by `type` field
MessageInput = Annotated[
    Union[TextMessageInput, ActionExecutionMessageInput, ResultMessageInput, ImageMessageInput],
    strawberry.union("MessageInput"),
]
# Strawberry input types don't accept `List[Union]` in fields (graphql-core
# rejects non-null list of union). The CopilotKit React client only ever sends
# TextMessageInput for chat turns, so we use the concrete type for the
# resolver signature while still defining the union above for documentation.
MessageInputList = List[TextMessageInput]


@strawberry.input
class GenerateCopilotResponseMetadataInput:
    requestType: Optional[str] = None


@strawberry.input
class AgentStateInput:
    threadId: strawberry.ID
    agentName: strawberry.ID
    state: Optional[str] = None


@strawberry.input
class FrontendActionInput:
    """One CopilotKit frontend action declaration (e.g. `navigateToSearch`).
    The `jsonSchema` field is a stringified JSON Schema object describing
    the action's parameters — we keep it as `JSONObject` rather than `str`
    so callers can pass it as a nested object and Strawberry serialises it
    to the GraphQL `JSON` scalar.
    """
    name: str
    description: str = ""
    available: str = "enabled"
    jsonSchema: Optional[JSONObject] = None


@strawberry.input
class FrontendInput:
    actions: List[FrontendActionInput] = strawberry.field(default_factory=list)
    url: Optional[str] = None


@strawberry.input
class CloudInput:
    # Reserved for CopilotKit Cloud. Always empty in self-hosted dev.
    guardrails: Optional[str] = None


@strawberry.input
class ForwardedParametersInput:
    tool_choice: Optional[str] = None


@strawberry.input
class OpenAIAssistantAPIExtensionsInput:
    runId: str
    threadId: str


@strawberry.input
class ExtensionsInput:
    openaiAssistantAPI: Optional[OpenAIAssistantAPIExtensionsInput] = None


@strawberry.input
class AgentSessionInput:
    agentName: strawberry.ID


@strawberry.input
class MetaEventInput:
    type: str
    name: str
    data: Optional[str] = None  # JSON-encoded


@strawberry.input
class CopilotContextInput:
    description: Optional[str] = None
    value: Optional[str] = None


@strawberry.input
class GenerateCopilotResponseInput:
    metadata: GenerateCopilotResponseMetadataInput
    threadId: Optional[strawberry.ID] = None
    runId: Optional[strawberry.ID] = None
    messages: List[TextMessageInput] = strawberry.field(default_factory=list)
    frontend: FrontendInput
    cloud: Optional[CloudInput] = None
    forwardedParameters: Optional[ForwardedParametersInput] = None
    agentSession: Optional[AgentSessionInput] = None
    agentState: Optional[AgentStateInput] = None
    agentStates: Optional[List[AgentStateInput]] = None
    extensions: Optional[ExtensionsInput] = None
    metaEvents: Optional[List[MetaEventInput]] = None
    context: Optional[List[CopilotContextInput]] = None


@strawberry.input
class LoadAgentStateInput:
    threadId: strawberry.ID
    agentName: strawberry.ID


# ---------------------------------------------------------------------------
# Resolvers
# ---------------------------------------------------------------------------


def _agent_from_request(info, agent_name: str):
    """Read the `_AdminGatedAgent` instance from `app.state` and verify it
    matches the requested name. Returns `None` if the bridge wasn't mounted.
    """
    request = (info.context or {}).get("request")
    if request is None:
        return None
    sdk_agents = getattr(request.app.state, "copilotkit_agents", None) or []
    for a in sdk_agents:
        if a.name == agent_name:
            return a
    return None


async def _messages_to_text(messages: List[MessageInput]) -> str:
    """Collapse GraphQL MessageInput union into a single user-text string the
    agent can consume. The M5 graph expects `state.messages` to be langchain
    `BaseMessage` list; for chat use the latest user text is enough.
    """
    parts: List[str] = []
    for m in messages:
        # Strawberry unions expose `__typename` automatically.
        if isinstance(m, TextMessageInput):
            tm = m.textMessage
            if tm.role == MessageRole.user and tm.content:
                parts.append(tm.content)
        elif isinstance(m, ActionExecutionMessageInput):
            parts.append(f"[action {m.name}: {m.arguments}]")
        elif isinstance(m, ResultMessageInput):
            parts.append(f"[result {m.actionName}: {m.result}]")
    # Concatenate user turns with newlines — agent sees a single HumanMessage.
    return "\n".join(parts) if parts else ""


async def _drain_agent_response(
    info,
    agent_name: str,
    thread_id: str,
    user_text: str,
    input_messages: List[MessageInput],
) -> List[BaseMessageOutput]:
    """Drive `_AdminGatedAgent.execute()` once and translate ag-ui events
    into a single non-streamed list of CopilotKit message outputs.

    This replaces the earlier streaming variant because the CopilotKit
    React 1.10 client cannot reliably consume `@stream`/`@defer` patches
    from a custom Strawberry backend. We collapse the entire agent run
    into one final list of messages.

    Event mapping (ag-ui → CopilotKit message):
    - TEXT_MESSAGE_START  → reserve an id, reset buffer
    - TEXT_MESSAGE_CONTENT → append delta to buffer
    - TEXT_MESSAGE_END    → no-op
    - TOOL_CALL_START    → ActionExecutionMessageOutput (recorded once)
    - TOOL_CALL_RESULT   → ResultMessageOutput
    - STATE_SNAPSHOT     → capture `final_response` / `early_response` for
      fallback text (assistant text path that bypasses streaming)
    - RUN_FINISHED       → close, emit a single TextMessageOutput with
      the full accumulated content (or fallback if no text streamed)
    - RUN_ERROR          → close
    - other events       → ignored
    """
    from ag_ui.core.events import EventType

    agent = _agent_from_request(info, agent_name)
    if agent is None:
        return []

    run_id = str(uuid.uuid4())
    trace_id = _copilotkit_trace_id.get() or uuid.uuid4().hex
    logger.info(
        "graphql_generate_copilot_response trace_id=%s thread_id=%s run_id=%s agent=%s",
        trace_id, thread_id, run_id, agent_name,
    )

    # Build the `RunAgentInput` envelope the ag-ui agent expects. The GraphQL
    # `MessageRole` enum on the wire is lowercase (Strawberry-generated
    # enum values are the Python `.value` of each member).
    def _role_value(m: object) -> str:
        r = getattr(m, "role", "user")
        if hasattr(r, "value"):
            return r.value
        return str(r)

    def _to_ag_ui_message(m: object) -> dict:
        """Convert a CopilotKit `TextMessageInput` (which has a nested
        `textMessage { content, role }` object) into the flat shape the
        ag-ui `RunAgentInput` expects.
        """
        if isinstance(m, TextMessageInput):
            tm = m.textMessage
            return {
                "id": m.id,
                "role": _role_value(tm),
                "content": tm.content,
            }
        # Other message kinds (ActionExecutionMessageInput, etc.) — fall
        # through to a sensible default. We don't expect the React client
        # to send these in a chat turn, so treat as user text fallback.
        return {"id": getattr(m, "id", "msg"), "role": "user", "content": ""}

    ag_ui_messages = [_to_ag_ui_message(m) for m in input_messages]

    text_message_id: Optional[str] = None
    text_buffer: str = ""
    capture_text = False
    final_response_text: Optional[str] = None
    emitted: List[BaseMessageOutput] = []

    def _now_iso() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    try:
        async for encoded in agent.execute(
            state={},
            thread_id=thread_id,
            messages=ag_ui_messages,
            actions=[],
            node_name=None,
        ):
            # `agent.execute()` returns an async iterator that yields SSE
            # strings (we encoded with EventEncoder in copilotkit_bridge.py).
            # Parse each line back to a JSON event so we can route it.
            for line in encoded.splitlines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload:
                    continue
                try:
                    ev = __import__("json").loads(payload)
                except Exception:
                    continue
                ev_type = ev.get("type")
                if ev_type == EventType.TEXT_MESSAGE_START:
                    candidate_id = ev.get("messageId") or str(uuid.uuid4())
                    if str(candidate_id).startswith("lc_run--"):
                        capture_text = False
                        continue
                    capture_text = True
                    text_message_id = candidate_id
                    text_buffer = ""
                elif ev_type == EventType.TEXT_MESSAGE_CONTENT:
                    if not capture_text:
                        continue
                    delta = ev.get("delta", "") or ""
                    text_buffer += delta
                elif ev_type == EventType.TEXT_MESSAGE_END:
                    if not capture_text:
                        continue
                    capture_text = False
                    pass
                elif ev_type == EventType.TOOL_CALL_START:
                    tool_call_id = ev.get("toolCallId") or str(uuid.uuid4())
                    emitted.append(
                        ActionExecutionMessageOutput(
                            id=strawberry.ID(tool_call_id),
                            createdAt=_now_iso(),
                            name=ev.get("toolCallName", ""),
                            arguments=ev.get("toolCallArgs", "") or "{}",
                        )
                    )
                elif ev_type == EventType.TOOL_CALL_RESULT:
                    tool_call_id = ev.get("toolCallId") or "unknown"
                    emitted.append(
                        ResultMessageOutput(
                            id=strawberry.ID(str(uuid.uuid4())),
                            createdAt=_now_iso(),
                            result=str(ev.get("content", ""))[:4000],
                            actionName=ev.get("toolCallName", ""),
                            actionExecutionId=tool_call_id,
                        )
                    )
                elif ev_type == EventType.STATE_SNAPSHOT:
                    snapshot = ev.get("snapshot") or {}
                    if isinstance(snapshot, dict):
                        if snapshot.get("final_response"):
                            final_response_text = str(snapshot["final_response"])
                        elif snapshot.get("early_response"):
                            # `retrieve_catalog` sets `early_response` when the
                            # search returns 0 hits on a factual intent (no LLM
                            # call happens, so no AIMessage is ever appended to
                            # state.messages). Capture it here and surface as
                            # the assistant text on RUN_FINISHED.
                            final_response_text = str(snapshot["early_response"])
                        elif isinstance(snapshot.get("messages"), list):
                            for msg in reversed(snapshot["messages"]):
                                if msg.get("type") == "ai":
                                    content = str(msg.get("content") or "")
                                    if content and not content.lstrip().startswith("{"):
                                        final_response_text = content
                                        break
                elif ev_type == "MESSAGES_SNAPSHOT":
                    snapshot_messages = ev.get("messages") or []
                    if isinstance(snapshot_messages, list):
                        for msg in reversed(snapshot_messages):
                            if str(msg.get("role") or "") == "assistant":
                                content = str(msg.get("content") or "")
                                if content and not content.lstrip().startswith("{"):
                                    final_response_text = content
                                    break
                elif ev_type == EventType.RUN_FINISHED:
                    if text_message_id is None and final_response_text:
                        text_message_id = str(uuid.uuid4())
                        text_buffer = final_response_text
                    if text_message_id:
                        emitted.append(
                            TextMessageOutput(
                                id=strawberry.ID(text_message_id),
                                createdAt=_now_iso(),
                                role=MessageRole.assistant,
                                content=text_buffer,
                            )
                        )
                    return emitted
                elif ev_type == EventType.RUN_ERROR:
                    logger.warning(
                        "graphql_generate_copilot_response RUN_ERROR trace_id=%s msg=%s",
                        trace_id, ev.get("message"),
                    )
                    return emitted
    except Exception as exc:
        logger.exception("GraphQL run failed for %s", agent_name)
        # Surface the error as a single assistant text so the UI shows
        # something instead of hanging on the loading dots.
        emitted.append(
            TextMessageOutput(
                id=strawberry.ID(str(uuid.uuid4())),
                createdAt=_now_iso(),
                role=MessageRole.assistant,
                content=f"Internal error: {exc}",
            )
        )

    # Edge case: stream ended without RUN_FINISHED/RUN_ERROR (shouldn't
    # happen, but flush whatever text we have so the UI doesn't hang).
    if text_message_id:
        emitted.append(
            TextMessageOutput(
                id=strawberry.ID(text_message_id),
                createdAt=_now_iso(),
                role=MessageRole.assistant,
                content=text_buffer,
            )
        )
    return emitted


@strawberry.type
class Query:
    @strawberry.field
    async def available_agents(self, info: strawberry.Info) -> AgentsResponse:
        """Return the single agent the bridge currently exposes.
        Reads `app.state.copilotkit_agents` (set by `mount_copilotkit_bridge`).
        """
        request = (info.context or {}).get("request")
        if request is None:
            return AgentsResponse(agents=[])
        sdk_agents = getattr(request.app.state, "copilotkit_agents", []) or []
        return AgentsResponse(
            agents=[
                AgentDescription(
                    name=strawberry.ID(a.name),
                    id=strawberry.ID(a.name),
                    description=getattr(a, "description", None) or "",
                )
                for a in sdk_agents
            ]
        )

    @strawberry.field
    def hello(self) -> str:
        return "world"

    @strawberry.field
    async def load_agent_state(
        self, info: strawberry.Info, data: LoadAgentStateInput
    ) -> LoadAgentStateResponse:
        """Return the LangGraph checkpoint state for a thread, as JSON-encoded
        strings (CopilotKit's schema declares `state: String`).
        """
        import json as _json
        agent = _agent_from_request(info, str(data.agentName))
        if agent is None:
            return LoadAgentStateResponse(
                threadId=data.threadId,
                threadExists=False,
                state="{}",
                messages="[]",
            )
        try:
            state = await agent.get_state(thread_id=str(data.threadId))
        except Exception as exc:
            logger.info("load_agent_state: get_state failed: %s", exc)
            return LoadAgentStateResponse(
                threadId=data.threadId,
                threadExists=False,
                state="{}",
                messages="[]",
            )
        thread_exists = state.get("threadExists", False)
        return LoadAgentStateResponse(
            threadId=data.threadId,
            threadExists=thread_exists,
            state=_json.dumps(state.get("state", {}) or {}, default=str)[:16000],
            messages=_json.dumps(state.get("messages", []) or [], default=str)[:16000],
        )


@strawberry.type
class Mutation:
    @strawberry.field
    async def generate_copilot_response(
        self,
        info: strawberry.Info,
        data: GenerateCopilotResponseInput,
        properties: Optional[JSONObject] = None,
    ) -> CopilotResponse:
        """Drive the agent and stream messages back via the `@stream` directive.
        Returns the full `CopilotResponse` shell (threadId/runId/status) plus
        a `Streamable[BaseMessageOutput]` that yields message chunks as the
        agent runs.
        """
        thread_id = str(data.threadId) if data.threadId else str(uuid.uuid4())
        agent_name = (
            str(data.agentSession.agentName)
            if data.agentSession and data.agentSession.agentName
            else (str(data.agentState.agentName) if data.agentState and data.agentState.agentName
                  else "anphat-catalog")
        )
        run_id = str(data.runId) if data.runId else str(uuid.uuid4())

        # Drive the agent once and collapse the full event stream into a
        # single list of CopilotKit message outputs. The mutation returns
        # a non-incremental response (no `@stream` / `@defer`), so the
        # client receives a complete `messages` list in one payload.
        all_messages = await _drain_agent_response(
            info, agent_name, thread_id,
            await _messages_to_text(data.messages),
            list(data.messages),
        )

        # Sort the messages into the per-kind sibling fields so the inline
        # fragment queries (`... on TextMessageOutput`, `... on ResultMessageOutput`,
        # `... on ActionExecutionMessageOutput`) on the client resolve
        # correctly.
        text_messages: list = [m for m in all_messages if isinstance(m, TextMessageOutput)]
        tool_calls: list = [m for m in all_messages if isinstance(m, ActionExecutionMessageOutput)]
        tool_results: list = [m for m in all_messages if isinstance(m, ResultMessageOutput)]
        agent_states: list = [m for m in all_messages if isinstance(m, AgentStateMessageOutput)]
        images: list = [m for m in all_messages if isinstance(m, ImageMessageOutput)]

        return CopilotResponse(
            threadId=strawberry.ID(thread_id),
            runId=strawberry.ID(run_id),
            status=SuccessResponseStatus(),
            messages=all_messages,
            toolCalls=tool_calls,
            toolResults=tool_results,
            agentStateMessages=agent_states,
            images=images,
            metaEvents=[],
            extensions=ExtensionsResponse(
                openaiAssistantAPI=OpenAIAssistantAPIExtensions(
                    runId=strawberry.ID(run_id),
                    threadId=strawberry.ID(thread_id),
                )
            ),
        )


schema = strawberry.Schema(
    query=Query,
    mutation=Mutation,
    # Non-incremental: send the full `CopilotResponse.messages` (and
    # `metaEvents`) in one response. The v1.10 CopilotKit React client
    # only knows how to consume a non-incremental message list — the
    # incremental `@stream` / `@defer` patches (`data: { status: null }`)
    # confused its parser and produced `Unknown message type` errors.
    config=StrawberryConfig(enable_experimental_incremental_execution=False),
)


# ---------------------------------------------------------------------------
# Register no-op `@defer` / `@stream` directives at the graphql-core layer.
# The schema-level `directives=[...]` only works for *operation* directives
# declared on schema. The CopilotKit v1.10 client emits `@defer` on selection
# sets (field / fragment), so we need to register them as type-system
# directives with the same locations.
# ---------------------------------------------------------------------------
def _install_noop_directives() -> None:
    """Make CopilotKit v1.10 client queries (which always include
    `@defer` / `@stream` directives) pass through our non-incremental
    backend.

    graphql-core's default `execute` rejects schemas that declare
    `@defer` / `@stream`. We don't want to declare them on the schema
    (we're not doing incremental delivery), but we also can't avoid
    them in the client query. So we strip the directives from the
    incoming document *after* parse but *before* validation, and we
    install a custom GraphQL document parser that returns the stripped
    document. graphql-core's `KnownDirectives` validation rule then
    sees a clean document and accepts the operation.
    """
    try:
        import graphql as _graphql
        from graphql.language import parser as _graphql_parser

        if getattr(_graphql_parser.parse, "__anphat_strip_defer_stream__", False):
            return

        original_parse = _graphql_parser.parse

        def patched_parse(source, *args, **kwargs):  # type: ignore[no-redef]
            document = original_parse(source, *args, **kwargs)
            _strip_defer_stream_in_place(document)
            return document

        patched_parse.__anphat_strip_defer_stream__ = True  # type: ignore[attr-defined]
        _graphql_parser.parse = patched_parse
        # Some entry points go through `graphql.parse` directly. Mirror
        # the patch on the top-level re-export as well.
        _graphql.parse = patched_parse  # type: ignore[assignment]
        logger.info("Installed @defer/@stream stripper on graphql parser")
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("install_noop_directives failed: %s", exc)


_DEFER_STREAM_DIRECTIVE_RE = None
_DIRECTIVE_TAIL_RE = None


def _strip_defer_stream_in_query_text(query: str) -> str:
    """Strip `@defer` / `@stream` directives from a raw GraphQL query
    string. graphql-core's `parse` may be invoked from multiple code
    paths in the Strawberry / urql stack, so the parser-level patch
    isn't always reached. Doing the strip on the raw text before
    FastAPI hands the body to Strawberry is the most reliable seam.
    """
    if not query:
        return query
    # Match `@defer` or `@stream` followed by optional args and the
    # trailing space; collapse to nothing.
    import re
    return re.sub(r"@(defer|stream)\b(?:\s*\([^)]*\))?", "", query)


def _install_query_text_strip_middleware(app) -> None:
    """FastAPI middleware that rewrites POST /api/copilotkit-graphql
    bodies to remove `@defer` / `@stream` directives. The Strawberry
    router then parses a query that graphql-core accepts without
    requiring the experimental execution path to be enabled.
    """
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request as StarletteRequest

    class _StripDirectivesMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: StarletteRequest, call_next):
            if request.method.upper() == "POST" and "copilotkit-graphql" in request.url.path:
                body_bytes = await request.body()
                if body_bytes:
                    try:
                        import json as _json
                        payload = _json.loads(body_bytes)
                        if isinstance(payload, dict) and "query" in payload and isinstance(payload["query"], str):
                            new_query = _strip_defer_stream_in_query_text(payload["query"])
                            if new_query != payload["query"]:
                                payload["query"] = new_query
                                body_bytes = _json.dumps(payload).encode("utf-8")
                    except Exception:
                        # Non-JSON body or malformed payload — fall through
                        # untouched; Strawberry will surface a parse error.
                        pass
                # Rebuild the request with the rewritten body.
                async def receive() -> dict:
                    return {"type": "http.request", "body": body_bytes, "more_body": False}

                request = StarletteRequest(
                    request.scope,
                    receive,
                )
            return await call_next(request)

    app.add_middleware(_StripDirectivesMiddleware)


def _strip_defer_stream_in_place(document: object) -> None:
    """Recursively remove `@defer` / `@stream` directives from every node
    in a GraphQL document so graphql-core validation accepts the query
    without raising "Unknown directive".
    """
    from graphql.language import ast

    targets = {"defer", "stream"}

    def visit(node: object) -> None:
        directives = getattr(node, "directives", None)
        if isinstance(directives, list) and directives:
            directives[:] = [
                d for d in directives if getattr(d, "name", None) and d.name.value not in targets
            ]
        for attr in ("definitions", "selection_set", "arguments", "variable_definitions"):
            child = getattr(node, attr, None)
            if isinstance(child, list):
                for item in child:
                    if isinstance(item, ast.Node):
                        visit(item)
            elif isinstance(child, ast.Node):
                visit(child)

    visit(document)


_install_noop_directives()


# ---------------------------------------------------------------------------
# Public mount
# ---------------------------------------------------------------------------


def mount_copilotkit_graphql(app, path: str) -> None:
    """Mount the GraphQL proxy at `path` (typically the same path as the REST
    info endpoint). Strawberry registers a POST handler that scopes itself
    to the given path — `/api/copilotkit` (no trailing slash) becomes the
    GraphQL endpoint, leaving the existing REST routes (`/api/copilotkit/`,
    `/api/copilotkit/agent/<name>`, …) untouched.
    """
    if not agent_config.COPILOTKIT_ENABLED:
        logger.info("COPILOTKIT_ENABLED=false — GraphQL proxy not mounted")
        return

    # Strawberry's `GraphQLRouter` validates that `context_getter` returns
    # either a dict or a `BaseContext` subclass. Strawberry automatically
    # injects `request`, `response`, and `background_tasks` into the returned
    # context — so our getter just sets the auth contextvars and lets
    # Strawberry fill in the FastAPI bits.
    async def _context_getter() -> dict:
        # No `request` arg here on purpose: adding it makes FastAPI treat
        # the function as a dependency that needs query params (422). The
        # request/response are injected by Strawberry into the dict below.
        # (We can't read the auth headers from this getter — the auth
        # contextvars are set by the REST middleware BEFORE the GraphQL
        # dispatch, so resolvers see consistent `claims`/`trace_id` via
        # the shared module-level contextvars.)
        if not _copilotkit_trace_id.get():
            _copilotkit_trace_id.set(uuid.uuid4().hex)
        if _copilotkit_claims.get() is None:
            _copilotkit_claims.set(TokenClaims(
                user_id="dev",
                user_id_hash=hash_user_id("dev"),
                is_admin=True,
                raw={},
            ))
        return {}

    # The CopilotKit Python SDK's `add_fastapi_endpoint` mounts a catch-all
    # `<path>/{path:path}` route that swallows every sub-path (including
    # `/graphql`) and returns 404 from the SDK's own router. To avoid the
    # conflict we expose the GraphQL proxy on a sibling path
    # `<path>-graphql` (e.g. `/api/copilotkit-graphql`) — the React client
    # treats `runtimeUrl` as a single endpoint, so the same origin works.
    graphql_path = (path.rstrip("/") or "/api/copilotkit").rstrip("/") + "-graphql"
    router = GraphQLRouter(
        schema,
        path=graphql_path,
        graphql_ide=None,  # No GraphiQL UI — keep dev console out of the way
        allow_queries_via_get=False,  # CopilotKit only POSTs
        context_getter=_context_getter,
        tags=["copilotkit"],
    )
    # Strip `@defer` / `@stream` from incoming query strings BEFORE the
    # Strawberry router parses them. The v1.10 CopilotKit React client
    # always includes these directives in its standard query, but
    # graphql-core rejects them when the schema doesn't declare them.
    # Install the middleware FIRST (Starlette processes middleware in
    # reverse order of registration, so the last `add_middleware` runs
    # first) — call it before `app.include_router` so the rewrite
    # happens before Strawberry's request parser runs.
    _install_query_text_strip_middleware(app)
    logger.info("CopilotKit GraphQL middleware installed: @defer/@stream stripper")

    app.include_router(router)
    logger.info("CopilotKit GraphQL proxy mounted at %s", graphql_path)


__all__ = ["mount_copilotkit_graphql"]
