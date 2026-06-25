"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { useSearchProducts, searchProductsPath } from "@/lib/search";
import { ProductCard } from "@/components/product-card";
import { FacetSidebar } from "@/components/facet-sidebar";
import { SortBar } from "@/components/sort-bar";
import { ErrorState } from "@/components/error-state";
import { EmptyState } from "@/components/empty-state";
import { useQueryClient } from "@tanstack/react-query";
import type { SearchParams, SortKey } from "@/lib/types";

function paramsFromSearchParams(sp: URLSearchParams): SearchParams {
  const get = (k: string) => sp.get(k) || undefined;
  const num = (k: string) => {
    const v = sp.get(k);
    if (!v) return undefined;
    const n = Number(v);
    return Number.isFinite(n) ? n : undefined;
  };
  return {
    q: get("q"),
    page: num("page"),
    limit: num("limit"),
    sort: (get("sort") as SortKey) || undefined,
    brand: get("brand"),
    category: get("category"),
    price_min: num("price_min"),
    price_max: num("price_max"),
    ram_gb_min: num("ram_gb_min"),
    ram_gb_max: num("ram_gb_max"),
    storage_gb_min: num("storage_gb_min"),
    storage_gb_max: num("storage_gb_max"),
  };
}

export default function SearchPage() {
  return (
    <Suspense fallback={<div className="text-sm text-gray-500">Đang tải…</div>}>
      <SearchView />
    </Suspense>
  );
}

function SearchView() {
  const searchParams = useSearchParams();
  const params = paramsFromSearchParams(searchParams ?? new URLSearchParams());
  const queryClient = useQueryClient();
  const { data, error, isPending, refetch } = useSearchProducts(params);

  return (
    <div className="grid gap-6 lg:grid-cols-[280px_1fr]">
      {data && <FacetSidebar data={data} className="lg:sticky lg:top-4" />}
      <section aria-label="Kết quả tìm kiếm">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm text-gray-500" aria-live="polite">
            {isPending
              ? "Đang tải…"
              : data
                ? `${data.pagination.total_hits} sản phẩm`
                : ""}
          </p>
          <SortBar />
        </div>
        {error && <ErrorState error={error} retry={refetch} />}
        {data && data.hits.length === 0 && (
          <EmptyState
            title="Không tìm thấy sản phẩm phù hợp"
            description="Thử thay đổi bộ lọc hoặc từ khoá khác."
          />
        )}
        {data && data.hits.length > 0 && (
          <>
            <ul className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {data.hits.map((p) => (
                <li key={p.id}>
                  <ProductCard product={p} />
                </li>
              ))}
            </ul>
            {data.pagination.total_pages > 1 && (
              <nav
                className="mt-6 flex items-center justify-center gap-2"
                aria-label="Phân trang"
              >
                {Array.from({ length: data.pagination.total_pages }).map(
                  (_, i) => {
                    const page = i + 1;
                    const usp = new URLSearchParams(
                      searchParams?.toString() || "",
                    );
                    usp.set("page", String(page));
                    return (
                      <a
                        key={page}
                        href={`/search?${usp.toString()}`}
                        className={
                          "btn-outline h-8 px-3 text-xs" +
                          (data.pagination.page === page
                            ? " border-primary text-primary"
                            : "")
                        }
                      >
                        {page}
                      </a>
                    );
                  },
                )}
              </nav>
            )}
          </>
        )}
      </section>
    </div>
  );
}
