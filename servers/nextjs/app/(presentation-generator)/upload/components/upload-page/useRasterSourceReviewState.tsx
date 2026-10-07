"use client";
import { useTranslations as useA2bTranslations } from "next-intl";
/**
 * Owns all local review state, derived values and save/approve/page-mode
 * actions for RasterSourceReview.tsx: the current page/region selection,
 * editing drafts, per-page and whole-deck "preserve as image" workflows,
 * and the OCR-inventory/approval gates. Extracted from RasterSourceReview.tsx
 * (file-size ratchet split); behaviour unchanged, only the module boundary
 * moved. Returns the exact set of names the render used to destructure
 * locally, so the JSX did not need to change.
 */
import { useEffect, useMemo, useState } from "react";
import {
  rasterReviewImageUrl,
  type RasterAnalysisReview,
  type RasterReviewApprovalPin,
  type RasterReviewPageDetail,
  type RasterReviewSaveRequest,
  type RasterPageModeRequest,
  type RasterRegionKind,
  type RasterPixelBox,
} from "../../../services/api/source-content/raster-contract";

export interface UseRasterSourceReviewStateArgs {
  analysis: RasterAnalysisReview;
  variant: "manual" | "redesign";
  page: RasterReviewPageDetail | null;
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
  onConfirmInventory: (pin: RasterAnalysisReview["pin"]) => Promise<void>;
  onApprove: (pin: RasterAnalysisReview["pin"]) => Promise<RasterReviewApprovalPin>;
}

