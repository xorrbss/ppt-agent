"use client";

import React from "react";
import { ChevronLeft, ChevronRight, ImageOff } from "lucide-react";
import { useTranslations } from "next-intl";

import type { AuthoredStyleSummary } from "@/app/(presentation-generator)/services/api/authored";
import { cn } from "@/lib/utils";

import { AuthoredStylePreview } from "../AuthoredStyleCard";

interface StyleSampleSlidesProps {
  style: AuthoredStyleSummary;
  slideIndex: number;
  onSlideChange: (slideIndex: number) => void;
}

const pagerButtonClass =
  "absolute top-1/2 flex h-10 w-10 -translate-y-1/2 items-center justify-center rounded-full bg-white/90 text-[#27251E] shadow-md transition hover:bg-white disabled:pointer-events-none disabled:opacity-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand";

function slideNumber(index: number): string {
  return String(index).padStart(2, "0");
}

export default function StyleSampleSlides({
  style,
  slideIndex,
  onSlideChange,
}: StyleSampleSlidesProps) {
  const t = useTranslations("authoredStyleSamples");
  const samples = style.samples;

  if (!samples) {
    return (
      <div data-testid="style-sample-empty" className="flex flex-col gap-3">
        <div className="overflow-hidden rounded-xl border border-[#EBEBEC]">
          <AuthoredStylePreview style={style} compact />
        </div>
        <p className="flex items-center gap-2 text-sm text-[#676973]">
          <ImageOff className="h-4 w-4 shrink-0" aria-hidden="true" />
          {t("samplesPreparing")}
        </p>
      </div>
    );
  }

  const aspectRatio = `${samples.canvas.width} / ${samples.canvas.height}`;
  const slide = samples.slides[slideIndex] ?? samples.slides[0];
  const total = samples.slides.length;
  const position = samples.slides.indexOf(slide);

  return (
    <div className="flex flex-col gap-5">
      <div>
        <div
          className="relative overflow-hidden rounded-xl border border-[#EBEBEC] bg-[#F4F4F6]"
          style={{ aspectRatio }}
        >
          {/* Plain <img>: the files are pre-optimised WebP and the Next image
              optimiser would fetch without the session cookie (D12). */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            key={slide.fullUrl}
            src={slide.fullUrl}
            alt={t("slideAlt", { index: slide.index, title: slide.title })}
            loading="lazy"
            data-testid="style-sample-stage-image"
            className="h-full w-full object-contain"
          />
          <button
            type="button"
            className={cn(pagerButtonClass, "left-3")}
            onClick={() => onSlideChange(position - 1)}
            disabled={position <= 0}
            aria-label={t("previousSlide")}
            data-testid="style-sample-prev-slide"
          >
            <ChevronLeft className="h-5 w-5" aria-hidden="true" />
          </button>
          <button
            type="button"
            className={cn(pagerButtonClass, "right-3")}
            onClick={() => onSlideChange(position + 1)}
            disabled={position >= total - 1}
            aria-label={t("nextSlide")}
            data-testid="style-sample-next-slide"
          >
            <ChevronRight className="h-5 w-5" aria-hidden="true" />
          </button>
        </div>
        <div className="mt-2 flex items-center justify-between gap-3 text-xs text-[#676973]">
          <span className="truncate font-medium text-[#27251E]">{slide.title}</span>
          <span
            className="shrink-0 tabular-nums"
            aria-live="polite"
            data-testid="style-sample-counter"
          >
            {t("slideCounter", { current: position + 1, total })}
          </span>
        </div>
      </div>

      <section aria-labelledby="style-sample-all-slides">
        <h3
          id="style-sample-all-slides"
          className="text-xs font-bold uppercase tracking-[0.12em] text-[#676973]"
        >
          {t("allSlides", { count: total })}
        </h3>
        <ol className="mt-3 grid grid-cols-[repeat(auto-fill,minmax(120px,1fr))] gap-3">
          {samples.slides.map((tile, tileIndex) => (
            <li key={tile.index}>
              <button
                type="button"
                onClick={() => onSlideChange(tileIndex)}
                aria-current={tileIndex === position ? "true" : undefined}
                data-testid={`style-sample-tile-${tile.index}`}
                className={cn(
                  "relative block w-full overflow-hidden rounded-lg border bg-[#F4F4F6] transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand",
                  tileIndex === position
                    ? "border-[#6194F4] ring-2 ring-[#6194F4]/30"
                    : "border-[#EBEBEC] hover:border-[#C9CBD2]",
                )}
                style={{ aspectRatio }}
              >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={tile.thumbUrl}
                  alt={t("slideAlt", { index: tile.index, title: tile.title })}
                  loading="lazy"
                  className="h-full w-full object-contain"
                />
                <span className="absolute left-1.5 top-1.5 rounded bg-black/65 px-1.5 py-0.5 text-[10px] font-semibold tabular-nums text-white">
                  {slideNumber(tile.index)}
                </span>
              </button>
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}
