// Shared auth header helper for TanStack Query fetches AND CopilotKit
// runtime (R10). Reads NEXT_PUBLIC_DEV_BEARER_TOKEN at build time.

export type ApiAuthHeaders = Record<string, string>;

export function getAuthHeaders(): ApiAuthHeaders {
  const token = process.env.NEXT_PUBLIC_DEV_BEARER_TOKEN;
  if (!token) return {};
  return { Authorization: `Bearer ${token}` };
}
