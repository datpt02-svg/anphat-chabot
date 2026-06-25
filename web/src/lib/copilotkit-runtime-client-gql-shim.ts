// eslint-disable-next-line @typescript-eslint/no-explicit-any
import * as real from "@copilotkit/runtime-client-gql-real";

type MessageLike = {
  id?: unknown;
  __typename?: unknown;
  content?: unknown;
  role?: unknown;
  parentMessageId?: unknown;
  name?: unknown;
  arguments?: unknown;
  result?: unknown;
  actionExecutionId?: unknown;
  actionName?: unknown;
  threadId?: unknown;
  state?: unknown;
  agentName?: unknown;
  nodeName?: unknown;
  runId?: unknown;
  active?: unknown;
  running?: unknown;
  format?: unknown;
  bytes?: unknown;
  status?: unknown;
  [key: string]: unknown;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function isKnownTypename(value: unknown): value is string {
  return (
    typeof value === "string" &&
    (value === "TextMessageOutput" ||
      value === "ActionExecutionMessageOutput" ||
      value === "ResultMessageOutput" ||
      value === "AgentStateMessageOutput" ||
      value === "ImageMessageOutput")
  );
}

function getId(record: Record<string, unknown>): string | undefined {
  const id = record.id;
  if (typeof id === "string" || typeof id === "number") return String(id);
  return undefined;
}

function isFullMessage(record: Record<string, unknown>): boolean {
  return isKnownTypename(record.__typename);
}

function isStatusOnlyPatch(record: Record<string, unknown>): boolean {
  if (isFullMessage(record)) return false;
  return "status" in record && Object.keys(record).every((k) => k === "status" || k === "__typename" || k === "id");
}

function applyStatusPatch(target: MessageLike, patch: MessageLike) {
  if ("status" in patch) target.status = patch.status;
  if (patch.id !== undefined && target.id === undefined) target.id = patch.id;
  if (patch.__typename !== undefined && target.__typename === undefined) target.__typename = patch.__typename;
}

function mergeIncoming(messages: unknown): unknown[] {
  const list = Array.isArray(messages) ? messages : [messages];
  const byId = new Map<string, MessageLike>();
  const fullMessages: MessageLike[] = [];

  for (const raw of list) {
    if (!isRecord(raw)) continue;
    const record = raw as MessageLike;

    if (isStatusOnlyPatch(record)) {
      const id = getId(record);
      if (id) {
        const existing = byId.get(id);
        if (existing) {
          applyStatusPatch(existing, record);
          continue;
        }
      }
      continue;
    }

    if (!isFullMessage(record)) {
      // Unknown shape — keep it as-is so the converter can surface a
      // clearer error in development rather than silently dropping it.
      fullMessages.push(record);
      continue;
    }

    // The v1.10 client queries `content @stream`, which expects
    // `content` to be a `[String]`. The non-incremental backend
    // returns a plain `str`, so wrap it to keep the client happy.
    if (record.__typename === "TextMessageOutput" && typeof record.content === "string") {
      record.content = [record.content];
    }

    const id = getId(record);
    if (id) {
      const existing = byId.get(id);
      if (existing) {
        Object.assign(existing, record);
        continue;
      }
      byId.set(id, record);
      fullMessages.push(record);
      continue;
    }

    fullMessages.push(record);
  }

  return fullMessages;
}

export function convertGqlOutputToMessages(messages: unknown[]) {
  const safeMessages = mergeIncoming(messages);
  return (real as { convertGqlOutputToMessages: (m: unknown[]) => unknown[] }).convertGqlOutputToMessages(safeMessages);
}

export * from "@copilotkit/runtime-client-gql-real";
