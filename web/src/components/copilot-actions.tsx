"use client";

import { useEffect } from "react";
import { useRouter, usePathname } from "next/navigation";
import {
  useCopilotAction,
  renderActionLikeElements,
  type useCopilotActionRenderProps,
} from "@copilotkit/react-core";
import { useCompareStore } from "@/store/compareStore";
import { useHighlightStore } from "@/store/highlightStore";
import { useBuildDraftStore } from "@/store/buildDraftStore";
import type { BuildRequirements, SortKey } from "@/lib/types";

const MAX_COMPARE = 4;

type SearchFilters = {
  brand?: string;
  category?: string;
  price_min?: number;
  price_max?: number;
  ram_gb_min?: number;
  sort?: SortKey;
};

type LaptopSuggestion = {
  title?: string;
  slug?: string;
  price_text?: string;
  stock_text?: string;
  cpu_model?: string | null;
  ram_gb?: number | null;
  storage_gb?: number | null;
  gpu_model?: string | null;
  screen_inches?: number | null;
  slug_value?: string;
  url?: string;
};

type RenderLaptopArgs = {
  intro?: string;
  products?: LaptopSuggestion[];
};

// Read current URL query at call time (no useSearchParams → no Suspense required).
function readCurrentQuery(): URLSearchParams {
  if (typeof window === "undefined") return new URLSearchParams();
  return new URLSearchParams(window.location.search);
}

function formatVnd(price?: number | null) {
  if (price == null) return "Liên hệ";
  return `${(price || 0).toLocaleString("vi-VN")} VND`;
}

function LaptopCard({ product }: { product: LaptopSuggestion }) {
  const slug = product.slug || product.slug_value || "";
  const href = product.url || (slug ? `https://anphatpc.com.vn/${slug}.html` : "#");
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="block rounded-2xl border border-violet-100 bg-white p-4 shadow-sm transition hover:-translate-y-0.5 hover:shadow-md"
    >
      <div className="text-sm font-semibold text-slate-900 line-clamp-2 min-h-[2.5rem]">
        {product.title || "Laptop"}
      </div>
      <div className="mt-2 text-base font-bold text-violet-700">
        {product.price_text || "Liên hệ"}
      </div>
      <div className="mt-1 text-xs text-slate-500">
        {product.stock_text || "Tình trạng chưa rõ"}
      </div>
      <ul className="mt-2 space-y-0.5 text-xs text-slate-600">
        {product.cpu_model ? <li>CPU: {product.cpu_model}</li> : null}
        {product.ram_gb ? <li>RAM: {product.ram_gb}GB</li> : null}
        {product.storage_gb ? <li>SSD: {product.storage_gb}GB</li> : null}
        {product.gpu_model ? <li>GPU: {product.gpu_model}</li> : null}
        {product.screen_inches ? <li>Màn hình: {product.screen_inches} inch</li> : null}
      </ul>
    </a>
  );
}

function LaptopSuggestionList({ args }: { args: RenderLaptopArgs }) {
  const products = args.products || [];
  if (products.length === 0) return null;
  return (
    <div className="space-y-3">
      {args.intro ? (
        <p className="text-sm text-slate-700 whitespace-pre-wrap">{args.intro}</p>
      ) : null}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {products.slice(0, 6).map((p, idx) => (
          <LaptopCard key={idx} product={p} />
        ))}
      </div>
    </div>
  );
}

export function CopilotActionsBridge() {
  const router = useRouter();
  const pathname = usePathname();
  const compareAdd = useCompareStore((s) => s.add);
  const compareRemove = useCompareStore((s) => s.remove);
  const compareSlugs = useCompareStore((s) => s.slugs);
  const highlightSet = useHighlightStore((s) => s.set);
  const buildDraft = useBuildDraftStore();

  useCopilotAction({
    name: "renderLaptopSuggestions",
    description: "Render a list of laptop suggestion cards in the chat panel",
    parameters: [
      { name: "intro", type: "string", required: false, description: "Lead-in text" },
      {
        name: "products",
        type: "object[]",
        required: true,
        description: "List of laptop card payloads",
      },
    ],
    render: ({ args }: useCopilotActionRenderProps<RenderLaptopArgs>) => (
      <LaptopSuggestionList args={args as RenderLaptopArgs} />
    ),
  });

  useCopilotAction({
    name: "navigateToSearch",
    description: "Navigate to the search/PLP page with a query",
    parameters: [{ name: "query", type: "string", description: "Search query" }],
    handler: ({ query }) => {
      const params = new URLSearchParams();
      if (query) params.set("q", query);
      router.push(`/search?${params.toString()}`);
    },
  });

  useCopilotAction({
    name: "openProduct",
    description: "Navigate to a product detail page by slug",
    parameters: [{ name: "slug", type: "string" }],
    handler: ({ slug }) => {
      router.push(`/products/${encodeURIComponent(slug)}`);
    },
  });

  useCopilotAction({
    name: "addToCompare",
    description: `Add a product to the compare list (max ${MAX_COMPARE})`,
    parameters: [{ name: "slug", type: "string" }],
    handler: ({ slug }) => {
      if (compareSlugs.includes(slug)) {
        return "Đã có trong danh sách so sánh.";
      }
      if (compareSlugs.length >= MAX_COMPARE) {
        const oldest = compareSlugs[0];
        compareRemove(oldest);
        compareAdd(slug);
        return `Compare đầy (${MAX_COMPARE}/${MAX_COMPARE}). Đã thay sản phẩm cũ: ${oldest}.`;
      }
      compareAdd(slug);
      return `Đã thêm vào so sánh. (${compareSlugs.length + 1}/${MAX_COMPARE})`;
    },
  });

  useCopilotAction({
    name: "highlightProduct",
    description: "Highlight and scroll to a product on the current page",
    parameters: [{ name: "productId", type: "string" }],
    handler: ({ productId }) => {
      highlightSet(productId);
      if (typeof document !== "undefined") {
        const el = document.getElementById(productId);
        if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    },
  });

  useCopilotAction({
    name: "setSearchFilters",
    description: "Update search filters and navigate to PLP",
    parameters: [
      { name: "brand", type: "string", required: false },
      { name: "category", type: "string", required: false },
      { name: "price_min", type: "number", required: false },
      { name: "price_max", type: "number", required: false },
      { name: "ram_gb_min", type: "number", required: false },
      { name: "sort", type: "string", required: false },
    ],
    handler: (args) => {
      const allowed: (keyof SearchFilters)[] = [
        "brand",
        "category",
        "price_min",
        "price_max",
        "ram_gb_min",
        "sort",
      ];
      const params = readCurrentQuery();
      allowed.forEach((k) => params.delete(k));
      for (const k of allowed) {
        const v = (args as Record<string, unknown>)[k];
        if (v != null && v !== "") params.set(k, String(v));
      }
      params.delete("page");
      const qs = params.toString();
      const target = `/search${qs ? "?" + qs : ""}`;
      if (pathname !== "/search") {
        router.push(target);
      } else {
        router.replace(target);
      }
      buildDraft.setLastFilters(args as Partial<BuildRequirements>);
    },
  });

  useEffect(() => {
    // Side-effect: nothing to mount here; actions self-register.
    void renderActionLikeElements;
  }, []);

  return null;
}
