"use client";

import dynamic from "next/dynamic";
import { CopilotKit } from "@copilotkit/react-core";
import "@copilotkit/react-ui/styles.css";
import { getAuthHeaders } from "@/lib/auth";
import { useEffect } from "react";

const CopilotSidebar = dynamic(
  () => import("@copilotkit/react-ui").then((m) => m.CopilotSidebar),
  { ssr: false },
);
const CopilotPopup = dynamic(
  () => import("@copilotkit/react-ui").then((m) => m.CopilotPopup),
  { ssr: false },
);

const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";
// CopilotKit React 1.10 always POSTs GraphQL to `runtimeUrl` (no separate
// graphql endpoint config). The Strawberry GraphQL proxy lives at
// `<path>-graphql` — the React client points here. The REST endpoints
// (`<path>/info`, `<path>/agent/<name>`, …) stay on the base path.
const copilotPath = process.env.NEXT_PUBLIC_COPILOTKIT_PATH || "/api/copilotkit";
const graphqlPath = (copilotPath.replace(/\/$/, "")) + "-graphql";

export function CopilotProvider({ children }: { children: React.ReactNode }) {
  const headers = getAuthHeaders();
  const labels = {
    title: "An Phát Trợ Lý",
    initial: "Tôi có thể giúp bạn tìm laptop, build PC, hoặc so sánh sản phẩm.",
  };

  // Strip `@defer` / `@stream` directives from any GraphQL query the
  // CopilotKit runtime client sends. The v1.10 client always includes
  // them in its standard mutation, but our non-incremental backend
  // rejects them. The runtime client uses fetch internally, so we
  // monkey-patch window.fetch on the same origin to rewrite the
  // request body before it leaves the browser.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const originalFetch = window.fetch.bind(window);
    if ((window.fetch as unknown as { __anphatStripDeferStream?: boolean }).__anphatStripDeferStream) {
      return;
    }
    (window.fetch as unknown as { __anphatStripDeferStream?: boolean }).__anphatStripDeferStream = true;
    const strip = (text: string) =>
      text.replace(/@(defer|stream)\b(?:\s*\([^)]*\))?/g, "");
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      try {
        const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
        if (url && url.includes("copilotkit-graphql") && init && init.body) {
          let body = init.body as string;
          if (typeof body === "string" && body.includes("@")) {
            try {
              const payload = JSON.parse(body);
              if (payload && typeof payload.query === "string") {
                payload.query = strip(payload.query);
                init = { ...init, body: JSON.stringify(payload) };
              }
            } catch {
              // Non-JSON body — rewrite the raw text and hope for the best.
              init = { ...init, body: strip(body) };
            }
          }
        }
      } catch {
        // never let the patch break the request
      }
      return originalFetch(input, init);
    };
  }, []);

  return (
    <CopilotKit
      runtimeUrl={`${apiBase}${graphqlPath}`}
      agent="anphat-catalog"
      headers={headers}
    >
      {children}
      <div className="hidden md:block">
        <CopilotSidebar defaultOpen={false} labels={labels} />
      </div>
      <div className="md:hidden">
        <CopilotPopup labels={labels} />
      </div>
    </CopilotKit>
  );
}
