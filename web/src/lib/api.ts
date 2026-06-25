import { getAuthHeaders } from "./auth";
import type { ApiError } from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

export class ApiClientError extends Error {
  readonly status: number;
  readonly code: string;
  readonly traceId: string | null;
  readonly details?: Record<string, unknown>;

  constructor(
    message: string,
    status: number,
    code: string,
    traceId: string | null,
    details?: Record<string, unknown>,
  ) {
    super(message);
    this.name = "ApiClientError";
    this.status = status;
    this.code = code;
    this.traceId = traceId;
    this.details = details;
  }
}

export interface ApiFetchOptions extends RequestInit {
  /** When false, omits the Authorization header (default true). */
  withAuth?: boolean;
}

interface ErrorEnvelope {
  error?: string;
  code?: string;
  details?: Record<string, unknown>;
  detail?: ErrorEnvelope | string;
}

function extractError(body: unknown, fallbackStatus: number): {
  message: string;
  code: string;
  details?: Record<string, unknown>;
} {
  const env = body as ErrorEnvelope | null | undefined;
  if (!env) return { message: "", code: `HTTP_${fallbackStatus}` };

  // FastAPI wraps detail dict for HTTPException.
  if (env.detail && typeof env.detail === "object") {
    const detail = env.detail;
    return {
      message: detail.error ?? env.error ?? "",
      code: detail.code ?? env.code ?? `HTTP_${fallbackStatus}`,
      details: detail.details ?? env.details,
    };
  }
  if (typeof env.detail === "string") {
    return { message: env.detail, code: env.code ?? `HTTP_${fallbackStatus}` };
  }
  return {
    message: env.error ?? "",
    code: env.code ?? `HTTP_${fallbackStatus}`,
    details: env.details,
  };
}

export async function apiFetch<T>(
  path: string,
  options: ApiFetchOptions = {},
): Promise<T> {
  const { withAuth = true, headers, ...rest } = options;
  const finalHeaders: Record<string, string> = {
    Accept: "application/json",
    ...(withAuth ? getAuthHeaders() : {}),
    ...((headers as Record<string, string> | undefined) ?? {}),
  };
  const url = `${API_BASE}${path.startsWith("/") ? path : "/" + path}`;
  const res = await fetch(url, { ...rest, headers: finalHeaders });
  const traceId = res.headers.get("X-Trace-Id");
  if (!res.ok) {
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      /* ignore */
    }
    const { message, code, details } = extractError(body, res.status);
    throw new ApiClientError(
      message || res.statusText || "Request failed",
      res.status,
      code,
      traceId,
      details,
    );
  }
  return (await res.json()) as T;
}
