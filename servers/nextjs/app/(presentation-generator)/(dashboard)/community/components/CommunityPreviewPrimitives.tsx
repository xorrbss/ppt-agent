import Image from "next/image";
import type { ReactNode } from "react";

import {
  IMAGE_PROVIDER_OPTIONS,
  LLM_PROVIDER_OPTIONS,
  WEB_SEARCH_PROVIDER_OPTIONS,
} from "@/utils/provider-registry.generated";

export function CommunityCount({
  icon,
  label,
  value,
}: {
  icon: ReactNode;
  label: string;
  value: number;
}) {
  return (
    <span
      aria-label={label}
      className="inline-flex h-[41px] min-w-[60px] shrink-0 items-center justify-center gap-1.5 rounded-full border border-[#EDEEEF] bg-white px-2.5 font-syne text-sm font-normal text-[#4C4C4C] tabular-nums sm:min-w-[68px] sm:px-3"
    >
      {icon}
      {formatCount(value)}
    </span>
  );
}

export function ProviderLogo({
  provider,
  className = "",
}: {
  provider: string;
  className?: string;
}) {
  const visual = getProviderVisual(provider);
  return (
    <span
      className={`relative flex h-[22px] w-[22px] shrink-0 items-center justify-center overflow-hidden rounded-full border border-[#EDEEEF] bg-white ${className}`}
      title={visual.label}
    >
      {visual.src ? (
        <Image src={visual.src} alt="" aria-hidden="true" fill sizes="22px" className="object-contain p-0.5" />
      ) : (
        <span className="text-[8px] font-semibold text-[#4C4C4C]">{visual.initials}</span>
      )}
    </span>
  );
}

export function SetupRow({ label, children }: { label: string; children: ReactNode }) {
  return <div className="flex min-h-[18px] items-start gap-2.5"><dt className="w-[76px] shrink-0 pt-0.5 text-sm font-normal text-[#808080]">{label}</dt><dd className="flex min-w-0 flex-wrap items-center gap-1">{children}</dd></div>;
}

export function SetupChip({ children }: { children: ReactNode }) {
  return <span className="inline-flex items-center justify-center rounded-[10px] border border-[#D9D6FE] bg-[#FAFAFF] px-2.5 py-0.5 text-xs font-normal text-[#4C4C4C]">{children}</span>;
}

export function getProviderVisual(provider: string) {
  const normalized = provider.trim().toLowerCase();
  const option = LLM_PROVIDER_OPTIONS[normalized] ?? IMAGE_PROVIDER_OPTIONS[normalized] ?? WEB_SEARCH_PROVIDER_OPTIONS[normalized];
  const label = option?.label ?? (normalized.split(/[-_\s]+/).filter(Boolean).map((word) => word.charAt(0).toUpperCase() + word.slice(1)).join(" ") || "Presenton");
  const initials = label.split(/\s+/).filter(Boolean).slice(0, 2).map((word) => word[0]).join("").toUpperCase() || "P";
  return { label, src: option?.icon, initials };
}

function formatCount(value: number) {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(value >= 10_000_000 ? 0 : 1)}m`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(value >= 100_000 ? 0 : 1)}k`;
  return String(value);
}
