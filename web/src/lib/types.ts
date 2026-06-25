// Types aligned with M4 Pydantic schemas in [api/schemas.py](api/schemas.py)
// and M5b PCBuild in [agents/compat/schemas.py](agents/compat/schemas.py).

export type SortKey = "relevance" | "price_asc" | "price_desc" | "newest" | "name_asc";

export type StockStatus = "in_stock" | "out_of_stock" | "preorder" | "unknown" | "contact";

export interface SearchHit {
  id: string;
  slug: string;
  name: string;
  brand: string | null;
  category: string;
  thumbnail_url: string | null;
  price_vnd: number | null;
  list_price_vnd: number | null;
  sale_price_vnd: number | null;
  stock_status: StockStatus;
  spec_summary: string | null;
}

export interface Pagination {
  page: number;
  limit: number;
  total_hits: number;
  total_pages: number;
}

export type SearchSource = "meilisearch" | "postgres";

export interface SearchResponse {
  query: string;
  source: SearchSource;
  fallback: boolean;
  hits: SearchHit[];
  facets: Record<string, Record<string, number>>;
  pagination: Pagination;
  processing_time_ms: number | null;
}

export interface SearchParams {
  q?: string;
  page?: number;
  limit?: number;
  sort?: SortKey;
  brand?: string;
  category?: string;
  price_min?: number;
  price_max?: number;
  ram_gb_min?: number;
  ram_gb_max?: number;
  storage_gb_min?: number;
  storage_gb_max?: number;
}

export interface CurrentPrice {
  price_vnd: number | null;
  list_price_vnd: number | null;
  stock_status: StockStatus;
  captured_at: string;
}

export interface SpecItem {
  label: string;
  value: string;
}

export interface SpecsSummary {
  cpu_model?: string | null;
  ram_gb?: number | null;
  storage_gb?: number | null;
  gpu_model?: string | null;
  [k: string]: unknown;
}

export interface ProductDetail {
  id: string;
  slug: string;
  source: string;
  source_url: string;
  sku: string | null;
  name: string;
  brand: string | null;
  category: string;
  breadcrumbs: string[];
  images: string[];
  description: string | null;
  current_price: CurrentPrice | null;
  specs_summary: SpecsSummary | null;
  specs_grouped: Record<string, SpecItem[]>;
  updated_at: string;
}

export interface RelatedProduct {
  id: string;
  slug: string;
  name: string;
  category: string;
  thumbnail_url: string | null;
  price_vnd: number | null;
}

export interface CategoryEntry {
  name: string;
  count: number;
}

export type UseCase = "gaming" | "office" | "video_editing" | "3d_render" | "general";
export type CpuPreference = "intel" | "amd" | "any";
export type GpuPreference = "nvidia" | "amd" | "any";
export type Priority = "performance" | "balanced" | "budget";

export interface BuildRequirements {
  use_case: UseCase;
  budget_vnd: number;
  cpu_preference?: CpuPreference;
  gpu_preference?: GpuPreference;
  ram_min_gb?: number;
  priority?: Priority;
  include_overclock?: boolean;
  pinned?: Record<string, string>;
}

export type PccCategory = "cpu" | "mobo" | "ram" | "gpu" | "storage" | "psu" | "case" | "cooler";

export interface PCComponent {
  category: PccCategory;
  product_id: string;
  name: string;
  price_vnd: number;
  url: string;
  pinned: boolean;
}

export interface CompatibilityIssue {
  pair: [string, string];
  rule: string;
  detail: string;
  severity: "error" | "warning";
}

export interface CompatibilityResult {
  compatible: boolean;
  issues: CompatibilityIssue[];
  warnings: CompatibilityIssue[];
  psu_wattage_required: number;
  psu_wattage_recommended: number;
  total_price_vnd: number;
}

export interface PCBuild {
  build: PCComponent[];
  total_price_vnd: number;
  compatibility: CompatibilityResult;
  reasoning: string;
  alternatives: PCBuild[];
}

export interface ApiError {
  error: string;
  code: string;
  details?: Record<string, unknown>;
}
