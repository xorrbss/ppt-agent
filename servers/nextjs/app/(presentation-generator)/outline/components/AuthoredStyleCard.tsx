"use client";

import React, { memo, useState } from "react";
import { Check } from "lucide-react";
import Image from "next/image";

import type { AuthoredStyleSummary } from "@/app/(presentation-generator)/services/api/authored";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

type PreviewFamily =
  | "console"
  | "editorial"
  | "executive"
  | "expressive"
  | "structured";

function getPreviewFamily(variant: string): PreviewFamily {
  if (/clean-light/i.test(variant)) {
    return "structured";
  }
  if (/(console|grid|blueprint|spectral|signal|clinical|precision)/i.test(variant)) {
    return "console";
  }
  if (/(editorial|journal|magazine|plate|margin|notebook|collage|tactile)/i.test(variant)) {
    return "editorial";
  }
  if (/(executive|ledger|boardroom|noir|dark|luminous)/i.test(variant)) {
    return "executive";
  }
  if (/(neon|acid|groovy|botanical|fluid|light|aura)/i.test(variant)) {
    return "expressive";
  }
  return "structured";
}

function getReadableTextColor(background: string): string {
  const hex = background.replace("#", "");
  if (!/^[0-9a-f]{6}$/i.test(hex)) return "#0F172A";

  const channels = [0, 2, 4].map((offset) => {
    const value = Number.parseInt(hex.slice(offset, offset + 2), 16) / 255;
    return value <= 0.04045
      ? value / 12.92
      : Math.pow((value + 0.055) / 1.055, 2.4);
  });
  const luminance =
    channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
  return luminance > 0.179 ? "#0F172A" : "#F8FAFC";
}

