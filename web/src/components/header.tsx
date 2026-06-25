"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCompareStore } from "@/store/compareStore";
import { SearchBar } from "./search-bar";
import { useCompareCount } from "./compare-count";

export function Header() {
  useCompareCount(); // ensures store hydration
  const compareCount = useCompareStore((s) => s.slugs.length);
  const router = useRouter();
  return (
    <header className="border-b border-black/5 bg-white">
      <div className="container mx-auto flex items-center gap-4 px-4 py-3">
        <Link href="/" className="font-heading text-xl font-bold text-primary">
          An Phát PC
        </Link>
        <SearchBar
          className="flex-1"
          onSubmit={(q) =>
            router.push(q ? `/search?q=${encodeURIComponent(q)}` : "/search")
          }
        />
        <Link href="/build-pc" className="btn-outline hidden md:inline-flex">
          Build PC
        </Link>
        <Link
          href="/compare"
          className="btn-outline relative"
          aria-label={`So sánh (${compareCount})`}
        >
          So sánh
          {compareCount > 0 && (
            <span className="badge-violet ml-2">{compareCount}</span>
          )}
        </Link>
      </div>
    </header>
  );
}
