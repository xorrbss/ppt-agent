"use client";

import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import {
  type RasterAnalysisReview,
  type RasterReviewApprovalPin,
  type RasterReviewPageDetail,
  type RasterReviewRegion,
  type RasterReviewSaveRequest,
  type RasterPageModeRequest,
  type RasterRegionKind,
} from "../../../services/api/source-content/raster-contract";
import { useRasterSourceReviewState } from "./useRasterSourceReviewState";

type Props = {
  analysis: RasterAnalysisReview;
  /** "redesign" (auto_redesign default): only flagged low-confidence regions show, and the bulk picture-preserve workflow stays hidden behind the fallback banner. "manual": today's full reconstruct review. */
  variant?: "manual" | "redesign";
  page: RasterReviewPageDetail | null;
  isLoadingPage?: boolean;
  onLoadPage: (pageIndex: number) => void | Promise<void>;
  onSaveReview: (request: RasterReviewSaveRequest) => Promise<void>;
  onSetPageMode: (request: RasterPageModeRequest) => Promise<void>;
  onSelectRecommendedPages: (
    pin: RasterAnalysisReview["pin"],
    pageIndexes: number[],
    onProgress: (selected: number, total: number) => void,
  ) => Promise<void>;
  onPreserveDeck: (
    pin: RasterAnalysisReview["pin"],
    stage: "select" | "confirm",
    onProgress: (completed: number, total: number) => void,
  ) => Promise<void>;
  onAutoPreserveAndGenerate?: () => Promise<void>;
  onConfirmInventory: (pin: RasterAnalysisReview["pin"]) => Promise<void>;
  onApprove: (pin: RasterAnalysisReview["pin"]) => Promise<RasterReviewApprovalPin>;
};

function regionPercentBox(
  region: RasterReviewRegion,
  page: RasterReviewPageDetail,
) {
  const { x, y, width, height } = region.sourceBox;
  const pw = page.sourceWidthUnits;
  const ph = page.sourceHeightUnits;
  if (
    ![x, y, width, height, pw, ph].every(Number.isFinite) ||
    pw <= 0 || ph <= 0 || width <= 0 || height <= 0
  ) return null;
  const left = Math.max(0, Math.min(100, (x / pw) * 100));
  const top = Math.max(0, Math.min(100, (y / ph) * 100));
  const right = Math.max(left, Math.min(100, ((x + width) / pw) * 100));
  const bottom = Math.max(top, Math.min(100, ((y + height) / ph) * 100));
  return {
    left: `${left}%`,
    top: `${top}%`,
    width: `${right - left}%`,
    height: `${bottom - top}%`,
  };
}