function AuthoredStylePreview({ style }: { style: AuthoredStyleSummary }) {
  const [imageFailed, setImageFailed] = useState(false);
  const family = getPreviewFamily(style.preview.variant);
  const colors = style.preview.palette;
  const background = style.preview.bg;
  const accent = style.preview.accent;
  const ink = getReadableTextColor(background);
  const secondary = colors[2] ?? accent;
  const tertiary = colors[3] ?? colors[1] ?? ink;
  const seed = Array.from(style.preview.variant).reduce(
    (sum, character) => sum + character.charCodeAt(0),
    0
  );

  if (style.preview.image && !imageFailed) {
    return (
      <div
        data-testid={`authored-style-preview-${style.id}`}
        data-variant={style.preview.variant}
        data-preview-family="reference"
        className="relative h-[168px] overflow-hidden bg-slate-950"
        aria-hidden="true"
      >
        <Image
          src={style.preview.image}
          alt=""
          fill
          sizes="(max-width: 768px) 100vw, 360px"
          className="object-cover"
          unoptimized
          onError={() => setImageFailed(true)}
        />
        <div className="pointer-events-none absolute inset-0 ring-1 ring-inset ring-black/10" />
      </div>
    );
  }

  return (
    <div
      data-testid={`authored-style-preview-${style.id}`}
      data-variant={style.preview.variant}
      data-preview-family={family}
      className="relative h-[168px] overflow-hidden"
      style={{ backgroundColor: background, color: ink }}
      aria-hidden="true"
    >
      <div className="absolute inset-x-4 top-3 flex items-center justify-between text-[8px] font-bold uppercase tracking-[0.18em] opacity-80">
        <span>{style.tags[0] ?? "AI AUTHORED"}</span>
        <span>{String((seed % 8) + 1).padStart(2, "0")}</span>
      </div>

      {family === "console" && (
        <>
          <div className="absolute inset-x-4 bottom-4 top-8 grid grid-cols-[1.25fr_0.75fr] gap-2 rounded-md border border-current/25 p-2">
            <div className="relative overflow-hidden rounded-sm border border-current/20">
              <div className="absolute inset-x-2 top-2 h-1 rounded-full bg-current/55" />
              <div className="absolute inset-x-2 bottom-2 flex h-12 items-end gap-1">
                {[38, 76, 54, 92, 66].map((height, index) => (
                  <span
                    key={height}
                    className="flex-1 rounded-t-[2px]"
                    style={{
                      height: `${height}%`,
                      backgroundColor: index === seed % 5 ? accent : secondary,
                    }}
                  />
                ))}
              </div>
            </div>
            <div className="grid grid-rows-3 gap-2">
              {[accent, secondary, tertiary].map((color, index) => (
                <div
                  key={`${color}-${index}`}
                  className="relative overflow-hidden rounded-sm border border-current/20 bg-black/5"
                >
                  <span
                    className="absolute left-2 top-2 h-2 w-2 rounded-full"
                    style={{ backgroundColor: color }}
                  />
                  <span className="absolute bottom-2 left-2 right-2 h-1 rounded-full bg-current/30" />
                </div>
              ))}
            </div>
          </div>
          <div
            className="absolute bottom-4 left-4 top-8 w-px opacity-80"
            style={{ backgroundColor: accent }}
          />
        </>
      )}

      {family === "editorial" && (
        <>
          <div className="absolute bottom-4 left-4 top-9 w-[43%] border-y border-current/30 py-2">
            <div className="text-[25px] font-black leading-[0.8] tracking-[-0.08em]">
              {style.name.slice(0, 4)}
            </div>
            <div className="mt-3 h-1 w-4/5 bg-current/65" />
            <div className="mt-1.5 h-1 w-3/5 bg-current/30" />
            <div
              className="absolute bottom-0 right-0 h-8 w-8 rounded-full"
              style={{ backgroundColor: accent }}
            />
          </div>
          <div
            className={cn(
              "absolute bottom-4 right-4 top-9 w-[45%] overflow-hidden",
              seed % 2 === 0 ? "rounded-tl-[42px]" : "rounded-br-[42px]"
            )}
            style={{ backgroundColor: secondary }}
          >
            <div className="absolute inset-3 border border-white/55" />
            <div
              className="absolute -bottom-5 -right-3 h-16 w-16 rotate-12"
              style={{ backgroundColor: tertiary }}
            />
          </div>
        </>
      )}

      {family === "executive" && (
        <>
          <div className="absolute inset-x-4 bottom-4 top-9 grid grid-cols-[0.7fr_1.3fr] overflow-hidden rounded-sm border border-current/25">
            <div className="flex flex-col justify-between border-r border-current/20 p-3">
              <span className="text-[28px] font-semibold leading-none">
                {72 + (seed % 23)}
              </span>
              <span className="h-1 w-8 bg-current/45" />
            </div>
            <div className="p-3">
              <div className="h-2 w-3/4 bg-current/80" />
              <div className="mt-2 h-1 w-full bg-current/30" />
              <div className="mt-1.5 h-1 w-5/6 bg-current/20" />
              <div className="mt-4 flex gap-1">
                {[secondary, accent, tertiary].map((color, index) => (
                  <span
                    key={`${color}-${index}`}
                    className="h-7 flex-1"
                    style={{ backgroundColor: color }}
                  />
                ))}
              </div>
            </div>
          </div>
          <div
            className="absolute right-4 top-9 h-1 w-12"
            style={{ backgroundColor: accent }}
          />
        </>
      )}

      {family === "expressive" && (
        <>
          <div
            className={cn(
              "absolute -left-8 top-9 h-24 w-32 rotate-[-12deg] rounded-[45%]",
              seed % 2 === 0 ? "rounded-tr-none" : "rounded-bl-none"
            )}
            style={{ backgroundColor: accent }}
          />
          <div
            className="absolute -right-4 bottom-1 h-24 w-24 rotate-[18deg] rounded-[32%]"
            style={{ backgroundColor: secondary }}
          />
          <div className="absolute inset-x-8 bottom-6 top-11 flex flex-col justify-center rounded-lg border border-current/25 bg-white/85 px-4 text-[#0F172A] shadow-sm">
            <div className="text-[18px] font-black leading-none tracking-[-0.05em]">
              {style.name.slice(0, 6)}
            </div>
            <div className="mt-2 h-1 w-4/5 rounded-full bg-slate-900/60" />
            <div className="mt-1.5 h-1 w-1/2 rounded-full bg-slate-900/25" />
          </div>
        </>
      )}

      {family === "structured" && (
        <>
          <div className="absolute inset-x-4 bottom-4 top-9 grid grid-cols-2 grid-rows-2 gap-2">
            <div
              className="row-span-2 rounded-sm p-3"
              style={{ backgroundColor: secondary }}
            >
              <span className="block text-[24px] font-black leading-none text-white/90">
                {String((seed % 9) + 1).padStart(2, "0")}
              </span>
              <span className="mt-8 block h-1 w-3/4 bg-white/65" />
            </div>
            <div className="rounded-sm border border-current/25 p-2">
              <span className="block h-1.5 w-2/3 bg-current/60" />
              <span className="mt-2 block h-1 w-full bg-current/25" />
            </div>
            <div className="flex items-end gap-1 rounded-sm border border-current/25 p-2">
              {[accent, tertiary, accent].map((color, index) => (
                <span
                  key={`${color}-${index}`}
                  className="flex-1"
                  style={{
                    height: `${45 + ((seed + index * 17) % 45)}%`,
                    backgroundColor: color,
                  }}
                />
              ))}
            </div>
          </div>
        </>
      )}

      <div className="absolute bottom-1.5 left-4 flex gap-1">
        {colors.slice(0, 5).map((color, index) => (
          <span
            key={`${color}-${index}`}
            className="h-1.5 w-4 rounded-full border border-black/10"
            style={{ backgroundColor: color }}
          />
        ))}
      </div>
    </div>
  );
}

