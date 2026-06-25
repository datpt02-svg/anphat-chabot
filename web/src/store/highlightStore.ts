"use client";

import { create } from "zustand";

interface HighlightState {
  productId: string | null;
  set: (productId: string) => void;
  clear: () => void;
}

export const useHighlightStore = create<HighlightState>((set) => ({
  productId: null,
  set: (productId) => set({ productId }),
  clear: () => set({ productId: null }),
}));
