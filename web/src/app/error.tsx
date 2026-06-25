"use client";

import Link from "next/link";
import { useEffect } from "react";

export default function AppError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Log to console; production would forward to a monitoring service.
    console.error("AppRouter error boundary:", error);
  }, [error]);
  return (
    <div className="card flex flex-col items-center gap-3 py-12 text-center">
      <h1 className="text-base font-semibold text-red-600">Có lỗi xảy ra</h1>
      <p className="text-sm text-gray-600">{error.message || "Lỗi không xác định"}</p>
      {error.digest && (
        <p className="text-xs text-gray-500">
          Mã lỗi: <code>{error.digest}</code>
        </p>
      )}
      <div className="flex gap-2">
        <button type="button" onClick={() => reset()} className="btn-primary">
          Thử lại
        </button>
        <Link href="/" className="btn-outline">
          Về trang chủ
        </Link>
      </div>
    </div>
  );
}
