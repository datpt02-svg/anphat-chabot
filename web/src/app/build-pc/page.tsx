import { Suspense } from "react";
import { BuildPcForm } from "@/components/build-pc-form";

// Header uses useSearchParams via SearchBar — opt this page out of static
// prerender so Next 15 doesn't bail at build time.
export const dynamic = "force-dynamic";

export default function BuildPcPage() {
  return (
    <div className="mx-auto max-w-3xl">
      <h1 className="mb-1 font-heading text-2xl font-bold">Build PC</h1>
      <p className="mb-6 text-sm text-gray-500">
        Chọn mục đích và ngân sách — hệ thống đề xuất cấu hình tương thích.
      </p>
      <Suspense fallback={null}>
        <BuildPcForm />
      </Suspense>
    </div>
  );
}
