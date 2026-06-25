export default function Loading() {
  return (
    <div
      className="grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-4"
      role="status"
      aria-label="Đang tải"
    >
      {Array.from({ length: 8 }).map((_, i) => (
        <div
          key={i}
          className="card flex animate-pulse flex-col gap-3"
          aria-hidden
        >
          <div className="aspect-[4/3] w-full rounded-xl bg-gray-100" />
          <div className="h-4 w-3/4 rounded bg-gray-100" />
          <div className="h-4 w-1/2 rounded bg-gray-100" />
          <div className="h-6 w-1/3 rounded bg-gray-100" />
        </div>
      ))}
    </div>
  );
}
