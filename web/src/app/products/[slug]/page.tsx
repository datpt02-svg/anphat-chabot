import Image from "next/image";
import Link from "next/link";
import { notFound } from "next/navigation";
import { getProduct, getRelated } from "@/lib/products";
import { PriceTag } from "@/components/price-tag";
import { StockBadge } from "@/components/stock-badge";
import { SpecTable } from "@/components/spec-table";
import { RelatedProducts } from "@/components/related-products";
import { CompareToggleButton } from "@/components/compare-toggle-button";
import { ApiClientError } from "@/lib/api";
import { ErrorState } from "@/components/error-state";

// No static params — slug set is too large (26k products).
// M9 will add ISR/SSR cache.
export const dynamic = "force-dynamic";

export default async function ProductPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  let product, related;
  try {
    [product, related] = await Promise.all([
      getProduct(slug),
      getRelated(slug, 8),
    ]);
  } catch (err) {
    if (err instanceof ApiClientError && err.status === 404) notFound();
    return <ErrorState error={err} />;
  }

  const heroImage = product.images?.[0] || product.current_price?.price_vnd ? null : null;

  return (
    <article className="flex flex-col gap-8">
      <div className="grid gap-6 md:grid-cols-2">
        <div className="card flex items-center justify-center p-6">
          {product.images?.[0] ? (
            <Image
              src={product.images[0]}
              alt={product.name}
              width={800}
              height={600}
              className="max-h-[480px] w-full rounded object-contain"
              priority
            />
          ) : (
            <div className="flex aspect-[4/3] w-full items-center justify-center rounded bg-gray-100 text-sm text-gray-500">
              Không có ảnh
            </div>
          )}
        </div>
        <aside className="flex flex-col gap-4 md:sticky md:top-4">
          {product.breadcrumbs.length > 0 && (
            <nav className="text-xs text-gray-500" aria-label="Breadcrumb">
              {product.breadcrumbs.map((c, i) => (
                <span key={i}>
                  {i > 0 && " / "}
                  {c}
                </span>
              ))}
            </nav>
          )}
          <h1 className="font-heading text-2xl font-bold">{product.name}</h1>
          {product.brand && (
            <Link
              href={`/search?brand=${encodeURIComponent(product.brand)}`}
              className="text-sm text-primary hover:underline"
            >
              {product.brand}
            </Link>
          )}
          {product.current_price && (
            <PriceTag
              current={product.current_price.price_vnd}
              list={product.current_price.list_price_vnd ?? undefined}
              className="text-lg"
            />
          )}
          {product.current_price && (
            <StockBadge status={product.current_price.stock_status} />
          )}
          <CompareToggleButton slug={product.slug} />
        </aside>
      </div>
      {product.description && (
        <section aria-label="Mô tả">
          <h2 className="mb-2 text-lg font-semibold">Mô tả</h2>
          <p className="whitespace-pre-line text-sm text-gray-700">
            {product.description}
          </p>
        </section>
      )}
      {product.specs_grouped && (
        <section aria-label="Thông số kỹ thuật">
          <h2 className="mb-2 text-lg font-semibold">Thông số kỹ thuật</h2>
          <SpecTable groups={product.specs_grouped} />
        </section>
      )}
      <RelatedProducts products={related || []} />
    </article>
  );
}
