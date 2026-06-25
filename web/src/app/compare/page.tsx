"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { useSearchParams, useRouter, usePathname } from "next/navigation";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import Image from "next/image";
import { Trash2, X } from "lucide-react";
import { getProduct } from "@/lib/products";
import { PriceTag } from "@/components/price-tag";
import { ErrorState } from "@/components/error-state";
import type { ProductDetail, SpecItem } from "@/lib/types";

const MAX = 4;

function parseSlugs(s: string | null): string[] {
  if (!s) return [];
  return s
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean)
    .slice(0, MAX);
}

export default function ComparePage() {
  return (
    <Suspense fallback={<div className="text-sm text-gray-500">Đang tải…</div>}>
      <CompareView />
    </Suspense>
  );
}

function CompareView() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const slugs = useMemo(
    () => parseSlugs(searchParams?.get("slugs") ?? null),
    [searchParams],
  );
  const [toast, setToast] = useState<string | null>(null);

  // Hydrate from URL: if more than MAX, keep first MAX and notify.
  useEffect(() => {
    const raw = (searchParams?.get("slugs") ?? "")
      .split(",")
      .map((x) => x.trim())
      .filter(Boolean);
    if (raw.length > MAX) {
      const trimmed = raw.slice(0, MAX);
      const usp = new URLSearchParams(searchParams?.toString() || "");
      usp.set("slugs", trimmed.join(","));
      router.replace(`${pathname}?${usp.toString()}`);
      setToast(`Đã giữ ${MAX} sản phẩm đầu tiên.`);
    }
  }, [searchParams, router, pathname]);

  // Fetch products in parallel.
  const queries = slugs.map((slug) => ({
    queryKey: ["product", slug],
    queryFn: () => getProduct(slug),
  }));
  const results = useQuery({
    queryKey: ["compare-batch", slugs],
    queryFn: async () => {
      const list = await Promise.all(queries.map((q) => q.queryFn()));
      return list;
    },
    enabled: slugs.length > 0,
  });

  const products = results.data || [];
  const isLoading = results.isPending;

  const removeSlug = (slug: string) => {
    const usp = new URLSearchParams(searchParams?.toString() || "");
    const next = slugs.filter((s) => s !== slug);
    if (next.length === 0) usp.delete("slugs");
    else usp.set("slugs", next.join(","));
    router.replace(`${pathname}?${usp.toString()}`);
  };

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="font-heading text-2xl font-bold">So sánh sản phẩm</h1>
        {slugs.length > 0 && (
          <span className="badge-violet">{slugs.length} / {MAX}</span>
        )}
      </div>
      {toast && (
        <div className="card bg-amber-50 text-amber-800" role="status">
          {toast}
          <button
            type="button"
            onClick={() => setToast(null)}
            className="ml-2"
            aria-label="Đóng"
          >
            <X className="inline h-3 w-3" />
          </button>
        </div>
      )}
      {results.isError && <ErrorState error={results.error} retry={results.refetch} />}
      {slugs.length === 0 && (
        <div className="card text-center text-sm text-gray-500">
          <p>Chưa có sản phẩm nào để so sánh.</p>
          <Link href="/search" className="btn-primary mt-3 inline-flex">
            Đi tới tìm kiếm
          </Link>
        </div>
      )}
      {slugs.length > 0 && (
        <div className="card overflow-x-auto">
          <table className="min-w-full">
            <thead>
              <tr>
                <th className="w-40 text-left text-xs uppercase text-gray-500">Tiêu chí</th>
                {products.map((p, i) => (
                  <th
                    key={p?.id ?? `loading-${i}`}
                    className="min-w-[180px] px-3 py-2 text-left"
                  >
                    {p ? (
                      <div className="flex flex-col gap-1">
                        <div className="flex items-center gap-2">
                          {p.images[0] && (
                            <Image
                              src={p.images[0]}
                              alt={p.name}
                              width={64}
                              height={48}
                              className="h-12 w-16 rounded object-contain"
                            />
                          )}
                          <Link
                            href={`/products/${p.slug}`}
                            className="line-clamp-2 text-sm font-medium hover:underline"
                          >
                            {p.name}
                          </Link>
                          <button
                            type="button"
                            onClick={() => removeSlug(p.slug)}
                            className="ml-auto"
                            aria-label={`Bỏ ${p.name}`}
                          >
                            <Trash2 className="h-3 w-3" />
                          </button>
                        </div>
                        <PriceTag
                          current={p.current_price?.price_vnd}
                          list={p.current_price?.list_price_vnd ?? undefined}
                        />
                      </div>
                    ) : isLoading ? (
                      <div className="h-12 animate-pulse rounded bg-gray-100" />
                    ) : null}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {isLoading && products.length === 0 ? (
                <tr>
                  <td colSpan={slugs.length + 1} className="py-6 text-center text-sm text-gray-500">
                    Đang tải…
                  </td>
                </tr>
              ) : (
                <SpecRows products={products} />
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function SpecRows({ products }: { products: ProductDetail[] }) {
  // Union of all spec labels.
  const labels = Array.from(
    new Set(
      products.flatMap((p) =>
        Object.values(p.specs_grouped || {}).flatMap((rows: SpecItem[]) =>
          rows.map((r) => r.label),
        ),
      ),
    ),
  );
  if (labels.length === 0) {
    return (
      <tr>
        <td colSpan={products.length + 1} className="py-6 text-center text-sm text-gray-500">
          Không có thông số để so sánh.
        </td>
      </tr>
    );
  }
  return (
    <>
      {labels.map((label) => (
        <tr key={label} className="border-t border-black/5">
          <th className="px-3 py-2 text-left text-xs font-medium text-gray-500">
            {label}
          </th>
          {products.map((p, i) => {
            const value = Object.values(p.specs_grouped || {})
              .flatMap((r) => r)
              .find((r) => r.label === label)?.value;
            return (
              <td key={p?.id ?? i} className="px-3 py-2 text-sm">
                {value || "—"}
              </td>
            );
          })}
        </tr>
      ))}
    </>
  );
}