export function handleAuthoredRadioKeyDown(
  event: React.KeyboardEvent<HTMLButtonElement>
) {
  const direction =
    event.key === "ArrowRight" || event.key === "ArrowDown"
      ? 1
      : event.key === "ArrowLeft" || event.key === "ArrowUp"
        ? -1
        : 0;
  const isBoundaryKey = event.key === "Home" || event.key === "End";
  if (direction === 0 && !isBoundaryKey) return;

  const group = event.currentTarget.closest('[role="radiogroup"]');
  const radios = Array.from(
    group?.querySelectorAll<HTMLButtonElement>('button[role="radio"]') ?? []
  );
  const currentIndex = radios.indexOf(event.currentTarget);
  if (currentIndex < 0 || radios.length === 0) return;

  event.preventDefault();
  const nextIndex =
    event.key === "Home"
      ? 0
      : event.key === "End"
        ? radios.length - 1
        : (currentIndex + direction + radios.length) % radios.length;
  radios[nextIndex].focus();
  radios[nextIndex].click();
}

interface AuthoredStyleCardProps {
  style: AuthoredStyleSummary;
  categoryLabel: string;
  isSelected: boolean;
  isTabStop: boolean;
  onSelect: (styleId: string) => void;
}

const AuthoredStyleCard = memo(function AuthoredStyleCard({
  style,
  categoryLabel,
  isSelected,
  isTabStop,
  onSelect,
}: AuthoredStyleCardProps) {
  const titleId = `authored-style-title-${style.id}`;
  const descriptionId = `authored-style-description-${style.id}`;

  return (
    <Card
      data-testid={`authored-style-card-${style.id}`}
      className={cn(
        "relative min-w-0 overflow-hidden rounded-[22px] border bg-white transition-all duration-200 hover:-translate-y-0.5 hover:shadow-md",
        isSelected
          ? "border-[#5141E5] ring-2 ring-[#5141E5]/25 shadow-sm"
          : "border-[#E8E9EC]"
      )}
    >
      <button
        type="button"
        role="radio"
        aria-checked={isSelected}
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        tabIndex={isTabStop ? 0 : -1}
        data-testid={`authored-style-select-${style.id}`}
        className="block h-full w-full min-w-0 cursor-pointer text-left outline-none focus-visible:ring-2 focus-visible:ring-[#5141E5] focus-visible:ring-inset"
        onClick={() => onSelect(style.id)}
        onKeyDown={handleAuthoredRadioKeyDown}
      >
        <AuthoredStylePreview style={style} />
        <div className="min-w-0 border-t border-[#EDEEEF] bg-white px-5 py-4">
          <div className="flex min-w-0 items-start justify-between gap-3">
            <div className="min-w-0">
              <span className="text-[10px] font-bold uppercase tracking-[0.12em] text-[#6B6D76]">
                {categoryLabel}
              </span>
              <span
                id={titleId}
                className="mt-0.5 block min-w-0 break-words text-sm font-bold text-gray-900 font-syne"
              >
                {style.name}
              </span>
            </div>
            {isSelected && (
              <span
                aria-hidden="true"
                className="flex shrink-0 items-center gap-1 rounded-full bg-[#EEECFF] px-2 py-1 text-[11px] font-semibold text-[#4033B8]"
              >
                <Check className="h-3 w-3" aria-hidden="true" />
                선택됨
              </span>
            )}
          </div>
          <p
            id={descriptionId}
            className="mt-2 line-clamp-2 break-all text-xs leading-5 text-gray-600 font-syne"
          >
            {style.description}
            <span className="sr-only">
              {` 카테고리 ${categoryLabel}. ${
                style.tags.length > 0 ? `태그 ${style.tags.join(", ")}.` : ""
              } ${
                style.use_cases.length > 0
                  ? `추천 용도 ${style.use_cases.join(", ")}.`
                  : ""
              }`}
            </span>
          </p>
          <div className="mt-3 flex min-h-6 flex-wrap gap-1.5" aria-hidden="true">
            {style.tags.slice(0, 2).map((tag) => (
              <span
                key={tag}
                className="rounded-full border border-[#E3E4E8] bg-[#F8F8FA] px-2 py-1 text-[10px] font-medium text-[#51535B]"
              >
                {tag}
              </span>
            ))}
          </div>
          {style.use_cases[0] && (
            <p className="mt-3 truncate text-[11px] text-[#676973]">
              추천 · {style.use_cases[0]}
            </p>
          )}
        </div>
      </button>
    </Card>
  );
});

export default AuthoredStyleCard;
