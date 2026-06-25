"use client";

// CopilotKit React 1.10 receives merged GraphQL incremental payloads from
// `CopilotRuntimeClient.asStream()`. The merge can leave placeholder entries
// like `{ status: null }` inside `generateCopilotResponse.messages`, and
// `convertGqlOutputToMessages()` then throws `Unknown message type` because
// those placeholders have no `__typename`.
//
// Patch at the transport seam instead of globally patching `Array.prototype`:
// sanitize streamed payloads BEFORE react-core consumes them.

declare global {
  interface Window {
    __copilotkitRuntimeClientWorkaroundInstalled?: boolean;
  }
}

type JsonLike = null | boolean | number | string | JsonLike[] | { [key: string]: JsonLike };

type GraphqlMessagePatch = {
  __typename?: string;
  status?: unknown;
  [key: string]: unknown;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function isStatusOnlyPatch(value: unknown): value is GraphqlMessagePatch {
  if (!isRecord(value)) return false;
  const keys = Object.keys(value);
  return keys.length === 1 && keys[0] === "status";
}

function isRenderableMessage(value: unknown): value is GraphqlMessagePatch {
  return isRecord(value) && typeof value.__typename === "string";
}

function sanitizeValue(value: unknown): unknown {
  if (Array.isArray(value)) {
    const sanitized = value
      .filter((item) => !isStatusOnlyPatch(item))
      .map((item) => sanitizeValue(item));
    return sanitized;
  }

  if (!isRecord(value)) {
    return value;
  }

  const next: Record<string, unknown> = {};
  for (const [key, child] of Object.entries(value)) {
    next[key] = sanitizeValue(child);
  }
  return next;
}

if (typeof window !== "undefined" && !window.__copilotkitRuntimeClientWorkaroundInstalled) {
  window.__copilotkitRuntimeClientWorkaroundInstalled = true;

  const loadRuntimeClientModule = new Function(
    "moduleName",
    "return import(moduleName)",
  ) as (moduleName: string) => Promise<Record<string, unknown>>;

  void loadRuntimeClientModule("@copilotkit/runtime-client-gql").then((mod) => {
    const RuntimeClient = mod.CopilotRuntimeClient as {
      prototype?: {
        asStream?: (source: unknown) => ReadableStream<unknown>;
        __anphatPatchedAsStream?: boolean;
      };
    };

    const proto = RuntimeClient?.prototype;
    if (!proto || typeof proto.asStream !== "function" || proto.__anphatPatchedAsStream) {
      return;
    }

    const originalAsStream = proto.asStream;
    proto.__anphatPatchedAsStream = true;

    proto.asStream = function patchedAsStream(source: unknown) {
      const stream = originalAsStream.call(this, source);
      return new ReadableStream<unknown>({
        start(controller) {
          const reader = stream.getReader();

          const pump = (): Promise<void> =>
            reader.read().then(({ done, value }) => {
              if (done) {
                controller.close();
                return;
              }
              controller.enqueue(sanitizeValue(value));
              return pump();
            }).catch((error) => {
              controller.error(error);
            });

          return pump();
        },
        cancel(reason) {
          return stream.cancel(reason);
        },
      });
    };
  }).catch(() => {
    // No-op. If import path changes in future versions, app keeps old behavior
    // instead of crashing at startup.
  });
}

export {};
