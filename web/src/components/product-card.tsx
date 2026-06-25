"use client";

import Image from "next/image";
import Link from "next/link";
import { Check, Plus } from "lucide-react";
import { useCompareStore } from "@/store/compareStore";
import { useHighlightStore } from "@/store/highlightStore";
import { PriceTag } from "./price-tag";
import { StockBadge } from "./stock-badge";
import type { SearchHit } from "@/lib/types";
import { cn } from "@/lib/utils";

export function ProductCard({
  product,
  className,
}: {
  product: SearchHit;
  className?: string;
}) {
  const inCompare = useCompareStore((s) => s.slugs.includes(product.slug));
  const isFull = useCompareStore((s) => s.slugs.length >= 4);
  const compareAdd = useCompareStore((s) => s.add);
  const compareRemove = useCompareStore((s) => s.remove);
  const highlightSet = useHighlightStore((s) => s.set);

  return (
    <article
      id={product.id}
      className={cn("card flex flex-col gap-3", className)}
      aria-label={product.name}
    >
      <Link
        href={`/products/${product.slug}`}
        className="block overflow-hidden rounded-xl bg-gray-50"
      >
        {product.thumbnail_url ? (
          <Image
            src={product.thumbnail_url}
            alt={product.name}
            width={320}
            height={240}
            className="aspect-[4/3] w-full object-contain"
          />
        ) : (
          <div
            className="flex aspect-[4/3] w-full items-center justify-center bg-gray-100 text-sm text-gray-500"
            aria-label="No image"
          >
            Không có ảnh
          </div>
        )}
      </Link>
      <div className="flex flex-1 flex-col gap-2">
        <Link
          href={`/products/${product.slug}`}
          className="line-clamp-2 text-sm font-medium hover:underline"
        >
          {product.name}
        </Link>
        {product.brand && (
          <div className="text-xs text-gray-500">{product.brand}</div>
        )}
        {product.spec_summary && (
          <div className="text-xs text-gray-500">{product.spec_summary}</div>
        )}
        <PriceTag
          current={product.sale_price_vnd ?? product.price_vnd}
          list={product.list_price_vnd ?? undefined}
        />
        <StockBadge status={product.stock_status} />
      </div>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => highlightSet(product.id)}
          className="btn-ghost text-xs"
          aria-label="Highlight sản phẩm"
        >
          Đánh dấu
        </button>
        <button
          type="button"
          onClick={() =>
            inCompare ? compareRemove(product.slug) : compareAdd(product.slug)
          }
          className="btn-outline text-xs"
          disabled={!inCompare && isFull}
          aria-label={
            inCompare
              ? "Bỏ khỏi so sánh"
              : isFull
                ? "Compare đầy (4/4)"
                : "Thêm vào so sánh"
          }
        >
          {inCompare ? (
            <>
              <Check className="mr-1 h-3 w-3" aria-hidden /> Đã so sánh
            </>
          ) : (
            <>
              <Plus className="mr-1 h-3 w-3" aria-hidden /> So sánh
            </>
          )}
        </button>
      </div>
    </article>
  );
}
