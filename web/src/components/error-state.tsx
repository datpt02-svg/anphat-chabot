"use client";

import Link from "next/link";
import { ApiClientError } from "@/lib/api";

export function ErrorState({
  error,
  retry,
}: {
  error: unknown;
  retry?: () => void;
}) {
  let message = "Đã xảy ra lỗi. Vui lòng thử lại.";
  let traceId: string | null = null;
  if (error instanceof ApiClientError) {
    message = error.message;
    traceId = error.traceId;
  } else if (error instanceof Error) {
    message = error.message;
  }
  return (
    <div
      className="card flex flex-col items-center gap-3 py-10 text-center"
      role="alert"
    >
      <h3 className="text-base font-semibold text-red-600">Có lỗi xảy ra</h3>
      <p className="text-sm text-gray-600">{message}</p>
      {traceId && (
        <p className="text-xs text-gray-500">
          Mã trace:{" "}
          <code className="rounded bg-gray-100 px-1 py-0.5">{traceId}</code>
        </p>
      )}
      <div className="mt-2 flex gap-2">
        {retry && (
          <button type="button" onClick={retry} className="btn-primary">
            Thử lại
          </button>
        )}
        <Link href="/" className="btn-outline">
          Về trang chủ
        </Link>
      </div>
    </div>
  );
}
