"use client";

import { Check, AlertTriangle, ExternalLink } from "lucide-react";
import { formatVnd } from "@/lib/format";
import type { PCBuild, PCComponent } from "@/lib/types";

const CATEGORY_LABEL: Record<string, string> = {
  cpu: "CPU",
  mobo: "Mainboard",
  ram: "RAM",
  gpu: "VGA",
  storage: "Ổ cứng",
  psu: "Nguồn",
  case: "Vỏ case",
  cooler: "Tản nhiệt",
};

function ComponentRow({ c }: { c: PCComponent }) {
  return (
    <tr className="border-b border-black/5 last:border-0">
      <td className="px-3 py-2 text-sm font-medium">
        {CATEGORY_LABEL[c.category] || c.category}
      </td>
      <td className="px-3 py-2 text-sm">
        <a
          href={c.url}
          target="_blank"
          rel="noreferrer noopener"
          className="inline-flex items-center gap-1 hover:underline"
        >
          {c.name}
          <ExternalLink className="h-3 w-3" aria-hidden />
        </a>
        {c.pinned && <span className="badge-violet ml-2">pinned</span>}
      </td>
      <td className="px-3 py-2 text-right text-sm">{formatVnd(c.price_vnd)}</td>
    </tr>
  );
}

export function BuildPcResult({ build }: { build: PCBuild }) {
  const compatible = build.compatibility.compatible;
  return (
    <section
      className="card mt-6"
      aria-label="Kết quả build PC"
    >
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-semibold">Cấu hình đề xuất</h2>
        <div className="flex items-center gap-2 text-sm">
          {compatible ? (
            <span className="badge-green inline-flex items-center gap-1">
              <Check className="h-3 w-3" aria-hidden /> Tương thích
            </span>
          ) : (
            <span className="badge-red inline-flex items-center gap-1">
              <AlertTriangle className="h-3 w-3" aria-hidden /> Lỗi tương thích
            </span>
          )}
          <span className="font-semibold">{formatVnd(build.total_price_vnd)}</span>
        </div>
      </div>
      <table className="w-full">
        <thead>
          <tr className="text-left text-xs uppercase text-gray-500">
            <th className="px-3 py-2">Loại</th>
            <th className="px-3 py-2">Sản phẩm</th>
            <th className="px-3 py-2 text-right">Giá</th>
          </tr>
        </thead>
        <tbody>
          {build.build.map((c, i) => (
            <ComponentRow key={`${c.product_id}-${i}`} c={c} />
          ))}
        </tbody>
      </table>
      {build.reasoning && (
        <p className="mt-4 text-sm text-gray-600">{build.reasoning}</p>
      )}
      {build.compatibility.issues.length > 0 && (
        <div className="mt-4 rounded-lg border border-red-100 bg-red-50 p-3">
          <h3 className="mb-1 text-sm font-semibold text-red-700">Vấn đề</h3>
          <ul className="list-disc pl-5 text-sm text-red-700">
            {build.compatibility.issues.map((it, i) => (
              <li key={i}>
                {it.rule}: {it.detail}
              </li>
            ))}
          </ul>
        </div>
      )}
      {build.compatibility.warnings.length > 0 && (
        <div className="mt-4 rounded-lg border border-amber-100 bg-amber-50 p-3">
          <h3 className="mb-1 text-sm font-semibold text-amber-700">Cảnh báo</h3>
          <ul className="list-disc pl-5 text-sm text-amber-700">
            {build.compatibility.warnings.map((it, i) => (
              <li key={i}>
                {it.rule}: {it.detail}
              </li>
            ))}
          </ul>
        </div>
      )}
      {build.alternatives.length > 0 && (
        <div className="mt-6">
          <h3 className="mb-2 text-sm font-semibold">Phương án thay thế</h3>
          {build.alternatives.map((alt, i) => (
            <div
              key={i}
              className="card mb-3"
              aria-label={`Alternative build ${i + 1}`}
            >
              <div className="mb-2 flex items-center justify-between">
                <span className="badge-violet">Phương án {i + 1}</span>
                <span className="font-semibold">
                  {formatVnd(alt.total_price_vnd)}
                </span>
              </div>
              <ul className="text-sm">
                {alt.build.map((c, j) => (
                  <li
                    key={`${c.product_id}-${i}-${j}`}
                    className="flex justify-between border-b border-black/5 py-1 last:border-0"
                  >
                    <span>
                      {CATEGORY_LABEL[c.category] || c.category}: {c.name}
                    </span>
                    <span className="text-gray-500">
                      {formatVnd(c.price_vnd)}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
