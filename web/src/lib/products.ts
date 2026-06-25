import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "./api";
import type { ProductDetail, RelatedProduct, CategoryEntry } from "./types";

export function getProduct(slug: string) {
  return apiFetch<ProductDetail>(`/api/products/${encodeURIComponent(slug)}`);
}

export function getRelated(slug: string, limit = 8) {
  return apiFetch<RelatedProduct[]>(`/api/products/${encodeURIComponent(slug)}/related?limit=${limit}`);
}

export function useProduct(slug: string) {
  return useQuery({
    queryKey: ["product", slug],
    queryFn: () => getProduct(slug),
  });
}

export function useRelated(slug: string, limit = 8) {
  return useQuery({
    queryKey: ["related", slug, limit],
    queryFn: () => getRelated(slug, limit),
  });
}

export function getCategories() {
  return apiFetch<CategoryEntry[]>("/api/categories");
}

export function useCategories() {
  return useQuery({
    queryKey: ["categories"],
    queryFn: getCategories,
    staleTime: 5 * 60_000,
  });
}
