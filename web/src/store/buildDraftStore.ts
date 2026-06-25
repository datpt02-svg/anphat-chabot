"use client";

import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { BuildRequirements, PCBuild } from "@/lib/types";

interface BuildDraftState {
  draft: Partial<BuildRequirements>;
  lastResult: PCBuild | null;
  lastFilters: Partial<BuildRequirements> | null;
  setDraft: (patch: Partial<BuildRequirements>) => void;
  clearDraft: () => void;
  setLastResult: (result: PCBuild) => void;
  setLastFilters: (filters: Partial<BuildRequirements>) => void;
  clearAll: () => void;
}

const EMPTY: Partial<BuildRequirements> = {};

export const useBuildDraftStore = create<BuildDraftState>()(
  persist(
    (set) => ({
      draft: { ...EMPTY },
      lastResult: null,
      lastFilters: null,
      setDraft: (patch) => set((s) => ({ draft: { ...s.draft, ...patch } })),
      clearDraft: () => set({ draft: { ...EMPTY } }),
      setLastResult: (result) => set({ lastResult: result }),
      setLastFilters: (filters) => set({ lastFilters: filters }),
      clearAll: () => set({ draft: { ...EMPTY }, lastResult: null, lastFilters: null }),
    }),
    { name: "anphat-build-draft" },
  ),
);
