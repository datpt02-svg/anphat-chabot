"use client";

import { formatVnd, formatPercent } from "@/lib/format";
import { cn } from "@/lib/utils";

export function PriceTag({
  current,
  list,
  className,
}: {
  current: number | null | undefined;
  list: number | null | undefined;
  className?: string;
}) {
  const hasDiscount = list != null && current != null && list > current;
  const discountPct =
    hasDiscount && list ? Math.round(((list - (current as number)) / list) * 100) : null;
  return (
    <div className={cn("flex flex-wrap items-baseline gap-2", className)}>
      <span
        className={cn(
          "text-base font-semibold",
          hasDiscount ? "text-red-600" : "text-foreground",
        )}
      >
        {formatVnd(current)}
      </span>
      {hasDiscount && (
        <>
          <span className="text-sm text-gray-500 line-through">{formatVnd(list)}</span>
          <span className="badge-red">-{formatPercent(discountPct)}</span>
        </>
      )}
    </div>
  );
}
