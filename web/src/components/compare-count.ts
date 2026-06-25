"use client";

import { useEffect } from "react";
import { useCompareStore } from "@/store/compareStore";

// SSR/CSR hydration: useCompareStore is persisted; on first client render
// rehydrate from localStorage. This component does nothing visible.
export function useCompareCount() {
  useEffect(() => {
    useCompareStore.persist?.rehydrate?.();
  }, []);
}
