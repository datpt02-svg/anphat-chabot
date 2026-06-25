"use client";

import Image from "next/image";
import Link from "next/link";
import { PriceTag } from "./price-tag";
import type { RelatedProduct } from "@/lib/types";

export function RelatedProducts({ products }: { products: RelatedProduct[] }) {
  if (!products?.length) return null;
  return (
    <section aria-label="Sản phẩm liên quan" className="mt-12">
      <h2 className="mb-4 text-lg font-semibold">Sản phẩm liên quan</h2>
      <ul className="grid grid-cols-2 gap-4 md:grid-cols-4">
        {products.map((p) => (
          <li key={p.id} className="card flex flex-col gap-2">
            <Link href={`/products/${p.slug}`} className="block">
              {p.thumbnail_url ? (
                <Image
                  src={p.thumbnail_url}
                  alt={p.name}
                  width={200}
                  height={150}
                  className="aspect-[4/3] w-full rounded object-contain"
                />
              ) : (
                <div
                  className="flex aspect-[4/3] w-full items-center justify-center rounded bg-gray-100 text-xs text-gray-500"
                  aria-label="No image"
                >
                  Không có ảnh
                </div>
              )}
            </Link>
            <Link
              href={`/products/${p.slug}`}
              className="line-clamp-2 text-sm hover:underline"
            >
              {p.name}
            </Link>
            <PriceTag current={p.price_vnd} list={null} />
          </li>
        ))}
      </ul>
    </section>
  );
}
