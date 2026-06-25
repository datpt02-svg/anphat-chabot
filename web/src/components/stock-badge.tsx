"use client";

import { cn } from "@/lib/utils";
import type { StockStatus } from "@/lib/types";

const LABEL: Record<StockStatus, string> = {
  in_stock: "Còn hàng",
  out_of_stock: "Hết hàng",
  preorder: "Đặt trước",
  contact: "Liên hệ",
  unknown: "—",
};

const CLASS: Record<StockStatus, string> = {
  in_stock: "badge-green",
  out_of_stock: "badge-red",
  preorder: "badge-amber",
  contact: "badge-violet",
  unknown: "badge-gray",
};

export function StockBadge({
  status,
  className,
}: {
  status: StockStatus;
  className?: string;
}) {
  return (
    <span className={cn(CLASS[status] || "badge-gray", className)}>
      {LABEL[status] || status}
    </span>
  );
}
