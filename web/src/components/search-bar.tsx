"use client";

import { useEffect, useRef } from "react";
import { Search } from "lucide-react";

/**
 * Presentational search input. No useSearchParams — caller owns URL state.
 * Use this inside Header (must render in any route, including /404).
 * Pages that need URL-bound search wrap <SearchBarControlled/> in Suspense.
 */
export function SearchBar({
  className,
  size = "md",
  autoFocus = false,
  defaultValue = "",
  onSubmit,
}: {
  className?: string;
  size?: "md" | "lg";
  autoFocus?: boolean;
  defaultValue?: string;
  onSubmit?: (q: string) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);

  // Global "/" keybinding to focus search (ignore when typing in inputs)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "/") return;
      const target = e.target as HTMLElement | null;
      if (target && /^(input|textarea|select)$/i.test(target.tagName)) return;
      e.preventDefault();
      inputRef.current?.focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const sizeCls = size === "lg" ? "h-14 text-lg" : "h-10 text-sm";

  return (
    <form
      role="search"
      className={className}
      onSubmit={(e) => {
        e.preventDefault();
        const data = new FormData(e.currentTarget);
        const q = String(data.get("q") ?? "").trim();
        onSubmit?.(q);
      }}
    >
      <label className="relative block">
        <span className="sr-only">Tìm sản phẩm</span>
        <Search
          className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400"
          aria-hidden
        />
        <input
          ref={inputRef}
          type="search"
          name="q"
          defaultValue={defaultValue}
          placeholder="Tìm laptop, CPU, màn hình…"
          className={`input pl-9 ${sizeCls}`}
          autoFocus={autoFocus}
        />
      </label>
    </form>
  );
}