export function useRasterSourceReviewState({
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
}: UseRasterSourceReviewStateArgs) {
  const tA2b = useA2bTranslations("uploadExtras");
  const [pageIndex, setPageIndex] = useState(analysis.pages[0]?.pageIndex ?? 0);
  const [regionId, setRegionId] = useState<string | null>(null);
  const [textDraft, setTextDraft] = useState("");
  const [kindDraft, setKindDraft] = useState<RasterRegionKind>("unknown");
  const [pixelBoxDraft, setPixelBoxDraft] = useState<RasterPixelBox | null>(null);
  const [loadedImageSize, setLoadedImageSize] = useState<{ url: string; width: number; height: number } | null>(null);
  const [saving, setSaving] = useState(false);
  const [approving, setApproving] = useState(false);
  const [awaitingRevision, setAwaitingRevision] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [approval, setApproval] = useState<RasterReviewApprovalPin | null>(null);
  const [visitedPages, setVisitedPages] = useState<Set<number>>(new Set());
  const [inventoryConfirmed, setInventoryConfirmed] = useState(false);
  const [acknowledgeImageOnly, setAcknowledgeImageOnly] = useState(false);
  const [acknowledgeLegibility, setAcknowledgeLegibility] = useState(false);
  const [bulkSelecting, setBulkSelecting] = useState(false);
  const [bulkProgress, setBulkProgress] = useState<{ selected: number; total: number } | null>(null);
  const [deckStage, setDeckStage] = useState<"select" | "confirm" | null>(null);
  const [deckProgress, setDeckProgress] = useState<{ completed: number; total: number } | null>(null);
  const [deckAcknowledgeImageOnly, setDeckAcknowledgeImageOnly] = useState(false);
  const [deckAcknowledgeLegibility, setDeckAcknowledgeLegibility] = useState(false);
  const [migrating, setMigrating] = useState(false);

  const currentPage = page?.pageIndex === pageIndex ? page : null;
  const selectedRegion = useMemo(
    () => currentPage?.renderMode === "full_page_preserve" ? null :
      currentPage?.regions.find((region) => region.regionId === regionId) ?? null,
    [currentPage, regionId],
  );
  const pageSummary = analysis.pages.find((item) => item.pageIndex === pageIndex);
  const previewUrl = rasterReviewImageUrl(currentPage?.sourcePreviewUrl ?? null);
  const isFullPagePreserve = currentPage?.renderMode === "full_page_preserve";
  const sourcePixelSize = currentPage?.sourcePixelSize ??
    (loadedImageSize?.url === previewUrl ? loadedImageSize : null);

  useEffect(() => {
    void onLoadPage(pageIndex);
  }, [onLoadPage, pageIndex]);

  useEffect(() => {
    if (!currentPage) return;
    setVisitedPages((previous) => new Set(previous).add(currentPage.pageIndex));
    if (currentPage.renderMode === "full_page_preserve") return;
    if (!currentPage.regions.some((region) => region.regionId === regionId)) {
      setRegionId(currentPage.regions.find((region) => region.reviewState !== "verified")?.regionId
        ?? currentPage.regions[0]?.regionId ?? null);
    }
  }, [currentPage, regionId]);

  useEffect(() => {
    setKindDraft(selectedRegion?.kind ?? "unknown");
    setTextDraft(selectedRegion?.confirmedText ?? selectedRegion?.candidateText ?? "");
    setPixelBoxDraft(selectedRegion?.sourcePixelBox ?? null);
    setError(null);
  }, [selectedRegion]);

  useEffect(() => {
    setAwaitingRevision(false);
    setApproval(null);
    setInventoryConfirmed(false);
  }, [analysis.pin.revision, analysis.pin.analysisDigest]);

  useEffect(() => {
    setAcknowledgeImageOnly(currentPage?.fullPageAcknowledgements.includes("image_only") ?? false);
    setAcknowledgeLegibility(currentPage?.fullPageAcknowledgements.includes("legibility") ?? false);
  }, [currentPage?.pageIndex, currentPage?.renderMode, analysis.pin.revision]);

  const editable = Boolean(selectedRegion);
  const hasTextChange = (kindDraft === "title" || kindDraft === "body" || kindDraft === "table") &&
    textDraft !== (selectedRegion?.confirmedText ?? selectedRegion?.candidateText ?? "");
  const hasKindChange = Boolean(selectedRegion && kindDraft !== selectedRegion.kind);
  const hasPixelBoxChange = Boolean(selectedRegion?.sourcePixelBox && pixelBoxDraft &&
    (Object.keys(pixelBoxDraft) as Array<keyof RasterPixelBox>).some((key) =>
      pixelBoxDraft[key] !== selectedRegion.sourcePixelBox?.[key]));
  const pixelBoxValid = !hasPixelBoxChange || Boolean(previewUrl && pixelBoxDraft && sourcePixelSize &&
    Object.values(pixelBoxDraft).every(Number.isInteger) &&
    pixelBoxDraft.x >= 0 && pixelBoxDraft.y >= 0 &&
    pixelBoxDraft.width > 0 && pixelBoxDraft.height > 0 &&
    pixelBoxDraft.x + pixelBoxDraft.width <= sourcePixelSize.width &&
    pixelBoxDraft.y + pixelBoxDraft.height <= sourcePixelSize.height);
  const needsConfirmation = Boolean(selectedRegion && selectedRegion.reviewState !== "verified");
  const hasUnsavedInput = Boolean(hasTextChange || hasKindChange || hasPixelBoxChange);
  const hasChanges = hasUnsavedInput || needsConfirmation;
  const approved = analysis.status === "approved" || Boolean(approval);
  const canApprove = analysis.status === "ready" && analysis.canApprove &&
    analysis.blockingIssueCount === 0 && !hasChanges && !saving &&
    !approving && !awaitingRevision;
  const recommendedPages = analysis.pages.filter((item) =>
    item.renderMode === "reconstruct" && item.recommendedRenderMode === "full_page_preserve");
  const selectedFullPageCount = analysis.pages.filter((item) => item.renderMode === "full_page_preserve").length;
  const confirmedFullPageCount = analysis.pages.filter((item) =>
    item.renderMode === "full_page_preserve" && item.blockingIssueCount === 0).length;
  const remainingPages = analysis.pages.filter((item) => item.blockingIssueCount > 0);
  const nextUnreviewedPage = remainingPages.find((item) => item.pageIndex > pageIndex) ?? remainingPages[0] ?? null;
  const allPagesSelected = analysis.pages.length === analysis.sourcePageCount &&
    selectedFullPageCount === analysis.sourcePageCount;
  const allPagesConfirmed = allPagesSelected && confirmedFullPageCount === analysis.sourcePageCount && analysis.canApprove;

  const save = async () => {
    if (!selectedRegion || !editable || !hasChanges || !pixelBoxValid) return;
    setSaving(true);
    setError(null);
    try {
      await onSaveReview({
        ...analysis.pin,
        pageIndex,
        edits: [{
          regionId: selectedRegion.regionId,
          kind: kindDraft,
          reviewed: !hasPixelBoxChange,
          ...(hasPixelBoxChange && pixelBoxDraft ? { sourcePixelBox: pixelBoxDraft } : {}),
          ...(kindDraft === "title" || kindDraft === "body" || kindDraft === "table" ? { confirmedText: textDraft } : {}),
        }],
      });
      setAwaitingRevision(true);
    } catch {
      setError("검토 내용을 저장하지 못했습니다. 최신 분석을 다시 불러와 확인해 주세요.");
    } finally {
      setSaving(false);
    }
  };

  const approve = async () => {
    if (!canApprove) return;
    setApproving(true);
    setError(null);
    try {
      const pin = await onApprove(analysis.pin);
      if (pin.analysisId !== analysis.pin.analysisId ||
          pin.revision !== analysis.pin.revision ||
          pin.analysisDigest !== analysis.pin.analysisDigest ||
          pin.operationId !== analysis.pin.operationId) {
        throw new Error("raster_approval_pin_mismatch");
      }
      setApproval(pin);
    } catch {
      setError("승인하지 못했습니다. 분석 revision을 다시 확인해 주세요.");
    } finally {
      setApproving(false);
    }
  };

  const confirmInventory = async () => {
    if (!inventoryConfirmed || visitedPages.size < analysis.pages.length || saving || awaitingRevision || hasUnsavedInput) return;
    setSaving(true);
    setError(null);
    try {
      await onConfirmInventory(analysis.pin);
      setAwaitingRevision(true);
    } catch {
      setError("전체 인식 항목을 확인 처리하지 못했습니다. 최신 분석을 다시 불러와 주세요.");
    } finally {
      setSaving(false);
    }
  };

  const visibleRegions = variant === "redesign"
    ? (currentPage?.regions.filter((region) => region.reviewState !== "verified") ?? [])
    : (currentPage?.regions ?? []);
  const cropUrl = rasterReviewImageUrl(selectedRegion?.cropPreviewUrl ?? null);
  const imageRegionsToConfirm = currentPage?.regions.filter((region) =>
    (region.kind === "screenshot" || region.kind === "logo" || region.kind === "table") &&
    region.reviewState === "needs_review" &&
    rasterReviewImageUrl(region.cropPreviewUrl) !== null
  ) ?? [];

  const confirmPageImages = async () => {
    if (!currentPage || !previewUrl || !visitedPages.has(pageIndex) ||
        imageRegionsToConfirm.length === 0 || hasUnsavedInput || saving || awaitingRevision || approved) return;
    setSaving(true);
    setError(null);
    try {
      await onSaveReview({
        ...analysis.pin,
        pageIndex,
        edits: imageRegionsToConfirm.map((region) => ({ regionId: region.regionId, reviewed: true })),
      });
      setAwaitingRevision(true);
    } catch {
      setError("이 페이지의 이미지 영역 확인을 저장하지 못했습니다. 최신 분석을 다시 불러와 주세요.");
    } finally {
      setSaving(false);
    }
  };

  const changePageMode = async (renderMode: RasterPageModeRequest["renderMode"], reviewed: boolean) => {
    if (!currentPage || !previewUrl || !visitedPages.has(pageIndex) || saving || awaitingRevision || approved || hasUnsavedInput) return;
    if (renderMode === "full_page_preserve" && reviewed && (!acknowledgeImageOnly || !acknowledgeLegibility)) return;
    setSaving(true);
    setError(null);
    try {
      await onSetPageMode({
        ...analysis.pin,
        pageIndex,
        renderMode,
        reviewed,
        acknowledgeImageOnly: renderMode === "full_page_preserve" && reviewed && acknowledgeImageOnly,
        acknowledgeLegibility: renderMode === "full_page_preserve" && reviewed && acknowledgeLegibility,
      });
      setAwaitingRevision(true);
    } catch {
      setError("이 페이지의 보존 방식을 저장하지 못했습니다. 최신 분석을 다시 불러와 주세요.");
    } finally {
      setSaving(false);
    }
  };
  const selectRecommendedPages = async () => {
    if (recommendedPages.length === 0 || saving || awaitingRevision || approved || hasUnsavedInput) return;
    const targets = recommendedPages.map((item) => item.pageIndex);
    let selected = 0;
    setSaving(true);
    setBulkSelecting(true);
    setBulkProgress({ selected: 0, total: targets.length });
    setError(null);
    try {
      await onSelectRecommendedPages(analysis.pin, targets, (count, total) => {
        selected = count;
        setBulkProgress({ selected: count, total });
      });
    } catch {
      setError(`권장 페이지 선택이 중단됐습니다. 응답으로 확인된 ${selected}장을 포함해 최신 검토 상태를 다시 확인해 주세요.`);
    } finally {
      setBulkSelecting(false);
      setSaving(false);
    }
  };
  const preserveDeck = async (stage: "select" | "confirm") => {
    if (saving || awaitingRevision || approved || hasUnsavedInput ||
        analysis.pages.length !== analysis.sourcePageCount ||
        (stage === "select" && allPagesSelected) ||
        (stage === "confirm" && (!allPagesSelected || !deckAcknowledgeImageOnly || !deckAcknowledgeLegibility))) return;
    setSaving(true);
    setDeckStage(stage);
    setDeckProgress({ completed: stage === "select" ? selectedFullPageCount : confirmedFullPageCount, total: analysis.sourcePageCount });
    setError(null);
    try {
      await onPreserveDeck(analysis.pin, stage, (completed, total) => setDeckProgress({ completed, total }));
    } catch {
      setError(stage === "select"
        ? "전체 원본 화면 선택이 중단됐습니다. 저장된 페이지 수를 확인한 뒤 다시 선택해 주세요."
        : "전체 이미지 보존 확인이 중단됐습니다. 서버의 남은 검토 항목을 확인한 뒤 다시 시도해 주세요.");
    } finally {
      setDeckStage(null);
      setSaving(false);
    }
  };
  const a4PreviewUrl = rasterReviewImageUrl(currentPage?.fullPageRegion?.cropPreviewUrl ?? null) ?? previewUrl;
  const a4Preview = a4PreviewUrl ? <div className="mx-auto w-full max-w-52 rounded border border-slate-300 bg-white p-1 shadow-sm"
    style={{ aspectRatio: analysis.targetWidth && analysis.targetHeight ? `${analysis.targetWidth} / ${analysis.targetHeight}` : analysis.targetCanvas === "a4-portrait" ? "840 / 1188" : "1188 / 840" }}>
    {/* eslint-disable-next-line @next/next/no-img-element */}
    <img src={a4PreviewUrl} alt={tA2b("a4LayoutProportionPreviewForSlideValue", { value0: pageIndex + 1 })} className="h-full w-full object-contain" />
  </div> : null;

  return {
    pageIndex, setPageIndex,
    regionId, setRegionId,
    textDraft, setTextDraft,
    kindDraft, setKindDraft,
    pixelBoxDraft, setPixelBoxDraft,
    loadedImageSize, setLoadedImageSize,
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
  };
}
