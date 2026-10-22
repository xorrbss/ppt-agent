"use client";

import { useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { isGenerationStalled, minutesSince } from "../../services/api/generation-activity-model";
import { GENERATION_RESUMABLE_STATUSES, isGenerationFailureStatus } from "../workspace/generationFailure";
import GenerationFailurePanel from "./GenerationFailurePanel";
import DraftSlideGrid from "./DraftSlideGrid";
import { deriveDraftTiles, draftImageUrl } from "./draftGridModel";

type DraftPage = {
  index: number; author: string; render: string; persist: string;
  quality: string; imageAvailability: "unconfirmed"; pageVersion: string;
};
type DraftPreview = {
  kind: "checkpoint_draft"; published: false; jobId: string;
  operationState: string; jobState: string; stage: string | null;
  totalPages: number; stopCode: string | null; pages: DraftPage[];
  operationId?: string; stopClass?: string | null;
  lastProgressAt?: string | null;
};
const TERMINAL = new Set(["failed", "dead_letter", "cancelled", "deadline_exceeded", "rejected", "expired"]);
type Translate = ReturnType<typeof useTranslations<"generationActivity">>;
function stageLabel(t: Translate, stage: string): string {
  switch (stage) {
    case "author": return t("draft.stageAuthor");
    case "render": return t("draft.stageRender");
    case "quality": return t("draft.stageQuality");
    case "vqa": return t("draft.stageVqa");
    case "persist": return t("draft.stagePersist");
    default: return t("draft.stageOther");
  }
}
function qualityLabel(t: Translate, quality: string): string {
  switch (quality) {
    case "verified": return t("draft.qualityVerified");
    case "review_pending": return t("draft.qualityReviewPending");
    case "failed": return t("draft.qualityFailed");
    case "passed": return t("draft.qualityPassed");
    case "blocked": return t("draft.qualityBlocked");
    case "draft": return t("draft.qualityDraft");
    default: return t("draft.qualityUnconfirmed");
  }
}

/** Separate from published slides: one selected image, no deck asset preloading. */
export default function DraftCheckpointPreview({ presentationId }: { presentationId: string }) {
  const t = useTranslations("generationActivity");
  const [clockOffsetMs, setClockOffsetMs] = useState(0);
  const [draft, setDraft] = useState<DraftPreview | null>(null);
  const [selected, setSelected] = useState(0);
  const [view, setView] = useState<"grid" | "page">("grid");
  const [refresh, setRefresh] = useState(0);
  const [loadFailed, setLoadFailed] = useState(false);
  const [failedPageKey, setFailedPageKey] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setLoadFailed(false);
    void fetch(`/api/v1/ppt/presentation/${encodeURIComponent(presentationId)}/draft-preview`, {
      credentials: "same-origin", cache: "no-store", signal: controller.signal,
    }).then(async (response) => {
      if (response.status === 404) return null;
      if (!response.ok) throw new Error("draft_preview_unavailable");
      // Server clock offset keeps "N minutes ago" independent of client clock skew.
      const serverDate = Date.parse(response.headers.get("Date") ?? "");
      if (Number.isFinite(serverDate)) setClockOffsetMs(serverDate - Date.now());
      return response.json() as Promise<DraftPreview>;
    }).then((result) => {
      if (controller.signal.aborted) return;
      if (result === null) { setDraft(null); return; }
      if (result?.kind !== "checkpoint_draft" || result.published !== false || !Array.isArray(result.pages)) return;
      setDraft(result);
      setSelected((previous) => result.pages.some((page) => page.index === previous)
        ? previous : result.pages[0]?.index ?? 0);
    }).catch(() => { if (!controller.signal.aborted) setLoadFailed(true); });
    return () => controller.abort();
  }, [presentationId, refresh]);

  useEffect(() => {
    if (!draft || TERMINAL.has(draft.operationState) || TERMINAL.has(draft.jobState) || draft.jobState === "succeeded") return;
    const timer = window.setTimeout(() => setRefresh((value) => value + 1), 10_000);
    return () => window.clearTimeout(timer);
  }, [draft]);

  const tiles = useMemo(() => draft ? deriveDraftTiles(draft.pages, draft.totalPages) : [], [draft]);

  if (!draft || tiles.length === 0) {
    return loadFailed ? <div className="p-4 text-sm text-slate-700" role="alert">
      {t("draft.loadFailed")}
      <Button variant="outline" size="sm" onClick={() => setRefresh((value) => value + 1)}>{t("draft.reload")}</Button>
    </div> : null;
  }
  const page = draft.pages.find((item) => item.index === selected);
  const pageKey = `${draft.jobId}:${selected}:${page?.pageVersion ?? ""}`;
  const stopped = TERMINAL.has(draft.operationState) || TERMINAL.has(draft.jobState);
  const imageReady = page?.render === "completed" && Boolean(page.pageVersion);
  const count = (stage: "author" | "render" | "persist") => draft.pages.filter((item) => item[stage] === "completed").length;
  const imageUrl = draftImageUrl(presentationId, draft.jobId, selected, page?.pageVersion ?? "");
  const nowMs = Date.now() + clockOffsetMs;
  const running = !stopped && draft.jobState !== "succeeded";
  const progressMinutes = running ? minutesSince(draft.lastProgressAt ?? null, nowMs) : null;
  const stalled = running && isGenerationStalled(
    { state: "running", lastProgressAt: draft.lastProgressAt ?? null, startedAt: null }, nowMs);
  const quota = draft.stopCode?.includes("quota") || draft.stopCode?.includes("budget")
    || draft.stopCode === "retry_amplification_ceiling_exceeded";
  // The class message replaces the generic stop text; an unclassified failure keeps the old text but still offers resume.
  const failurePanel = stopped && draft.operationId && isGenerationFailureStatus(draft.operationState)
    ? <GenerationFailurePanel operationId={draft.operationId} failureClass={draft.stopClass}
      fallbackText={quota ? t("draft.stopQuota") : t("draft.stopGeneric")}
      resumable={(GENERATION_RESUMABLE_STATUSES as readonly string[]).includes(draft.operationState)} />
    : null;
  return <section aria-label={t("draft.aria")} className="min-w-0 flex-1 overflow-y-auto border-r border-slate-200 bg-slate-50 p-4">
    <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-slate-800">
      <h2 className="font-semibold">{t("draft.heading")}</h2>
      <p className="mt-1">{t("draft.noteSaved")}</p>
      <p className="mt-1">{t("draft.noteImage")}</p>
      <p className="mt-2" aria-live="polite">{t("draft.counts", { author: count("author"), render: count("render"), persist: count("persist"), total: draft.totalPages })}</p>
      <p className="mt-1">{stopped ? t("draft.stateStopped") : draft.jobState === "succeeded" ? t("draft.stateDone") : t("draft.stateRunning")}{draft.stage ? t("draft.stageSuffix", { stage: stageLabel(t, draft.stage) }) : ""}</p>
      {progressMinutes !== null && <p className="mt-1" data-testid="draft-last-progress">{progressMinutes < 1 ? t("lastProgressJustNow") : t("lastProgress", { minutes: progressMinutes })}</p>}
      {stalled && progressMinutes !== null && <p className="mt-1 font-medium text-amber-900" role="status" data-testid="draft-stalled">{t("stalled", { minutes: progressMinutes })}</p>}
      {stopped && (failurePanel ?? <p className="mt-1">{quota ? t("draft.stopQuota") : t("draft.stopGeneric")}</p>)}
      {loadFailed && <p role="alert" className="mt-1">{t("draft.staleResult")}</p>}
    </div>
    {view === "grid" ? <DraftSlideGrid presentationId={presentationId} jobId={draft.jobId} tiles={tiles}
      stageText={draft.stage ? stageLabel(t, draft.stage) : null} stopped={stopped}
      qualityLabel={(quality) => qualityLabel(t, quality)}
      onOpen={(pageIndex) => { setSelected(pageIndex); setFailedPageKey(null); setView("page"); }} /> : <>
    <div className="mb-3 flex flex-wrap items-center gap-2">
      <Button variant="outline" size="sm" onClick={() => setView("grid")}>{t("draft.backToGrid")}</Button>
      <label className="text-sm" htmlFor="draft-preview-page">{t("draft.pageLabel")}</label>
      <select id="draft-preview-page" value={selected} className="rounded border border-slate-300 bg-white p-2 text-sm"
        onChange={(event) => { setSelected(Number(event.target.value)); setFailedPageKey(null); }}>
        {draft.pages.map((item) => <option key={item.index} value={item.index}>
          {t("draft.pageOption", { page: item.index + 1, total: draft.totalPages })}
        </option>)}
      </select>
      <Button variant="outline" size="sm" onClick={() => setRefresh((value) => value + 1)}>{t("draft.refreshStatus")}</Button>
      <span className="text-xs text-slate-600">{t("draft.pageQuality", { quality: qualityLabel(t, page?.quality ?? "unconfirmed") })}</span>
    </div>
    {!imageReady ? <div role="status" className="p-4 text-sm text-slate-700">
      {stopped ? t("draft.imageStopped", { page: selected + 1 })
        : t("draft.imagePending", { page: selected + 1 })}
    </div>
      : failedPageKey === pageKey ? <div role="alert" className="p-4 text-sm text-red-700">
      <p>{t("draft.imageFailed")}</p>
      <Button variant="outline" size="sm" onClick={() => setFailedPageKey(null)}>{t("draft.imageReload")}</Button>
    </div>
      : <img key={imageUrl} src={imageUrl} alt={t("draft.imageAlt", { page: selected + 1 })} loading="eager" onError={() => setFailedPageKey(pageKey)} className="h-auto w-full bg-white shadow-sm" />}
    </>}
  </section>;
}
