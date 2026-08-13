"use client";

import { useMemo, useState, type KeyboardEvent } from "react";
import { useTranslations } from "next-intl";

import type {
  SlideRailItem,
  VirtualRailWindow,
  WorkbenchEntityId,
} from "./types";

export interface VirtualSlideRailProps {
  slides: readonly SlideRailItem[];
  activeSlideId: WorkbenchEntityId | null;
  window: VirtualRailWindow;
  disabled?: boolean;
  onSelect: (slideId: WorkbenchEntityId) => void;
  onReorder: (slideId: WorkbenchEntityId, targetPosition: number) => void;
  onWindowChange?: (startIndex: number) => void;
}

function clamp(value: number, minimum: number, maximum: number) {
  return Math.max(minimum, Math.min(maximum, value));
}

export function VirtualSlideRail({
  slides,
  activeSlideId,
  window,
  disabled = false,
  onSelect,
  onReorder,
  onWindowChange,
}: VirtualSlideRailProps) {
  const t = useTranslations("authoredVnext.workbench.rail");
  const [announcement, setAnnouncement] = useState("");
  const overscan = Math.max(0, window.overscan ?? 2);
  const start = clamp(window.startIndex - overscan, 0, slides.length);
  const end = clamp(
    window.startIndex + window.visibleCount + overscan,
    start,
    slides.length,
  );
  const visibleSlides = useMemo(() => slides.slice(start, end), [slides, start, end]);

  const requestReorder = (slide: SlideRailItem, targetPosition: number) => {
    const nextPosition = clamp(targetPosition, 0, slides.length - 1);
    if (nextPosition === slide.position || disabled) return;
    onReorder(slide.id, nextPosition);
    setAnnouncement(t("reordered", { title: slide.title, position: nextPosition + 1 }));
  };

  const handleKeyDown = (
    event: KeyboardEvent<HTMLButtonElement>,
    slide: SlideRailItem,
  ) => {
    if (!event.altKey || (event.key !== "ArrowUp" && event.key !== "ArrowDown")) return;
    event.preventDefault();
    requestReorder(slide, slide.position + (event.key === "ArrowUp" ? -1 : 1));
  };

  return (
    <nav aria-label={t("navigationAria")} className="flex h-full min-h-0 flex-col bg-slate-950 text-white">
      <div className="flex items-center justify-between border-b border-slate-800 px-3 py-2">
        <h2 className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-300">{t("title")}</h2>
        <span className="text-xs tabular-nums text-slate-400">{t("count", { count: slides.length })}</span>
      </div>
      <p className="sr-only">{t("keyboardHelp")}</p>
      <div
        className="relative min-h-0 flex-1 overflow-y-auto overscroll-contain"
        data-testid="authored-rail-viewport"
        onScroll={(event) => {
          if (!onWindowChange) return;
          onWindowChange(Math.floor(event.currentTarget.scrollTop / window.rowHeight));
        }}
        style={{ minHeight: window.visibleCount * window.rowHeight }}
        tabIndex={0}
      >
        <ol
          aria-label={t("virtualizedAria")}
          className="relative"
          data-testid="authored-rail"
          style={{ height: slides.length * window.rowHeight }}
        >
          {visibleSlides.map((slide) => {
            const active = slide.id === activeSlideId;
            return (
              <li
                className="absolute left-0 w-full px-2 py-1"
                key={slide.id}
                style={{ height: window.rowHeight, top: slide.position * window.rowHeight }}
              >
                <div
                  className={`group flex h-full gap-2 rounded-lg border p-1.5 transition ${
                    active
                      ? "border-blue-400 bg-blue-500/15 ring-1 ring-blue-400"
                      : "border-slate-800 bg-slate-900 hover:border-slate-600"
                  }`}
                >
                  <button
                    aria-current={active ? "page" : undefined}
                    aria-label={t("slideAria", { position: slide.position + 1, title: slide.title, status: t(`status.${slide.status}`) })}
                    className="flex min-w-0 flex-1 items-center gap-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-300"
                    data-slide-id={slide.id}
                    data-testid="authored-rail-slide"
                    disabled={disabled}
                    onClick={() => onSelect(slide.id)}
                    onKeyDown={(event) => handleKeyDown(event, slide)}
                    type="button"
                  >
                    <span className="w-6 shrink-0 text-center text-xs font-semibold tabular-nums text-slate-400">
                      {slide.position + 1}
                    </span>
                    <span className="relative aspect-video w-20 shrink-0 overflow-hidden rounded bg-slate-800">
                      {slide.thumbnailUrl ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img alt="" className="h-full w-full object-cover" src={slide.thumbnailUrl} />
                      ) : (
                        <span aria-hidden="true" className="grid h-full place-items-center text-[10px] text-slate-500">
                          {slide.status === "generating" ? t("status.generating") : t("noPreview")}
                        </span>
                      )}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-xs font-medium">{slide.title}</span>
                      <span className="mt-1 block truncate text-[10px] text-slate-400">
                        {t("revision", { status: t(`status.${slide.status}`), revision: slide.revision })}
                      </span>
                    </span>
                  </button>
                  <span className="flex shrink-0 flex-col justify-center gap-1 opacity-80 group-focus-within:opacity-100 group-hover:opacity-100">
                    <button
                      aria-label={t("moveUp", { title: slide.title })}
                      className="rounded px-1 text-xs text-slate-300 hover:bg-slate-700 disabled:opacity-30"
                      data-slide-id={slide.id}
                      data-testid="authored-rail-move-up"
                      disabled={disabled || slide.position === 0}
                      onClick={() => requestReorder(slide, slide.position - 1)}
                      type="button"
                    >
                      ↑
                    </button>
                    <button
                      aria-label={t("moveDown", { title: slide.title })}
                      className="rounded px-1 text-xs text-slate-300 hover:bg-slate-700 disabled:opacity-30"
                      data-slide-id={slide.id}
                      data-testid="authored-rail-move-down"
                      disabled={disabled || slide.position === slides.length - 1}
                      onClick={() => requestReorder(slide, slide.position + 1)}
                      type="button"
                    >
                      ↓
                    </button>
                  </span>
                </div>
              </li>
            );
          })}
        </ol>
      </div>
      <p aria-live="polite" className="sr-only" role="status">{announcement}</p>
    </nav>
  );
}