export default function RasterSourceReview({
  analysis,
  variant = "manual",
  page,
  isLoadingPage = false,
  onLoadPage,
  onSaveReview,
  onSetPageMode,
  onSelectRecommendedPages,
  onPreserveDeck,
  onAutoPreserveAndGenerate,
  onConfirmInventory,
  onApprove,
}: Props) {
  const t = useTranslations("upload.rasterReview");
  const REGION_NAMES: Record<RasterReviewRegion["kind"], string> = {
    title: t("regionTitle"),
    body: t("regionBody"),
    screenshot: t("regionScreenshot"),
    table: t("regionTable"),
    logo: t("regionLogo"),
    decoration: t("regionDecoration"),
    unknown: t("regionUnknown"),
  };
  const {
    pageIndex, setPageIndex,
    regionId, setRegionId,
    textDraft, setTextDraft,
    kindDraft, setKindDraft,
    pixelBoxDraft, setPixelBoxDraft,
    setLoadedImageSize,
    saving,
    approving,
    awaitingRevision,
    error, setError,
    visitedPages,
    inventoryConfirmed, setInventoryConfirmed,
    acknowledgeImageOnly, setAcknowledgeImageOnly,
    acknowledgeLegibility, setAcknowledgeLegibility,
    bulkSelecting,
    bulkProgress,
    deckStage,
    deckProgress,
    deckAcknowledgeImageOnly, setDeckAcknowledgeImageOnly,
    deckAcknowledgeLegibility, setDeckAcknowledgeLegibility,
    migrating, setMigrating,
    currentPage,
    selectedRegion,
    pageSummary,
    previewUrl,
    isFullPagePreserve,
    sourcePixelSize,
    editable,
    hasPixelBoxChange,
    pixelBoxValid,
    hasUnsavedInput,
    hasChanges,
    approved,
    canApprove,
    recommendedPages,
    selectedFullPageCount,
    confirmedFullPageCount,
    nextUnreviewedPage,
    allPagesSelected,
    allPagesConfirmed,
    save,
    approve,
    confirmInventory,
    visibleRegions,
    cropUrl,
    imageRegionsToConfirm,
    confirmPageImages,
    changePageMode,
    selectRecommendedPages,
    preserveDeck,
    a4Preview,
  } = useRasterSourceReviewState({
    analysis,
    variant,
    page,
    onLoadPage,
    onSaveReview,
    onSetPageMode,
    onSelectRecommendedPages,
    onPreserveDeck,
    onConfirmInventory,
    onApprove,
  });

  return (
    <section className="space-y-4 rounded-2xl border border-slate-200 bg-white p-4 md:p-6" aria-labelledby="raster-review-title" data-testid="raster-source-review">
      <header className="space-y-1">
        <h2 id="raster-review-title" className="text-lg font-semibold text-slate-900">{t("reviewOriginalScreensAndReconstructedContent")}</h2>
        <p className="text-sm text-slate-600">
          {t("source")} {analysis.sourcePageCount}{t("slidesOutput")} {analysis.targetCanvas === "svg" ? `${analysis.targetWidth} × ${analysis.targetHeight}` : analysis.targetCanvas === "a4-portrait" ? t("a4Portrait") : t("a4Landscape")}
        </p>
        <p className="text-sm text-slate-700" role="status">
          {approved ? t("approvedYouCanGenerateFromThisAnalysis") :
            analysis.status === "analyzing" ? t("analyzingPages") :
            analysis.status === "generating" ? t("generatingFromApprovedContent") :
            analysis.status === "complete" ? t("outputReviewIsComplete") :
            analysis.status === "ready" ? t("allRequiredReviewsAreCompleteYouCan") :
            t("reviewPendingValueUnresolvedItemsRemain", { value0: analysis.blockingIssueCount })}
        </p>
        {analysis.visionReaderUnavailable && <div role="note" className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950" data-testid="raster-vision-unavailable">
          <p className="font-medium">{t("visionUnavailableTitle")}</p>
          <p>{t("visionUnavailable")}</p>
        </div>}
      </header>

      {onAutoPreserveAndGenerate && <div className="space-y-2 rounded-xl border-2 border-blue-300 bg-blue-50 p-4">
        <h3 className="font-semibold text-slate-900">{t("generateDirectlyFromOriginalScreens")}</h3>
        <p className="text-sm text-slate-700">{t("startANewAutomaticPreservationOperationFrom")}</p>
        <Button type="button" disabled={migrating || saving || approving} onClick={() => {
          setMigrating(true);
          void onAutoPreserveAndGenerate().finally(() => setMigrating(false));
        }} data-testid="raster-auto-preserve-existing">
          {migrating ? t("preparingSourcePreservingGeneration") : t("continueWithANewAutomaticSourcePreserving")}
        </Button>
      </div>}

      {variant === "manual" && <section className="space-y-4 rounded-xl border-2 border-blue-300 bg-blue-50 p-4" aria-label={t("preserveEntireOriginalScreen")}>
        <div>
          <h3 className="text-base font-semibold text-slate-900">{t("preserveEveryPageOnA4AsIts")}</h3>
          <p className="mt-1 text-sm text-slate-700">{t("recommendedForScreenHeavyMaterialSelectA")}</p>
          <p className="mt-2 text-sm font-medium text-slate-900">{t("selected")} {selectedFullPageCount}/{analysis.sourcePageCount}{t("slidesWarningsConfirmed")} {confirmedFullPageCount}/{analysis.sourcePageCount}{t("slides")}</p>
          <p className="text-xs text-slate-600">{t("unresolvedFlags")} {analysis.blockingIssueCount}{t("areServerReviewConditionsNotTheNumber")}</p>
        </div>
        <div className="space-y-2 rounded-lg bg-white p-3">
          <p className="text-sm font-semibold text-slate-900">{t("label24")}</p>
          <p className="text-sm text-slate-700">{analysis.sourcePageCount}{t("selectAllPagesToPreserveEachAs")}</p>
          <Button type="button" onClick={() => void preserveDeck("select")}
            disabled={allPagesSelected || saving || awaitingRevision || approved || hasUnsavedInput || analysis.pages.length !== analysis.sourcePageCount}
            data-testid="raster-select-entire-deck">
            {deckStage === "select" ? t("selectingValueValueSlides", { value0: deckProgress?.completed ?? selectedFullPageCount, value1: analysis.sourcePageCount }) :
              allPagesSelected ? t("allPagesSelected") : t("selectAllPagesForOriginalScreenPreservation")}
          </Button>
        </div>
        {allPagesSelected && <div className="space-y-3 rounded-lg bg-white p-3">
          <p className="text-sm font-semibold text-slate-900">{t("label29")}</p>
          <p className="text-sm text-slate-700">{t("reviewTheSourceAndA4ProportionsOf")} {analysis.sourcePageCount}{t("slides2")}</p>
          <label className="flex items-start gap-2 text-sm text-slate-800">
            <input type="checkbox" className="mt-1" checked={deckAcknowledgeImageOnly} onChange={(event) => setDeckAcknowledgeImageOnly(event.target.checked)} disabled={saving || approved} />
            <span>{t("iUnderstandThatEntirePagesArePreserved")}</span>
          </label>
          <label className="flex items-start gap-2 text-sm text-slate-800">
            <input type="checkbox" className="mt-1" checked={deckAcknowledgeLegibility} onChange={(event) => setDeckAcknowledgeLegibility(event.target.checked)} disabled={saving || approved} />
            <span>{t("iUnderstandThatSmallTextMayBe")}</span>
          </label>
          <Button type="button" onClick={() => void preserveDeck("confirm")}
            disabled={allPagesConfirmed || !deckAcknowledgeImageOnly || !deckAcknowledgeLegibility || saving || awaitingRevision || approved || hasUnsavedInput}
            data-testid="raster-confirm-entire-deck">
            {deckStage === "confirm" ? t("savingConfirmationsForValueValueSlides", { value0: deckProgress?.completed ?? confirmedFullPageCount, value1: analysis.sourcePageCount }) :
              allPagesConfirmed ? t("allWarningsConfirmed") : t("confirmImagePreservationWarningsForAllValue", { value0: analysis.sourcePageCount })}
          </Button>
        </div>}
        {deckProgress && <p className="text-xs text-slate-700" role="status">{t("serverConfirmedProgress")} {deckProgress.completed}/{deckProgress.total}{t("slidesIfInterruptedYouCanResumeFrom")}</p>}
        {allPagesConfirmed && <p className="text-sm font-medium text-blue-900">{t("allConfirmationsWereSavedUseTheSeparate")}</p>}
      </section>}

      {variant === "manual" && recommendedPages.length > 0 && <details className="rounded-lg border border-slate-200 p-3 text-sm text-slate-700">
        <summary className="cursor-pointer font-medium">{t("advancedOptionPreserveOnlyRecommendedPagesAs")}</summary>
        <p className="mt-2">{t("ifSomePagesShouldKeepRegionBased")} {recommendedPages.length}{t("slidesFirstEachPageMustBeConfirmed")}</p>
        <Button type="button" variant="outline" className="mt-2" onClick={() => void selectRecommendedPages()}
          disabled={saving || awaitingRevision || approved || hasUnsavedInput}
          data-testid="raster-select-recommended-pages">
          {bulkSelecting ? t("selectingValueValueSlides", { value0: bulkProgress?.selected ?? 0, value1: bulkProgress?.total ?? recommendedPages.length }) : t("selectOnlyValueRecommendedSlides", { value0: recommendedPages.length })}
        </Button>
      </details>}

      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" variant="outline" size="sm"
          disabled={!nextUnreviewedPage || nextUnreviewedPage.pageIndex === pageIndex || hasUnsavedInput || saving || awaitingRevision}
          onClick={() => {
            if (!nextUnreviewedPage) return;
            setPageIndex(nextUnreviewedPage.pageIndex);
            setRegionId(null);
            setError(null);
          }}
          data-testid="raster-next-unreviewed-page">
          {t("nextUnreviewedPage")}{nextUnreviewedPage ? t("slideValue", { value0: nextUnreviewedPage.pageIndex + 1 }) : t("none")}
        </Button>
      </div>

      <nav aria-label={t("sourcePage")} className="flex gap-2 overflow-x-auto pb-1">
        {analysis.pages.map((item) => (
          <Button key={item.pageIndex} type="button" size="sm"
            variant={item.pageIndex === pageIndex ? "default" : "outline"}
            aria-current={item.pageIndex === pageIndex ? "page" : undefined}
            disabled={hasUnsavedInput || saving || awaitingRevision}
            onClick={() => { setPageIndex(item.pageIndex); setRegionId(null); setError(null); }}>
            {item.pageIndex + 1}{t("slides")} {item.renderMode === "full_page_preserve" ? t("originalImage") : ""} {item.blockingIssueCount > 0 ? t("valueConfirmations", { value0: item.blockingIssueCount }) : item.status === "ready" || item.status === "verified" ? t("confirmed") : ""}
          </Button>
        ))}
      </nav>

      {isLoadingPage || !currentPage ? (
        <p className="rounded-lg bg-slate-50 p-6 text-sm text-slate-600" role="status">{t("loadingPageDetails")}</p>
      ) : (
        <div className="grid gap-5 lg:grid-cols-[minmax(0,1.3fr)_minmax(280px,1fr)]">
          <div className="space-y-3">
            <h3 className="text-sm font-semibold text-slate-900">{t("source")} {pageIndex + 1}{t("slides")}{isFullPagePreserve ? t("wholeImage") : t("andDetectedRegions")}</h3>
            {previewUrl ? (
              <div className="relative w-full overflow-hidden rounded-lg border border-slate-200 bg-slate-100"
                style={{ aspectRatio: `${currentPage.sourceWidthUnits} / ${currentPage.sourceHeightUnits}` }}>
                {/* Authenticated, owner-scoped API image; Next image optimization would drop credentials. */}
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={previewUrl} alt={t("sourcePreviewForSlideValue", { value0: pageIndex + 1 })} className="absolute inset-0 h-full w-full object-contain"
                  onLoad={(event) => setLoadedImageSize({
                    url: previewUrl,
                    width: event.currentTarget.naturalWidth,
                    height: event.currentTarget.naturalHeight,
                  })} />
                {!isFullPagePreserve && currentPage.regions.map((region) => {
                  const box = regionPercentBox(region, currentPage);
                  return box ? (
                    <button key={region.regionId} type="button" style={box}
                      aria-label={t("valueRegionValue", { value0: REGION_NAMES[region.kind], value1: region.regionId })}
                      aria-pressed={region.regionId === regionId}
                      disabled={hasUnsavedInput || saving || awaitingRevision}
                      onClick={() => setRegionId(region.regionId)}
                      className={`absolute border-2 transition-colors ${region.regionId === regionId ? "z-20 border-blue-600 bg-blue-500/15" : region.reviewState === "verified" ? "z-10 border-emerald-600 bg-emerald-500/10 hover:bg-emerald-500/20" : "z-10 border-amber-600 bg-amber-500/15 hover:bg-amber-500/25"}`} />
                  ) : null;
                })}
                {!isFullPagePreserve && hasPixelBoxChange && pixelBoxValid && pixelBoxDraft && sourcePixelSize &&
                  <div aria-hidden="true" className="pointer-events-none absolute z-30 border-2 border-dashed border-fuchsia-600 bg-fuchsia-500/10"
                    style={{
                      left: `${pixelBoxDraft.x / sourcePixelSize.width * 100}%`,
                      top: `${pixelBoxDraft.y / sourcePixelSize.height * 100}%`,
                      width: `${pixelBoxDraft.width / sourcePixelSize.width * 100}%`,
                      height: `${pixelBoxDraft.height / sourcePixelSize.height * 100}%`,
                    }} />}
              </div>
            ) : <p className="rounded-lg bg-amber-50 p-4 text-sm text-amber-900">{t("anAuthorizedSourcePreviewIsUnavailable")}</p>}
            {!isFullPagePreserve && <p className="text-xs text-slate-600">{t("selectAColoredRegionToCompareThe")}</p>}
            {!isFullPagePreserve && imageRegionsToConfirm.length > 0 && <div className="flex flex-wrap items-center gap-2 rounded-lg border border-blue-200 bg-blue-50 p-3">
              <p className="flex-1 text-sm text-blue-950">{t("afterComparingTheSourceAndCropYou")} {imageRegionsToConfirm.length}{t("together")}</p>
              <Button type="button" variant="outline" onClick={confirmPageImages}
                disabled={!previewUrl || !visitedPages.has(pageIndex) || hasUnsavedInput || saving || awaitingRevision || approved}
                data-testid="raster-review-confirm-page-images">
                {saving ? t("saving") : t("sourceImageRegionsConfirmed")}
              </Button>
            </div>}
            {!isFullPagePreserve && (currentPage.issues.length > 0 || (pageSummary?.issueCount ?? 0) > 0) && (
              <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950">
                <p className="font-medium">{t("reviewItemsOnThisPage")} {pageSummary?.issueCount ?? currentPage.issues.length}{t("items")}</p>
                {currentPage.issues.length > 0 && <ul className="mt-2 list-disc space-y-1 pl-5">{currentPage.issues.map((issue, index) =>
                  <li key={`${issue.code}:${index}`}>
                    <button type="button" className="text-left underline-offset-2 hover:underline" onClick={() => issue.regionId && setRegionId(issue.regionId)}>{issue.code === "page_kept_as_pixels" ? t("pageKeptAsPixels") : issue.message}</button>
                  </li>)}</ul>}
              </div>
            )}
            {!isFullPagePreserve && currentPage.ocrInventory.length > 0 && <details className="rounded-lg border border-slate-200 p-3 text-sm">
              <summary className="cursor-pointer font-medium text-slate-900">{t("textRecognizedFromTheSource")} {currentPage.ocrInventory.length}{t("itemsToCompare")}</summary>
              <p className="mt-2 text-xs text-slate-600">{t("theseAreCandidateStringsCompareThemWith")}</p>
              <ul className="mt-2 max-h-48 space-y-1 overflow-y-auto">
                {currentPage.ocrInventory.map((item, index) => <li key={index} className={item.reviewRequired ? "text-amber-900" : "text-slate-700"}>
                  {item.text || t("noRecognizedText")}{item.reviewRequired ? t("confirmationNeeded") : ""}
                </li>)}
              </ul>
            </details>}
          </div>

          <div className="space-y-3">
            {isFullPagePreserve ? <div className="space-y-3 rounded-lg border border-blue-200 bg-blue-50/50 p-4" data-testid="raster-full-page-preserve-review">
              <h3 className="font-semibold text-slate-900">{t("preserveOriginalScreenAsIs")}</h3>
              <p className="text-sm text-slate-700">{t("theWholeSourceScreenIsPlacedOn")}</p>
              <p className="text-sm text-amber-900">{t("fittingTheScreenToA4MayMake")}</p>
              {a4Preview}
              <p className="text-xs text-slate-600" role="status">
                {currentPage.fullPageRegion?.reviewState === "verified" ? t("imagePreservationAndWarningConfirmationsWereSaved") : t("theWholeImageWasCreatedConfirmBoth")}
              </p>
              {currentPage.fullPageRegion?.reviewState !== "verified" && <div className="space-y-2">
                <label className="flex items-start gap-2 text-sm text-slate-800">
                  <input type="checkbox" className="mt-1" checked={acknowledgeImageOnly} onChange={(event) => setAcknowledgeImageOnly(event.target.checked)} />
                  <span>{t("iConfirmThatThisPageIsPreserved")}</span>
                </label>
                <label className="flex items-start gap-2 text-sm text-slate-800">
                  <input type="checkbox" className="mt-1" checked={acknowledgeLegibility} onChange={(event) => setAcknowledgeLegibility(event.target.checked)} />
                  <span>{t("iCheckedThePreviewAndSourceAnd")}</span>
                </label>
                <Button type="button" onClick={() => void changePageMode("full_page_preserve", true)}
                  disabled={!previewUrl || !currentPage.fullPageRegion || !acknowledgeImageOnly || !acknowledgeLegibility || saving || awaitingRevision || approved}
                  data-testid="raster-confirm-full-page-preserve">{t("confirmImagePreservationForThisPage")}</Button>
              </div>}
              <Button type="button" variant="outline" onClick={() => void changePageMode("reconstruct", false)}
                disabled={saving || awaitingRevision || approved}>{t("returnToRegionBasedReconstruction")}</Button>
            </div> : <>
            <div className="space-y-3 rounded-lg border border-slate-200 bg-slate-50 p-3">
              <h3 className="font-semibold text-slate-900">{t("preservationModeForThisPage")}</h3>
              <p className="text-sm text-slate-700">{t("screenshotTextAndTableRegionsAreCurrently")}</p>
              {currentPage.recommendedRenderMode === "full_page_preserve" &&
                <p className="text-sm text-amber-900">{t("placingRegionsSeparatelyMayExceedTheA4")}</p>}
              <p className="text-sm text-amber-900">{t("preservingTheOriginalScreenMayMakeSmall")}</p>
              {a4Preview}
              <Button type="button" variant="outline" onClick={() => void changePageMode("full_page_preserve", false)}
                disabled={!previewUrl || saving || awaitingRevision || approved || hasUnsavedInput}
                data-testid="raster-select-full-page-preserve">{t("preserveOriginalScreenAsIs")}</Button>
            </div>
            <h3 className="text-sm font-semibold text-slate-900">
              {variant === "redesign" ? t("valueItemsNeedReview", { value0: visibleRegions.length }) : t("reviewByRegion")}
            </h3>
            {variant === "redesign" && visibleRegions.length === 0 && (
              <p className="text-sm text-emerald-700">{t("thisPageWasConfirmedAutomaticallyThereAre")}</p>
            )}
            <div className="max-h-36 overflow-y-auto rounded-lg border border-slate-200 p-2">
              {visibleRegions.map((region) => (
                <button key={region.regionId} type="button" disabled={hasUnsavedInput || saving || awaitingRevision} onClick={() => setRegionId(region.regionId)}
                  className={`block w-full rounded px-2 py-1.5 text-left text-sm ${region.regionId === regionId ? "bg-blue-50 text-blue-900" : "hover:bg-slate-50"}`}>
                  {REGION_NAMES[region.kind]} · {region.reviewState === "verified" ? t("confirmed2") : region.reviewState === "unmapped" ? t("contentUnmapped") : t("reviewNeeded")}
                </button>
              ))}
            </div>
            {selectedRegion ? (
              <div className="space-y-3 rounded-lg border border-slate-200 p-3">
                <p className="font-medium text-slate-900">{REGION_NAMES[selectedRegion.kind]}</p>
                <label className="block text-sm text-slate-800">{t("regionClassification")}
                  <select value={kindDraft} onChange={(event) => setKindDraft(event.target.value as RasterRegionKind)}
                    className="mt-1 w-full rounded-md border border-slate-300 bg-white p-2 text-sm">
                    {Object.entries(REGION_NAMES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                  </select>
                </label>
                {(selectedRegion.candidateText || selectedRegion.confirmedText) && kindDraft !== "decoration" &&
                  <Button type="button" variant="outline" disabled={saving || awaitingRevision || approved}
                    onClick={() => setKindDraft("decoration")}>{t("excludeInvisibleOcrCandidate")}</Button>}
                {selectedRegion.sourcePixelBox && pixelBoxDraft && <fieldset className="space-y-2 rounded-lg border border-slate-200 p-3">
                  <legend className="px-1 text-sm font-medium text-slate-800">{t("adjustSourceImageBounds")}</legend>
                  <p className="text-xs text-slate-600">{t("theseAreImagePixelCoordinatesSavingCreates")}</p>
                  {sourcePixelSize && <p className="text-xs text-slate-600">{t("sourceImage")} {sourcePixelSize.width} × {sourcePixelSize.height} {t("px")}</p>}
                  <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                    {([["x", "왼쪽 x"], ["y", "위쪽 y"], ["width", "너비"], ["height", "높이"]] as const).map(([key, label]) =>
                      <label key={key} className="text-xs text-slate-700">{label}
                        <input type="number" inputMode="numeric" step="1" min={key === "width" || key === "height" ? 1 : 0}
                          value={pixelBoxDraft[key]}
                          onChange={(event) => setPixelBoxDraft((previous) => previous ? { ...previous, [key]: Number(event.target.value) } : previous)}
                          className="mt-1 w-full rounded-md border border-slate-300 p-2 text-sm" />
                      </label>)}
                  </div>
                  {!pixelBoxValid && <p className="text-xs text-red-700">{t("setBoundsToIntegerPixelsInsideThe")}</p>}
                </fieldset>}
                {cropUrl && <div>
                  <p className="mb-1 text-xs text-slate-600">{t("zoomSourceRegion")}</p>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={cropUrl} alt={t("valueSourceRegion", { value0: REGION_NAMES[selectedRegion.kind] })} className="max-h-48 max-w-full rounded border border-slate-200 object-contain" />
                </div>}
                {selectedRegion.issues.length > 0 && <ul className="list-disc space-y-1 pl-5 text-sm text-amber-900">{selectedRegion.issues.map((issue, index) => <li key={`${issue.code}:${index}`}>{issue.message}</li>)}</ul>}
                {(kindDraft === "title" || kindDraft === "body" || kindDraft === "table") && <label className="block text-sm text-slate-800">
                  {t("confirmedSourceText")}
                  <textarea value={textDraft} onChange={(event) => setTextDraft(event.target.value)} rows={4}
                    className="mt-1 w-full rounded-md border border-slate-300 p-2 text-sm" aria-label={t("confirmedSourceText")} />
                </label>}
                {kindDraft === "table" && <p className="text-xs text-slate-600">{t("tablesArePreservedAsSourceRegionImages")}</p>}
                {kindDraft === "unknown" && <p className="text-sm text-amber-900">{t("contentMappingIsRequiredForThisRegion")}</p>}
                {editable && <div className="flex flex-wrap gap-2">
                  <Button type="button" onClick={save} disabled={!hasChanges || !pixelBoxValid || saving || awaitingRevision || approved}>
                    {saving ? t("saving") : awaitingRevision ? t("checkingNewRevision") : hasPixelBoxChange ? t("saveBoundsAndReconfirm") : t("saveRegionConfirmation")}
                  </Button>
                  <Button type="button" variant="outline" disabled={saving || awaitingRevision || !hasUnsavedInput}
                    onClick={() => {
                      setKindDraft(selectedRegion.kind);
                      setTextDraft(selectedRegion.confirmedText ?? selectedRegion.candidateText ?? "");
                      setPixelBoxDraft(selectedRegion.sourcePixelBox);
                    }}>{t("undoInput")}</Button>
                </div>}
              </div>
            ) : <p className="text-sm text-slate-600">{t("selectARegion")}</p>}
            </>}
          </div>
        </div>
      )}

      {error && <p role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-800">{error}</p>}
      {analysis.status === "awaiting_review" && analysis.pages.some((item) => item.renderMode === "reconstruct") && <div className="flex flex-wrap items-center gap-3 rounded-lg border border-slate-200 bg-slate-50 p-3">
        <p className="flex-1 text-sm text-slate-700">{t("all")} {analysis.pages.length}{t("pagesWithSourceOpenedOutOf")} {visitedPages.size}{t("pagesCompareTheOcrListAndUnmapped")}</p>
        <label className="flex w-full items-start gap-2 text-sm text-slate-700">
          <input type="checkbox" checked={inventoryConfirmed} onChange={(event) => setInventoryConfirmed(event.target.checked)}
            className="mt-1" />
          <span>{t("iDirectlyComparedTheSourceImageWith")}</span>
        </label>
        <Button type="button" variant="outline" onClick={confirmInventory}
          disabled={!inventoryConfirmed || visitedPages.size < analysis.pages.length || saving || awaitingRevision || hasUnsavedInput}>
          {t("fullRecognitionListComparisonComplete")}
        </Button>
      </div>}
      <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 pt-4">
        <p className="text-xs text-slate-600">{t("partialReviewIsNotTreatedAsFull")} {analysis.pin.revision}</p>
        <Button type="button" onClick={approve} disabled={!canApprove} data-testid="raster-review-approve">
          {approving ? t("approving") : approved ? t("approved") : t("completeReviewAndApproveGeneration")}
        </Button>
      </footer>
    </section>
  );
}
