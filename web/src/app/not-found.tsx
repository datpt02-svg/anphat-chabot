import Link from "next/link";

export default function NotFound() {
  return (
    <div className="card flex flex-col items-center gap-3 py-16 text-center">
      <h1 className="font-heading text-2xl font-bold">404</h1>
      <p className="text-sm text-gray-500">Trang không tồn tại.</p>
      <Link href="/" className="btn-primary">
        Về trang chủ
      </Link>
    </div>
  );
}
