import { useMutation } from "@tanstack/react-query";
import { apiFetch } from "./api";
import type { BuildRequirements, PCBuild } from "./types";

export function buildPc(req: BuildRequirements) {
  return apiFetch<PCBuild>("/api/build_pc", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
}

export function useBuildPc() {
  return useMutation({
    mutationFn: (req: BuildRequirements) => buildPc(req),
  });
}
