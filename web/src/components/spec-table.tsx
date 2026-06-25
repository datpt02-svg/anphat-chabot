import type { SpecItem } from "@/lib/types";

export function SpecTable({
  groups,
  className,
}: {
  groups: Record<string, SpecItem[]>;
  className?: string;
}) {
  const entries = Object.entries(groups || {});
  if (entries.length === 0) {
    return (
      <p className="text-sm text-gray-500" role="status">
        Không có thông số kỹ thuật.
      </p>
    );
  }
  return (
    <div className={className}>
      {entries.map(([group, items]) => (
        <section key={group} aria-label={group} className="mb-6">
          <h3 className="mb-2 text-sm font-semibold">{group}</h3>
          <dl className="divide-y divide-black/5 rounded-xl border border-black/5 bg-white">
            {items.map((it, i) => (
              <div
                key={`${group}-${i}`}
                className="grid grid-cols-2 gap-4 px-4 py-2 text-sm"
              >
                <dt className="text-gray-500">{it.label}</dt>
                <dd className="text-foreground">{it.value || "—"}</dd>
              </div>
            ))}
          </dl>
        </section>
      ))}
    </div>
  );
}
