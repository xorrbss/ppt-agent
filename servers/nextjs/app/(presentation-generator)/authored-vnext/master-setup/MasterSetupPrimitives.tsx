import type { ReactNode } from "react";

export function Badge({
  tone,
  children,
}: {
  tone: "blue" | "green" | "amber" | "red";
  children: ReactNode;
}) {
  const color = {
    blue: "bg-blue-100 text-blue-900",
    green: "bg-emerald-100 text-emerald-900",
    amber: "bg-amber-100 text-amber-950",
    red: "bg-red-100 text-red-900",
  };
  return (
    <span
      className={`rounded-full px-2.5 py-1 text-[11px] font-bold ${color[tone]}`}
    >
      {children}
    </span>
  );
}

export function Label({ children }: { children: ReactNode }) {
  return (
    <span className="mb-1 block text-xs font-semibold text-slate-700">
      {children}
    </span>
  );
}
