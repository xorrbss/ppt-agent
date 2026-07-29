"use client";

import type { PropsWithChildren, SelectHTMLAttributes } from "react";

const buttonClass =
  "rounded border border-slate-600 bg-slate-900 px-2 py-1 text-xs text-slate-100 hover:border-violet-400 disabled:cursor-not-allowed disabled:opacity-40";
const inputClass =
  "h-7 rounded border border-slate-600 bg-slate-950 px-1.5 text-xs text-slate-100 outline-none focus:border-violet-400";

export function ToolbarButton({
  children,
  className = "",
  ...props
}: PropsWithChildren<React.ButtonHTMLAttributes<HTMLButtonElement>>) {
  return (
    <button
      type="button"
      className={`${buttonClass} ${className}`}
      {...props}
    >
      {children}
    </button>
  );
}

export function ToolbarSelect({
  className = "",
  ...props
}: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={`${inputClass} ${className}`} {...props} />;
}

export function ToolbarField({
  label,
  children,
}: PropsWithChildren<{ label: string }>) {
  return (
    <label className="flex items-center gap-1 text-[11px] text-slate-300">
      <span className="sr-only">{label}</span>
      {children}
    </label>
  );
}

export const toolbarInputClass = inputClass;
