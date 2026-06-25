"use client";

import { useRouter, useSearchParams, usePathname } from "next/navigation";
import type { SortKey } from "@/lib/types";

const OPTIONS: { value: SortKey; label: string }[] = [
  { value: "relevance", label: "Liên quan" },
  { value: "price_asc", label: "Giá tăng dần" },
  { value: "price_desc", label: "Giá giảm dần" },
  { value: "newest", label: "Mới nhất" },
  { value: "name_asc", label: "Tên A-Z" },
];

export function SortBar({ className }: { className?: string }) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const current = (searchParams?.get("sort") as SortKey) || "relevance";
  return (
    <label className={`flex items-center gap-2 text-sm ${className || ""}`}>
      <span className="text-gray-500">Sắp xếp:</span>
      <select
        className="input h-8 w-auto"
        value={current}
        onChange={(e) => {
          const usp = new URLSearchParams(searchParams?.toString() || "");
          if (e.target.value === "relevance") usp.delete("sort");
          else usp.set("sort", e.target.value);
          usp.delete("page");
          const qs = usp.toString();
          const target = `/search${qs ? "?" + qs : ""}`;
          if (pathname === "/search") router.replace(target);
          else router.push(target);
        }}
        aria-label="Sắp xếp"
      >
        {OPTIONS.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}
