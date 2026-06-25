import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "./api";
import type { SearchParams, SearchResponse } from "./types";

export function searchProductsPath(params: SearchParams): string {
  const usp = new URLSearchParams();
  if (params.q) usp.set("q", params.q);
  if (params.page) usp.set("page", String(params.page));
  if (params.limit) usp.set("limit", String(params.limit));
  if (params.sort) usp.set("sort", params.sort);
  if (params.brand) usp.set("brand", params.brand);
  if (params.category) usp.set("category", params.category);
  if (params.price_min != null) usp.set("price_min", String(params.price_min));
  if (params.price_max != null) usp.set("price_max", String(params.price_max));
  if (params.ram_gb_min != null) usp.set("ram_gb_min", String(params.ram_gb_min));
  if (params.ram_gb_max != null) usp.set("ram_gb_max", String(params.ram_gb_max));
  if (params.storage_gb_min != null) usp.set("storage_gb_min", String(params.storage_gb_min));
  if (params.storage_gb_max != null) usp.set("storage_gb_max", String(params.storage_gb_max));
  const qs = usp.toString();
  return `/api/search${qs ? "?" + qs : ""}`;
}

export function searchProducts(params: SearchParams = {}) {
  return apiFetch<SearchResponse>(searchProductsPath(params));
}

export function useSearchProducts(params: SearchParams = {}) {
  return useQuery({
    queryKey: ["search", params],
    queryFn: () => searchProducts(params),
  });
}
