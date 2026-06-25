"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { CopilotProvider } from "./copilot-provider";

export function Providers({ children }: { children: React.ReactNode }) {
  // Production safety check: fail fast if dev token leaked to prod build.
  if (
    process.env.NODE_ENV === "production" &&
    process.env.NEXT_PUBLIC_DEV_BEARER_TOKEN
  ) {
    throw new Error("NEXT_PUBLIC_DEV_BEARER_TOKEN leaked to production build");
  }

  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 60_000,
            gcTime: 5 * 60_000,
            retry: 1,
          },
        },
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>
      <CopilotProvider>{children}</CopilotProvider>
    </QueryClientProvider>
  );
}
