"use client";

import { useRouter, useSearchParams, usePathname } from "next/navigation";
import type { SearchResponse, SortKey } from "@/lib/types";
import { cn } from "@/lib/utils";

type Params = {
  searchParams: URLSearchParams;
  pathname: string;
  router: ReturnType<typeof useRouter>;
};

function updateParam(p: Params, key: string, value: string | null) {
  const usp = new URLSearchParams(p.searchParams.toString());
  if (value == null || value === "") usp.delete(key);
  else usp.set(key, value);
  usp.delete("page");
  return usp;
}

function navigate(p: Params, usp: URLSearchParams) {
  const qs = usp.toString();
  const target = `/search${qs ? "?" + qs : ""}`;
  if (p.pathname === "/search") p.router.replace(target);
  else p.router.push(target);
}

export function FacetSidebar({
  data,
  className,
}: {
  data: SearchResponse;
  className?: string;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const p: Params = { searchParams: searchParams ?? new URLSearchParams(), pathname, router };
  const brands = data.facets?.brand || {};
  const categories = data.facets?.category || {};
  const currentBrand = p.searchParams.get("brand") || "";
  const currentCategory = p.searchParams.get("category") || "";
  const currentPriceMin = p.searchParams.get("price_min") || "";
  const currentPriceMax = p.searchParams.get("price_max") || "";
  const currentRam = p.searchParams.get("ram_gb_min") || "";

  return (
    <aside
      className={cn("card flex flex-col gap-6", className)}
      aria-label="Bộ lọc"
    >
      <div>
        <h3 className="mb-2 text-sm font-semibold">Danh mục</h3>
        <select
          className="input"
          aria-label="Danh mục"
          value={currentCategory}
          onChange={(e) => {
            const usp = updateParam(p, "category", e.target.value || null);
            navigate(p, usp);
          }}
        >
          <option value="">Tất cả</option>
          {Object.entries(categories)
            .sort((a, b) => b[1] - a[1])
            .map(([name, count]) => (
              <option key={name} value={name}>
                {name} ({count})
              </option>
            ))}
        </select>
      </div>
      <div>
        <h3 className="mb-2 text-sm font-semibold">Thương hiệu</h3>
        <select
          className="input"
          aria-label="Thương hiệu"
          value={currentBrand}
          onChange={(e) => {
            const usp = updateParam(p, "brand", e.target.value || null);
            navigate(p, usp);
          }}
        >
          <option value="">Tất cả</option>
          {Object.entries(brands)
            .sort((a, b) => b[1] - a[1])
            .map(([name, count]) => (
              <option key={name} value={name}>
                {name} ({count})
              </option>
            ))}
        </select>
      </div>
      <div>
        <h3 className="mb-2 text-sm font-semibold">Giá (VND)</h3>
        <div className="flex gap-2">
          <input
            type="number"
            inputMode="numeric"
            placeholder="Từ"
            value={currentPriceMin}
            onChange={(e) => {
              const usp = updateParam(p, "price_min", e.target.value || null);
              navigate(p, usp);
            }}
            className="input"
            aria-label="Giá tối thiểu"
          />
          <input
            type="number"
            inputMode="numeric"
            placeholder="Đến"
            value={currentPriceMax}
            onChange={(e) => {
              const usp = updateParam(p, "price_max", e.target.value || null);
              navigate(p, usp);
            }}
            className="input"
            aria-label="Giá tối đa"
          />
        </div>
      </div>
      <div>
        <h3 className="mb-2 text-sm font-semibold">RAM tối thiểu (GB)</h3>
        <select
          className="input"
          aria-label="RAM tối thiểu"
          value={currentRam}
          onChange={(e) => {
            const usp = updateParam(p, "ram_gb_min", e.target.value || null);
            navigate(p, usp);
          }}
        >
          <option value="">Không yêu cầu</option>
          {[4, 8, 16, 32, 64].map((gb) => (
            <option key={gb} value={gb}>
              ≥ {gb} GB
            </option>
          ))}
        </select>
      </div>
      <button
        type="button"
        className="btn-outline"
        onClick={() => {
          const usp = new URLSearchParams(p.searchParams.toString());
          ["brand", "category", "price_min", "price_max", "ram_gb_min"].forEach((k) =>
            usp.delete(k),
          );
          usp.delete("page");
          navigate(p, usp);
        }}
      >
        Xoá bộ lọc
      </button>
      <p className="text-xs text-gray-500" aria-live="polite">
        {data.pagination.total_hits} sản phẩm
      </p>
    </aside>
  );
}
