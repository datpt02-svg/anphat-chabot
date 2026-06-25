import type { ReactNode } from "react";

export function EmptyState({
  title,
  description,
  action,
  className,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`card flex flex-col items-center gap-2 py-12 text-center text-sm text-gray-500 ${className || ""}`}
      role="status"
    >
      <h3 className="text-base font-semibold text-foreground">{title}</h3>
      {description && <p>{description}</p>}
      {action}
    </div>
  );
}
