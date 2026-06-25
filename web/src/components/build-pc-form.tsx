"use client";

import { useState } from "react";
import { useBuildPc } from "@/lib/build-pc";
import { useBuildDraftStore } from "@/store/buildDraftStore";
import { BuildPcResult } from "./build-pc-result";
import { ErrorState } from "./error-state";
import type {
  BuildRequirements,
  CpuPreference,
  GpuPreference,
  Priority,
  UseCase,
} from "@/lib/types";

const USE_CASES: { value: UseCase; label: string }[] = [
  { value: "gaming", label: "Gaming" },
  { value: "office", label: "Văn phòng" },
  { value: "video_editing", label: "Dựng video" },
  { value: "3d_render", label: "Render 3D" },
  { value: "general", label: "Đa dụng" },
];

const CPU: { value: CpuPreference; label: string }[] = [
  { value: "any", label: "Không yêu cầu" },
  { value: "intel", label: "Intel" },
  { value: "amd", label: "AMD" },
];
const GPU: { value: GpuPreference; label: string }[] = [
  { value: "any", label: "Không yêu cầu" },
  { value: "nvidia", label: "NVIDIA" },
  { value: "amd", label: "AMD" },
];
const PRIORITIES: { value: Priority; label: string }[] = [
  { value: "balanced", label: "Cân bằng" },
  { value: "performance", label: "Hiệu năng" },
  { value: "budget", label: "Tiết kiệm" },
];

const RAM_OPTIONS = [8, 16, 32, 64];

export function BuildPcForm() {
  const draft = useBuildDraftStore((s) => s.draft);
  const setDraft = useBuildDraftStore((s) => s.setDraft);
  const setLastResult = useBuildDraftStore((s) => s.setLastResult);
  const lastResult = useBuildDraftStore((s) => s.lastResult);
  const mutation = useBuildPc();
  const [validationError, setValidationError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setValidationError(null);
    if (!draft.budget_vnd || draft.budget_vnd <= 0) {
      setValidationError("Vui lòng nhập ngân sách (> 0).");
      return;
    }
    if (!draft.use_case) {
      setValidationError("Vui lòng chọn mục đích sử dụng.");
      return;
    }
    const req: BuildRequirements = {
      use_case: draft.use_case,
      budget_vnd: draft.budget_vnd,
      cpu_preference: draft.cpu_preference || "any",
      gpu_preference: draft.gpu_preference || "any",
      ram_min_gb: draft.ram_min_gb,
      priority: draft.priority || "balanced",
    };
    try {
      const result = await mutation.mutateAsync(req);
      setLastResult(result);
    } catch {
      // Error already in mutation.error
    }
  };

  return (
    <form
      onSubmit={submit}
      className="flex flex-col gap-4"
      aria-label="Form build PC"
    >
      <div className="grid gap-4 md:grid-cols-2">
        <label className="flex flex-col gap-1 text-sm">
          <span>Mục đích</span>
          <select
            className="input"
            aria-label="Mục đích sử dụng"
            value={draft.use_case || ""}
            onChange={(e) =>
              setDraft({ use_case: (e.target.value || undefined) as UseCase })
            }
          >
            <option value="">-- Chọn --</option>
            {USE_CASES.map((u) => (
              <option key={u.value} value={u.value}>
                {u.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span>Ngân sách (VND) *</span>
          <input
            type="number"
            inputMode="numeric"
            min={1}
            className="input"
            value={draft.budget_vnd ?? ""}
            onChange={(e) =>
              setDraft({
                budget_vnd: e.target.value ? Number(e.target.value) : undefined,
              })
            }
            required
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span>CPU</span>
          <select
            className="input"
            aria-label="Ưu tiên CPU"
            value={draft.cpu_preference || "any"}
            onChange={(e) =>
              setDraft({
                cpu_preference: (e.target.value as CpuPreference) || "any",
              })
            }
          >
            {CPU.map((c) => (
              <option key={c.value} value={c.value}>
                {c.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span>GPU</span>
          <select
            className="input"
            aria-label="Ưu tiên GPU"
            value={draft.gpu_preference || "any"}
            onChange={(e) =>
              setDraft({
                gpu_preference: (e.target.value as GpuPreference) || "any",
              })
            }
          >
            {GPU.map((g) => (
              <option key={g.value} value={g.value}>
                {g.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span>RAM tối thiểu (GB)</span>
          <select
            className="input"
            aria-label="RAM tối thiểu"
            value={draft.ram_min_gb ?? ""}
            onChange={(e) =>
              setDraft({
                ram_min_gb: e.target.value ? Number(e.target.value) : undefined,
              })
            }
          >
            <option value="">Không yêu cầu</option>
            {RAM_OPTIONS.map((gb) => (
              <option key={gb} value={gb}>
                ≥ {gb} GB
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span>Ưu tiên</span>
          <select
            className="input"
            aria-label="Mức ưu tiên"
            value={draft.priority || "balanced"}
            onChange={(e) =>
              setDraft({
                priority: (e.target.value as Priority) || "balanced",
              })
            }
          >
            {PRIORITIES.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
        </label>
      </div>
      {validationError && (
        <p className="text-sm text-red-600" role="alert">
          {validationError}
        </p>
      )}
      <div>
        <button
          type="submit"
          className="btn-cta"
          disabled={mutation.isPending}
        >
          {mutation.isPending ? "Đang build..." : "Build PC"}
        </button>
      </div>
      {mutation.isError && <ErrorState error={mutation.error} />}
      {lastResult && <BuildPcResult build={lastResult} />}
    </form>
  );
}
