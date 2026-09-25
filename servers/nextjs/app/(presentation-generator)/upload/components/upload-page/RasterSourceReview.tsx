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

const REGION_NAMES: Record<RasterReviewRegion["kind"], string> = {
  title: "제목",
  body: "본문",
  screenshot: "화면 캡처",
  table: "표",
  logo: "로고",
  decoration: "장식",
  unknown: "미분류 영역",
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
        <h2 id="raster-review-title" className="text-lg font-semibold text-slate-900">원본 화면과 재구성 내용 검토</h2>
        <p className="text-sm text-slate-600">
          원본 {analysis.sourcePageCount}장 · 출력 {analysis.targetCanvas === "a4-portrait" ? "A4 세로" : "A4 가로"}
        </p>
        <p className="text-sm text-slate-700" role="status">
          {approved ? "승인 완료: 이 분석 revision으로 생성할 수 있습니다." :
            analysis.status === "analyzing" ? "페이지를 분석하는 중입니다." :
            analysis.status === "generating" ? "승인된 내용으로 생성 중입니다." :
            analysis.status === "complete" ? "출력 확인이 완료됐습니다." :
            analysis.status === "ready" ? "모든 필수 검토가 끝났습니다. 분석을 승인할 수 있습니다." :
            `검토 대기: 미해결 항목 ${analysis.blockingIssueCount}개가 있습니다.`}
        </p>
        {analysis.visionReaderUnavailable && <div role="note" className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950" data-testid="raster-vision-unavailable">
          <p className="font-medium">{t("visionUnavailableTitle")}</p>
          <p>{t("visionUnavailable")}</p>
        </div>}
      </header>

      {onAutoPreserveAndGenerate && <div className="space-y-2 rounded-xl border-2 border-blue-300 bg-blue-50 p-4">
        <h3 className="font-semibold text-slate-900">원본 화면 그대로 바로 생성</h3>
        <p className="text-sm text-slate-700">같은 원본에서 새 자동 보존 작업을 시작합니다. 기존 검토와 편집은 그대로 남으며 새 결과에는 반영되지 않습니다. 전체 페이지는 이미지로 보존되어 텍스트를 편집할 수 없고, A4에서 작은 글자는 읽기 어려울 수 있습니다.</p>
        <Button type="button" disabled={migrating || saving || approving} onClick={() => {
          setMigrating(true);
          void onAutoPreserveAndGenerate().finally(() => setMigrating(false));
        }} data-testid="raster-auto-preserve-existing">
          {migrating ? "원본 보존 생성 준비 중…" : "새 자동 원본 보존 작업으로 계속"}
        </Button>
      </div>}

      {variant === "manual" && <section className="space-y-4 rounded-xl border-2 border-blue-300 bg-blue-50 p-4" aria-label="전체 원본 화면 보존">
        <div>
          <h3 className="text-base font-semibold text-slate-900">모든 페이지를 원본 화면 그대로 A4에 보존</h3>
          <p className="mt-1 text-sm text-slate-700">화면 중심 자료에 권장합니다. 아래 페이지를 눌러 원본과 A4 비율을 미리 볼 수 있습니다.</p>
          <p className="mt-2 text-sm font-medium text-slate-900">선택 {selectedFullPageCount}/{analysis.sourcePageCount}장 · 경고 확인 {confirmedFullPageCount}/{analysis.sourcePageCount}장</p>
          <p className="text-xs text-slate-600">미해결 표시 {analysis.blockingIssueCount}건은 서버의 검토 조건 수이며 클릭 횟수가 아닙니다.</p>
        </div>
        <div className="space-y-2 rounded-lg bg-white p-3">
          <p className="text-sm font-semibold text-slate-900">1. 전체 원본 화면 선택</p>
          <p className="text-sm text-slate-700">{analysis.sourcePageCount}장 모두를 각각 원본 이미지 한 장으로 보존하도록 선택합니다. 이 단계에서는 아직 경고 확인이나 생성 승인이 되지 않습니다.</p>
          <Button type="button" onClick={() => void preserveDeck("select")}
            disabled={allPagesSelected || saving || awaitingRevision || approved || hasUnsavedInput || analysis.pages.length !== analysis.sourcePageCount}
            data-testid="raster-select-entire-deck">
            {deckStage === "select" ? `선택 중 ${deckProgress?.completed ?? selectedFullPageCount}/${analysis.sourcePageCount}장` :
              allPagesSelected ? "모든 페이지 선택 완료" : "모든 페이지 원본 화면 보존으로 선택"}
          </Button>
        </div>
        {allPagesSelected && <div className="space-y-3 rounded-lg bg-white p-3">
          <p className="text-sm font-semibold text-slate-900">2. 전체 이미지 보존 경고 확인</p>
          <p className="text-sm text-slate-700">선택한 페이지의 원본과 A4 비율을 아래에서 확인할 수 있습니다. 이 확인은 전체 {analysis.sourcePageCount}장에 적용됩니다.</p>
          <label className="flex items-start gap-2 text-sm text-slate-800">
            <input type="checkbox" className="mt-1" checked={deckAcknowledgeImageOnly} onChange={(event) => setDeckAcknowledgeImageOnly(event.target.checked)} disabled={saving || approved} />
            <span>전체 페이지가 이미지로 보존되며 텍스트를 편집할 수 없음을 이해했습니다.</span>
          </label>
          <label className="flex items-start gap-2 text-sm text-slate-800">
            <input type="checkbox" className="mt-1" checked={deckAcknowledgeLegibility} onChange={(event) => setDeckAcknowledgeLegibility(event.target.checked)} disabled={saving || approved} />
            <span>A4에서 작은 글자가 읽기 어려울 수 있음을 이해했습니다.</span>
          </label>
          <Button type="button" onClick={() => void preserveDeck("confirm")}
            disabled={allPagesConfirmed || !deckAcknowledgeImageOnly || !deckAcknowledgeLegibility || saving || awaitingRevision || approved || hasUnsavedInput}
            data-testid="raster-confirm-entire-deck">
            {deckStage === "confirm" ? `확인 저장 중 ${deckProgress?.completed ?? confirmedFullPageCount}/${analysis.sourcePageCount}장` :
              allPagesConfirmed ? "전체 경고 확인 완료" : `전체 ${analysis.sourcePageCount}장 이미지 보존 경고 확인`}
          </Button>
        </div>}
        {deckProgress && <p className="text-xs text-slate-700" role="status">서버가 확인한 진행 상태 {deckProgress.completed}/{deckProgress.total}장. 중단되면 저장된 상태부터 다시 진행할 수 있습니다.</p>}
        {allPagesConfirmed && <p className="text-sm font-medium text-blue-900">전체 확인이 저장됐습니다. 생성하려면 아래의 별도 승인 버튼을 누르세요.</p>}
      </section>}

      {variant === "manual" && recommendedPages.length > 0 && <details className="rounded-lg border border-slate-200 p-3 text-sm text-slate-700">
        <summary className="cursor-pointer font-medium">고급 옵션 · 권장 페이지만 원본 화면 보존</summary>
        <p className="mt-2">영역별 재구성을 유지할 페이지가 있다면 권장된 {recommendedPages.length}장만 먼저 선택할 수 있습니다. 각 페이지의 확인은 별도로 필요합니다.</p>
        <Button type="button" variant="outline" className="mt-2" onClick={() => void selectRecommendedPages()}
          disabled={saving || awaitingRevision || approved || hasUnsavedInput}
          data-testid="raster-select-recommended-pages">
          {bulkSelecting ? `선택 중 ${bulkProgress?.selected ?? 0}/${bulkProgress?.total ?? recommendedPages.length}장` : `권장 ${recommendedPages.length}장만 선택`}
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
          다음 미검토 페이지{nextUnreviewedPage ? ` · ${nextUnreviewedPage.pageIndex + 1}장` : " 없음"}
        </Button>
      </div>

      <nav aria-label="원본 페이지" className="flex gap-2 overflow-x-auto pb-1">
        {analysis.pages.map((item) => (
          <Button key={item.pageIndex} type="button" size="sm"
            variant={item.pageIndex === pageIndex ? "default" : "outline"}
            aria-current={item.pageIndex === pageIndex ? "page" : undefined}
            disabled={hasUnsavedInput || saving || awaitingRevision}
            onClick={() => { setPageIndex(item.pageIndex); setRegionId(null); setError(null); }}>
            {item.pageIndex + 1}장 {item.renderMode === "full_page_preserve" ? "· 원본 이미지" : ""} {item.blockingIssueCount > 0 ? `· 확인 ${item.blockingIssueCount}` : item.status === "ready" || item.status === "verified" ? "· 확인됨" : ""}
          </Button>
        ))}
      </nav>

      {isLoadingPage || !currentPage ? (
        <p className="rounded-lg bg-slate-50 p-6 text-sm text-slate-600" role="status">페이지 상세를 불러오는 중입니다.</p>
      ) : (
        <div className="grid gap-5 lg:grid-cols-[minmax(0,1.3fr)_minmax(280px,1fr)]">
          <div className="space-y-3">
            <h3 className="text-sm font-semibold text-slate-900">원본 {pageIndex + 1}장{isFullPagePreserve ? " 전체 이미지" : "과 탐지 영역"}</h3>
            {previewUrl ? (
              <div className="relative w-full overflow-hidden rounded-lg border border-slate-200 bg-slate-100"
                style={{ aspectRatio: `${currentPage.sourceWidthUnits} / ${currentPage.sourceHeightUnits}` }}>
                {/* Authenticated, owner-scoped API image; Next image optimization would drop credentials. */}
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={previewUrl} alt={`${pageIndex + 1}장 원본 미리보기`} className="absolute inset-0 h-full w-full object-contain"
                  onLoad={(event) => setLoadedImageSize({
                    url: previewUrl,
                    width: event.currentTarget.naturalWidth,
                    height: event.currentTarget.naturalHeight,
                  })} />
                {!isFullPagePreserve && currentPage.regions.map((region) => {
                  const box = regionPercentBox(region, currentPage);
                  return box ? (
                    <button key={region.regionId} type="button" style={box}
                      aria-label={`${REGION_NAMES[region.kind]} 영역 ${region.regionId}`}
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
            ) : <p className="rounded-lg bg-amber-50 p-4 text-sm text-amber-900">권한이 확인된 원본 미리보기를 사용할 수 없습니다.</p>}
            {!isFullPagePreserve && <p className="text-xs text-slate-600">색상 영역을 선택해 원본과 인식 내용을 비교하세요. 주황색은 검토가 필요합니다.</p>}
            {!isFullPagePreserve && imageRegionsToConfirm.length > 0 && <div className="flex flex-wrap items-center gap-2 rounded-lg border border-blue-200 bg-blue-50 p-3">
              <p className="flex-1 text-sm text-blue-950">원본과 crop을 대조한 뒤 이 페이지의 화면·로고·표 이미지 영역 {imageRegionsToConfirm.length}개를 함께 확인할 수 있습니다.</p>
              <Button type="button" variant="outline" onClick={confirmPageImages}
                disabled={!previewUrl || !visitedPages.has(pageIndex) || hasUnsavedInput || saving || awaitingRevision || approved}
                data-testid="raster-review-confirm-page-images">
                {saving ? "저장 중…" : "원본 이미지 영역 확인 완료"}
              </Button>
            </div>}
            {!isFullPagePreserve && (currentPage.issues.length > 0 || (pageSummary?.issueCount ?? 0) > 0) && (
              <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950">
                <p className="font-medium">이 페이지의 검토 항목 {pageSummary?.issueCount ?? currentPage.issues.length}개</p>
                {currentPage.issues.length > 0 && <ul className="mt-2 list-disc space-y-1 pl-5">{currentPage.issues.map((issue, index) =>
                  <li key={`${issue.code}:${index}`}>
                    <button type="button" className="text-left underline-offset-2 hover:underline" onClick={() => issue.regionId && setRegionId(issue.regionId)}>{issue.code === "page_kept_as_pixels" ? t("pageKeptAsPixels") : issue.message}</button>
                  </li>)}</ul>}
              </div>
            )}
            {!isFullPagePreserve && currentPage.ocrInventory.length > 0 && <details className="rounded-lg border border-slate-200 p-3 text-sm">
              <summary className="cursor-pointer font-medium text-slate-900">원본에서 인식한 문자 {currentPage.ocrInventory.length}개 대조</summary>
              <p className="mt-2 text-xs text-slate-600">후보 문자열입니다. 원본 이미지와 대조해야 확정됩니다.</p>
              <ul className="mt-2 max-h-48 space-y-1 overflow-y-auto">
                {currentPage.ocrInventory.map((item, index) => <li key={index} className={item.reviewRequired ? "text-amber-900" : "text-slate-700"}>
                  {item.text || "(빈 인식 결과)"}{item.reviewRequired ? " · 확인 필요" : ""}
                </li>)}
              </ul>
            </details>}
          </div>

          <div className="space-y-3">
            {isFullPagePreserve ? <div className="space-y-3 rounded-lg border border-blue-200 bg-blue-50/50 p-4" data-testid="raster-full-page-preserve-review">
              <h3 className="font-semibold text-slate-900">원본 화면 그대로 보존</h3>
              <p className="text-sm text-slate-700">이 페이지는 원본 화면 전체를 한 장의 이미지로 A4에 맞춰 배치합니다. 이 페이지의 텍스트는 편집할 수 없습니다.</p>
              <p className="text-sm text-amber-900">A4에 맞추면 화면 안의 작은 글자가 읽기 어려울 수 있습니다. 아래 비율 미리보기와 원본을 확인해 주세요. 실제 출력의 판독성은 별도 확인이 필요합니다.</p>
              {a4Preview}
              <p className="text-xs text-slate-600" role="status">
                {currentPage.fullPageRegion?.reviewState === "verified" ? "이미지 보존과 경고 확인이 저장됐습니다." : "전체 이미지가 생성됐습니다. 아래 두 항목을 확인해야 이 페이지를 승인할 수 있습니다."}
              </p>
              {currentPage.fullPageRegion?.reviewState !== "verified" && <div className="space-y-2">
                <label className="flex items-start gap-2 text-sm text-slate-800">
                  <input type="checkbox" className="mt-1" checked={acknowledgeImageOnly} onChange={(event) => setAcknowledgeImageOnly(event.target.checked)} />
                  <span>이 페이지는 원본 이미지 한 장으로 보존되며 텍스트를 편집할 수 없음을 확인했습니다.</span>
                </label>
                <label className="flex items-start gap-2 text-sm text-slate-800">
                  <input type="checkbox" className="mt-1" checked={acknowledgeLegibility} onChange={(event) => setAcknowledgeLegibility(event.target.checked)} />
                  <span>A4에 맞춘 화면의 글자가 작아질 수 있음을 미리보기와 원본으로 확인했습니다.</span>
                </label>
                <Button type="button" onClick={() => void changePageMode("full_page_preserve", true)}
                  disabled={!previewUrl || !currentPage.fullPageRegion || !acknowledgeImageOnly || !acknowledgeLegibility || saving || awaitingRevision || approved}
                  data-testid="raster-confirm-full-page-preserve">이 페이지 이미지 보존 확인</Button>
              </div>}
              <Button type="button" variant="outline" onClick={() => void changePageMode("reconstruct", false)}
                disabled={saving || awaitingRevision || approved}>영역별 재구성으로 돌아가기</Button>
            </div> : <>
            <div className="space-y-3 rounded-lg border border-slate-200 bg-slate-50 p-3">
              <h3 className="font-semibold text-slate-900">이 페이지의 보존 방식</h3>
              <p className="text-sm text-slate-700">현재는 화면·텍스트·표 영역을 따로 재구성합니다.</p>
              {currentPage.recommendedRenderMode === "full_page_preserve" &&
                <p className="text-sm text-amber-900">영역을 따로 배치하면 A4 범위를 벗어날 수 있어 원본 화면 보존을 권장합니다.</p>}
              <p className="text-sm text-amber-900">원본 화면 그대로 보존하면 A4에서 작은 글자가 읽기 어려울 수 있고 이 페이지의 텍스트를 편집할 수 없습니다.</p>
              {a4Preview}
              <Button type="button" variant="outline" onClick={() => void changePageMode("full_page_preserve", false)}
                disabled={!previewUrl || saving || awaitingRevision || approved || hasUnsavedInput}
                data-testid="raster-select-full-page-preserve">원본 화면 그대로 보존</Button>
            </div>
            <h3 className="text-sm font-semibold text-slate-900">
              {variant === "redesign" ? `검토가 필요한 항목 ${visibleRegions.length}개` : "영역별 확인"}
            </h3>
            {variant === "redesign" && visibleRegions.length === 0 && (
              <p className="text-sm text-emerald-700">이 페이지는 자동으로 확인됐습니다. 추가로 검토할 항목이 없습니다.</p>
            )}
            <div className="max-h-36 overflow-y-auto rounded-lg border border-slate-200 p-2">
              {visibleRegions.map((region) => (
                <button key={region.regionId} type="button" disabled={hasUnsavedInput || saving || awaitingRevision} onClick={() => setRegionId(region.regionId)}
                  className={`block w-full rounded px-2 py-1.5 text-left text-sm ${region.regionId === regionId ? "bg-blue-50 text-blue-900" : "hover:bg-slate-50"}`}>
                  {REGION_NAMES[region.kind]} · {region.reviewState === "verified" ? "확인됨" : region.reviewState === "unmapped" ? "내용 미매핑" : "검토 필요"}
                </button>
              ))}
            </div>
            {selectedRegion ? (
              <div className="space-y-3 rounded-lg border border-slate-200 p-3">
                <p className="font-medium text-slate-900">{REGION_NAMES[selectedRegion.kind]}</p>
                <label className="block text-sm text-slate-800">영역 분류
                  <select value={kindDraft} onChange={(event) => setKindDraft(event.target.value as RasterRegionKind)}
                    className="mt-1 w-full rounded-md border border-slate-300 bg-white p-2 text-sm">
                    {Object.entries(REGION_NAMES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                  </select>
                </label>
                {(selectedRegion.candidateText || selectedRegion.confirmedText) && kindDraft !== "decoration" &&
                  <Button type="button" variant="outline" disabled={saving || awaitingRevision || approved}
                    onClick={() => setKindDraft("decoration")}>보이지 않는 OCR 후보 제외</Button>}
                {selectedRegion.sourcePixelBox && pixelBoxDraft && <fieldset className="space-y-2 rounded-lg border border-slate-200 p-3">
                  <legend className="px-1 text-sm font-medium text-slate-800">원본 이미지 경계 수정</legend>
                  <p className="text-xs text-slate-600">이미지 픽셀 좌표입니다. 저장하면 새 crop이 만들어지며 이 영역을 다시 확인해야 합니다.</p>
                  {sourcePixelSize && <p className="text-xs text-slate-600">원본 이미지 {sourcePixelSize.width} × {sourcePixelSize.height} px</p>}
                  <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                    {([["x", "왼쪽 x"], ["y", "위쪽 y"], ["width", "너비"], ["height", "높이"]] as const).map(([key, label]) =>
                      <label key={key} className="text-xs text-slate-700">{label}
                        <input type="number" inputMode="numeric" step="1" min={key === "width" || key === "height" ? 1 : 0}
                          value={pixelBoxDraft[key]}
                          onChange={(event) => setPixelBoxDraft((previous) => previous ? { ...previous, [key]: Number(event.target.value) } : previous)}
                          className="mt-1 w-full rounded-md border border-slate-300 p-2 text-sm" />
                      </label>)}
                  </div>
                  {!pixelBoxValid && <p className="text-xs text-red-700">경계는 원본 이미지 안의 정수 픽셀로 지정해 주세요.</p>}
                </fieldset>}
                {cropUrl && <div>
                  <p className="mb-1 text-xs text-slate-600">원본 영역 확대</p>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={cropUrl} alt={`${REGION_NAMES[selectedRegion.kind]} 원본 영역`} className="max-h-48 max-w-full rounded border border-slate-200 object-contain" />
                </div>}
                {selectedRegion.issues.length > 0 && <ul className="list-disc space-y-1 pl-5 text-sm text-amber-900">{selectedRegion.issues.map((issue, index) => <li key={`${issue.code}:${index}`}>{issue.message}</li>)}</ul>}
                {(kindDraft === "title" || kindDraft === "body" || kindDraft === "table") && <label className="block text-sm text-slate-800">
                  확인한 원문
                  <textarea value={textDraft} onChange={(event) => setTextDraft(event.target.value)} rows={4}
                    className="mt-1 w-full rounded-md border border-slate-300 p-2 text-sm" aria-label="확인한 원문" />
                </label>}
                {kindDraft === "table" && <p className="text-xs text-slate-600">표는 원본 영역 이미지로 보존합니다. 셀 구조를 추정해 편집 가능한 표로 표시하지 않습니다.</p>}
                {kindDraft === "unknown" && <p className="text-sm text-amber-900">이 영역의 내용 연결이 필요합니다. 분류와 원본 대조가 끝날 때까지 승인할 수 없습니다.</p>}
                {editable && <div className="flex flex-wrap gap-2">
                  <Button type="button" onClick={save} disabled={!hasChanges || !pixelBoxValid || saving || awaitingRevision || approved}>
                    {saving ? "저장 중…" : awaitingRevision ? "새 revision 확인 중…" : hasPixelBoxChange ? "경계 저장·다시 확인" : "영역 확인 저장"}
                  </Button>
                  <Button type="button" variant="outline" disabled={saving || awaitingRevision || !hasUnsavedInput}
                    onClick={() => {
                      setKindDraft(selectedRegion.kind);
                      setTextDraft(selectedRegion.confirmedText ?? selectedRegion.candidateText ?? "");
                      setPixelBoxDraft(selectedRegion.sourcePixelBox);
                    }}>입력 되돌리기</Button>
                </div>}
              </div>
            ) : <p className="text-sm text-slate-600">영역을 선택하세요.</p>}
            </>}
          </div>
        </div>
      )}

      {error && <p role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-800">{error}</p>}
      {analysis.status === "awaiting_review" && analysis.pages.some((item) => item.renderMode === "reconstruct") && <div className="flex flex-wrap items-center gap-3 rounded-lg border border-slate-200 bg-slate-50 p-3">
        <p className="flex-1 text-sm text-slate-700">전체 {analysis.pages.length}장 중 원본을 열어 본 페이지 {visitedPages.size}장. OCR 목록과 미매핑 진단을 원본과 대조한 뒤 확인할 수 있습니다.</p>
        <label className="flex w-full items-start gap-2 text-sm text-slate-700">
          <input type="checkbox" checked={inventoryConfirmed} onChange={(event) => setInventoryConfirmed(event.target.checked)}
            className="mt-1" />
          <span>원본 이미지와 각 페이지의 인식 목록을 직접 대조했고, 미매핑 진단의 처리 근거를 확인했습니다.</span>
        </label>
        <Button type="button" variant="outline" onClick={confirmInventory}
          disabled={!inventoryConfirmed || visitedPages.size < analysis.pages.length || saving || awaitingRevision || hasUnsavedInput}>
          전체 인식 목록 대조 완료
        </Button>
      </div>}
      <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 pt-4">
        <p className="text-xs text-slate-600">부분 검토는 전체 승인으로 처리되지 않습니다. 분석 revision {analysis.pin.revision}</p>
        <Button type="button" onClick={approve} disabled={!canApprove} data-testid="raster-review-approve">
          {approving ? "승인 중…" : approved ? "승인 완료" : "검토 완료 및 생성 승인"}
        </Button>
      </footer>
    </section>
  );
}
