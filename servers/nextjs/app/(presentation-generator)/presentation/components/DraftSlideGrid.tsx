"use client";

import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import {
  countRenderedTiles, draftImageUrl, newestRenderedIndex, renderedPercent,
  type DraftTile,
} from "./draftGridModel";

type Props = {
  presentationId: string;
  jobId: string;
  tiles: DraftTile[];
  /** Already-translated current stage, or null when the job reports none. */
  stageText: string | null;
  stopped: boolean;
  qualityLabel: (quality: string) => string;
  onOpen: (pageIndex: number) => void;
};

function RenderedTileImage({ url, alt, failedText }: { url: string; alt: string; failedText: string }) {
  // Keyed by url at the call site, so a re-rendered page starts unloaded again and fades in.
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);
  if (failed) {
    return <span className="absolute inset-0 flex items-center justify-center p-2 text-center text-xs text-red-700">{failedText}</span>;
  }
  return <img src={url} alt={alt} loading="lazy" decoding="async"
    onLoad={() => setLoaded(true)} onError={() => setFailed(true)}
    className={`absolute inset-0 h-full w-full bg-white object-contain transition-opacity duration-300 motion-reduce:transition-none ${loaded ? "opacity-100" : "opacity-0"}`} />;
}

/** Responsive grid of every page of the deck; finished pages show their saved render. */
export default function DraftSlideGrid({ presentationId, jobId, tiles, stageText, stopped, qualityLabel, onOpen }: Props) {
  const t = useTranslations("generationActivity");
  const rootRef = useRef<HTMLDivElement>(null);
  const userScrolledRef = useRef(false);
  const previousRenderedRef = useRef<number | null>(null);
  const rendered = countRenderedTiles(tiles);
  const percent = renderedPercent(rendered, tiles.length);

  useEffect(() => {
    const scroller = rootRef.current?.parentElement;
    if (!scroller) return;
    const mark = () => { userScrolledRef.current = true; };
    const events = ["wheel", "touchmove", "pointerdown", "keydown"] as const;
    events.forEach((name) => scroller.addEventListener(name, mark, { passive: true }));
    return () => events.forEach((name) => scroller.removeEventListener(name, mark));
  }, []);

  useEffect(() => {
    const previous = previousRenderedRef.current;
    previousRenderedRef.current = rendered;
    // First paint and a user who has taken over the scroll position are never overridden.
    if (previous === null || rendered <= previous || userScrolledRef.current || stopped) return;
    const newest = newestRenderedIndex(tiles);
    if (newest === null) return;
    rootRef.current?.querySelector<HTMLElement>(`[data-tile-index="${newest}"]`)?.scrollIntoView({ block: "nearest" });
  }, [rendered, tiles, stopped]);

  return <div ref={rootRef}>
    <div className="mb-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-base font-semibold text-slate-900" aria-live="polite">
          {t("draft.gridHeading", { rendered, total: tiles.length })}
        </h3>
        {stageText && <span className="text-xs text-slate-600">{stageText}</span>}
      </div>
      <div role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}
        aria-label={t("draft.gridProgressAria")} className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-slate-200">
        <div className="h-full bg-slate-700 transition-all duration-500 motion-reduce:transition-none" style={{ width: `${percent}%` }} />
      </div>
    </div>
    <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-4">
      {tiles.map((tile) => {
        const page = tile.index + 1;
        const url = tile.state === "rendered" ? draftImageUrl(presentationId, jobId, tile.index, tile.pageVersion) : "";
        const showQuality = tile.state === "rendered" && tile.quality !== "unconfirmed";
        return <li key={tile.index}>
          <button type="button" data-tile-index={tile.index} onClick={() => onOpen(tile.index)}
            disabled={tile.state !== "rendered"}
            aria-label={tile.state === "rendered" ? t("draft.tileOpenAria", { page })
              : tile.state === "authored" ? t("draft.tileAuthoredAria", { page }) : t("draft.tilePendingAria", { page })}
            className="relative block aspect-video w-full overflow-hidden rounded-md border border-slate-200 bg-white text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-900 focus-visible:ring-offset-2 enabled:hover:border-slate-400 disabled:cursor-default">
            {tile.state === "rendered"
              ? <RenderedTileImage key={url} url={url} alt={t("draft.imageAlt", { page })} failedText={t("draft.tileImageFailed")} />
              : <span aria-hidden="true" className={`absolute inset-0 flex items-center justify-center text-xs text-slate-500 animate-pulse motion-reduce:animate-none ${tile.state === "authored" ? "bg-slate-200" : "bg-slate-100"}`}>
                {tile.state === "authored" ? t("draft.tileAuthored") : ""}
              </span>}
            <span aria-hidden="true" className="absolute left-1 top-1 rounded bg-slate-900/80 px-1.5 py-0.5 text-[11px] font-medium text-white">{page}</span>
            {showQuality && <span aria-hidden="true" className="absolute bottom-1 right-1 rounded bg-white/90 px-1.5 py-0.5 text-[11px] text-slate-800 shadow-sm">{qualityLabel(tile.quality)}</span>}
          </button>
        </li>;
      })}
    </ul>
  </div>;
}
