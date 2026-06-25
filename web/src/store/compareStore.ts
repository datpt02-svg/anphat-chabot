"use client";

import { create } from "zustand";
import { persist } from "zustand/middleware";

const MAX_COMPARE = 4;

interface CompareState {
  slugs: string[];
  add: (slug: string) => void;
  remove: (slug: string) => void;
  clear: () => void;
  has: (slug: string) => boolean;
  isFull: () => boolean;
}

export const useCompareStore = create<CompareState>()(
  persist(
    (set, get) => ({
      slugs: [],
      add: (slug) =>
        set((state) => {
          if (state.slugs.includes(slug)) return state;
          if (state.slugs.length >= MAX_COMPARE) {
            const [, ...rest] = state.slugs;
            return { slugs: [...rest, slug] };
          }
          return { slugs: [...state.slugs, slug] };
        }),
      remove: (slug) =>
        set((state) => ({ slugs: state.slugs.filter((s) => s !== slug) })),
      clear: () => set({ slugs: [] }),
      has: (slug) => get().slugs.includes(slug),
      isFull: () => get().slugs.length >= MAX_COMPARE,
    }),
    { name: "anphat-compare" },
  ),
);

export const MAX_COMPARE_ITEMS = MAX_COMPARE;
