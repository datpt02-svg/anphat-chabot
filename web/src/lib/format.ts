const VND = new Intl.NumberFormat("vi-VN", {
  style: "currency",
  currency: "VND",
  maximumFractionDigits: 0,
});

export function formatVnd(amount: number | null | undefined): string {
  if (amount == null) return "—";
  return VND.format(amount);
}

export function formatPercent(pct: number | null | undefined): string {
  if (pct == null) return "—";
  return `${Math.round(pct)}%`;
}

export function truncate(str: string, n: number): string {
  if (str.length <= n) return str;
  return str.slice(0, n - 1) + "…";
}

export function slugify(value: string): string {
  return value
    .toLowerCase()
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-|-$)+/g, "");
}
