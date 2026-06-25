import Link from "next/link";
import { SearchBar } from "@/components/search-bar";
import { ProductCard } from "@/components/product-card";
import { searchProducts } from "@/lib/search";
import { getCategories, getProduct } from "@/lib/products";
import { ErrorState } from "@/components/error-state";
import { Laptop, Cpu, Monitor, MemoryStick, HardDrive, Zap, Box, Keyboard, Mouse } from "lucide-react";

const STATIC_CATEGORIES = [
  { name: "laptop", label: "Laptop", icon: Laptop },
  { name: "desktop_pc", label: "PC", icon: Box },
  { name: "cpu", label: "CPU", icon: Cpu },
  { name: "mainboard", label: "Mainboard", icon: MemoryStick },
  { name: "gpu", label: "VGA", icon: Zap },
  { name: "storage", label: "Ổ cứng", icon: HardDrive },
  { name: "monitor", label: "Màn hình", icon: Monitor },
  { name: "keyboard", label: "Bàn phím", icon: Keyboard },
];

export default async function Home() {
  // SSR data fetches
  const [search, categoriesResult] = await Promise.allSettled([
    searchProducts({ sort: "newest", limit: 8 }),
    getCategories(),
  ]);

  return (
    <div className="flex flex-col gap-12">
      <section className="rounded-3xl bg-gradient-to-br from-primary/10 to-cta/10 px-6 py-16 text-center">
        <h1 className="font-heading text-3xl font-bold md:text-4xl">
          Tìm laptop, linh kiện PC phù hợp
        </h1>
        <p className="mt-2 text-sm text-gray-600 md:text-base">
          Dữ liệu cập nhật từ anphatpc.com.vn — gợi ý bởi trợ lý AI
        </p>
        <div className="mx-auto mt-6 max-w-2xl">
          <SearchBar size="lg" autoFocus />
        </div>
      </section>

      <section aria-label="Danh mục phổ biến">
        <h2 className="mb-4 text-lg font-semibold">Danh mục phổ biến</h2>
        {categoriesResult.status === "fulfilled" && (
          <ul className="grid grid-cols-2 gap-3 sm:grid-cols-4 md:grid-cols-8">
            {STATIC_CATEGORIES.map(({ name, label, icon: Icon }) => {
              const real = (categoriesResult.value as { name: string; count: number }[]).find(
                (c) => c.name === name,
              );
              return (
                <li key={name}>
                  <Link
                    href={`/search?category=${name}`}
                    className="card flex flex-col items-center gap-2 py-4 text-center hover:border-primary/30"
                  >
                    <Icon className="h-6 w-6 text-primary" aria-hidden />
                    <span className="text-xs font-medium">{label}</span>
                    {real && (
                      <span className="text-[10px] text-gray-500">
                        {real.count.toLocaleString("vi-VN")}
                      </span>
                    )}
                  </Link>
                </li>
              );
            })}
          </ul>
        )}
        {categoriesResult.status === "rejected" && (
          <p className="text-sm text-gray-500">
            Không tải được danh mục. Thử lại sau.
          </p>
        )}
      </section>

      <section aria-label="Sản phẩm mới">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold">Sản phẩm mới</h2>
          <Link href="/search?sort=newest" className="btn-outline text-xs">
            Xem tất cả
          </Link>
        </div>
        {search.status === "fulfilled" ? (
          <ul className="grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-4">
            {search.value.hits.map((p) => (
              <li key={p.id}>
                <ProductCard product={p} />
              </li>
            ))}
          </ul>
        ) : (
          <ErrorState
            error={search.reason}
            retry={undefined}
          />
        )}
      </section>
    </div>
  );
}
