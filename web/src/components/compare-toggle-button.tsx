"use client";

import { Check, Plus } from "lucide-react";
import { useCompareStore } from "@/store/compareStore";

export function CompareToggleButton({ slug }: { slug: string }) {
  const inCompare = useCompareStore((s) => s.slugs.includes(slug));
  const isFull = useCompareStore((s) => s.slugs.length >= 4 && !inCompare);
  const compareAdd = useCompareStore((s) => s.add);
  const compareRemove = useCompareStore((s) => s.remove);
  return (
    <button
      type="button"
      className="btn-outline"
      onClick={() => (inCompare ? compareRemove(slug) : compareAdd(slug))}
      disabled={isFull}
      aria-label={inCompare ? "Bỏ khỏi so sánh" : "Thêm vào so sánh"}
    >
      {inCompare ? (
        <>
          <Check className="mr-1 h-4 w-4" aria-hidden /> Đã so sánh
        </>
      ) : (
        <>
          <Plus className="mr-1 h-4 w-4" aria-hidden /> Thêm vào so sánh
        </>
      )}
    </button>
  );
}
