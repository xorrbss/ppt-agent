/**
 * Upload entry experience: a centered creation composer followed by a
 * container-responsive template catalog. Generation behavior remains owned by
 * useUploadPageState; this component only reorganizes the interaction surface.
 */

"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowUp, LayoutTemplate } from "lucide-react";
import { useTranslations } from "next-intl";
import { useDispatch, useSelector } from "react-redux";

import Wrapper from "@/components/Wrapper";
import { Button } from "@/components/ui/button";
import { notify } from "@/components/ui/sonner";
import { useUploadLimits } from "@/lib/use-upload-limits";
import { cn } from "@/lib/utils";
import {
  setPptGenUploadState,
  setSourceContentMode,
} from "@/store/slices/presentationGenUpload";
import type { RootState } from "@/store/store";
import { isAuthoredVnextMasterEnabled } from "@/lib/authored-vnext/master/contracts";
import type { MasterRevisionEnvelope } from "@/lib/authored-vnext/master/contracts";
import { AuthoredVnextMasterClient } from "@/lib/authored-vnext/master/client";
import {
  clearLastUsedMasterId,
  masterSelectionFromPublished,
} from "@/lib/authored-vnext/master/selection-storage";

import TemplateSelection from "../../outline/components/TemplateSelection";
import MasterCatalogDialog from "../../authored-vnext/master-setup/MasterCatalogDialog";
import type { MasterCatalogEntryMode } from "../../authored-vnext/master-setup/MasterCatalogDialog";
import { ConfigurationSelects } from "./ConfigurationSelects";
import { PromptInput } from "./PromptInput";
import SupportingDoc, { MAX_SUPPORTED_FILES } from "./SupportingDoc";
import { abortDocumentProcessing } from "./upload-page/abortDocumentProcessing";
import { useSupportingFiles } from "./upload-page/useSupportingFiles";
import SourceContentLossDialog from "./upload-page/SourceContentLossDialog";
import RasterSourceReview from "./upload-page/RasterSourceReview";
import { useUploadPageState } from "./upload-page/useUploadPageState";
import UploadDesignGallery from "./UploadDesignGallery";
import { GenerationPreparation } from "../../generation-workspace/GenerationPreparation";
import { PreviousAttemptNotice } from "./upload-page/PreviousAttemptNotice";
import AuthoredStylesApi from "../../services/api/authored";

const masterClient = new AuthoredVnextMasterClient();
const LAST_AI_MASTER_ANALYSIS_KEY = "presenton.authored-vnext.ai-master-analysis.v1";

export interface UploadPageProps {
  /** Component harness seam; production keeps the environment feature gate. */
  masterFeatureEnabled?: boolean;
}

const UploadPage = ({
  masterFeatureEnabled = isAuthoredVnextMasterEnabled(),
}: UploadPageProps = {}) => {
  const t = useTranslations("upload");
  const uploadLimits = useUploadLimits();
  const dispatch = useDispatch();
  const selectedThemeId = useSelector(
    (state: RootState) => state.pptGenUpload.selectedTheme
  );
  const authoredStyle = useSelector(
    (state: RootState) => state.pptGenUpload.authoredStyle,
  );
  const companyStandard = useSelector(
    (state: RootState) => state.pptGenUpload.companyStandard
  );
  const sourceContentMode = useSelector(
    (state: RootState) => state.pptGenUpload.sourceContentMode,
  );
  const generationStrategy = useSelector(
    (state: RootState) => state.pptGenUpload.generationStrategy,
  );
  const [sourceSvgCapability, setSourceSvgCapability] = useState<boolean | null>(null);
  const [reviewBeforeGeneration, setReviewBeforeGeneration] = useState(false);
  const [masterCatalogOpen, setMasterCatalogOpen] = useState(false);
  const [masterCatalogEntryMode, setMasterCatalogEntryMode] =
    useState<MasterCatalogEntryMode>("catalog");
  const [lastAnalysisId, setLastAnalysisId] = useState<string | null>(() => {
    if (typeof window === "undefined") return null;
    try {
      const value = window.localStorage.getItem(LAST_AI_MASTER_ANALYSIS_KEY);
      return value && /^[0-9a-f]{32}$/.test(value) ? value : null;
    } catch {
      return null;
    }
  });
  const [analyzedSourceSignature, setAnalyzedSourceSignature] = useState<string | null>(null);
  const masterSelectionEpochRef = useRef(0);

  useEffect(() => {
    if (!masterFeatureEnabled) return;
    // Company-standard selection is document-scoped. A fresh upload must never
    // inherit browser state created by another user, automation, or document.
    ++masterSelectionEpochRef.current;
    dispatch(setPptGenUploadState({ companyStandard: null }));
    clearLastUsedMasterId();
  }, [dispatch, masterFeatureEnabled]);

  useEffect(() => {
    if (!sourceContentMode) {
      setSourceSvgCapability(null);
      return;
    }
    let cancelled = false;
    setSourceSvgCapability(null);
    void AuthoredStylesApi.getDocumentCanvasCapabilities({
      generationStrategy: "svg",
      authoredStyle,
      sourceContentMode: true,
      // Company-standard masters are Authored-only and are omitted from this route.
      hasCompanyStandard: false,
    }).then((capability) => {
      if (!cancelled) setSourceSvgCapability(capability.sourceContentSvg.enabled);
    });
    return () => {
      cancelled = true;
    };
  }, [authoredStyle, sourceContentMode]);

  const {
    files,
    filesRef,
    replaceFiles,
    isDragOver,
    handleCardDragOver,
    handleCardDragLeave,
    handleCardDrop,
  } = useSupportingFiles({
    onFilesChange: (nextFiles) => {
      const isSinglePptx =
        nextFiles.length === 1 && /\.pptx$/i.test(nextFiles[0].name);
      dispatch(setSourceContentMode(isSinglePptx));
    },
  });
  const analysisSourceFile = useMemo(
    () => files.find((file) => /\.(?:ppt|pptx)$/i.test(file.name)) ?? null,
    [files],
  );
  const analysisSourceSignature = analysisSourceFile
    ? `${analysisSourceFile.name}:${analysisSourceFile.size}:${analysisSourceFile.lastModified}`
    : null;
  const resumeAnalysisId =
    !analysisSourceFile || analyzedSourceSignature === analysisSourceSignature
      ? lastAnalysisId
      : null;

  const {
    config,
    previousGenerationAttempt,
    generationAttemptActive,
    recoverCreate,
    rasterRetryAvailable,
    rasterAdvancedReview,
    setRasterAdvancedReview,
    retryRasterAnalysis,
    editGenerationRequest,
    handleConfigChange,
    loadingState,
    canCancelDocumentProcessing,
    decomposeAbortControllerRef,
    handleGeneratePresentation,
    handleReviewOutline,
    isMasterResolving,
    masterAdmissionError,
    sourceContentSummary,
    sourceContentCanvas,
    isSourceContentAnalyzing,
    sourceContentLossPrompt,
    resolveSourceContentLossPrompt,
    rasterReview,
    rasterAnalysisMode,
    canAutoPreserveExistingRaster,
    autoPreserveExistingRaster,
    rasterPage,
    isRasterPageLoading,
    loadRasterPage,
    saveRasterReview,
    setRasterPageMode,
    selectRecommendedRasterPages,
    preserveRasterDeck,
    confirmRasterInventory,
    approveRasterReview,
  } = useUploadPageState({
    files,
    filesRef,
    selectedThemeId,
    masterFeatureEnabled,
    onMasterRequired: () => {
      setMasterCatalogEntryMode("catalog");
      setMasterCatalogOpen(true);
    },
  });
  useEffect(() => {
    if (!generationAttemptActive && files.length === 0 && sourceContentMode) {
      dispatch(setSourceContentMode(false));
    }
  }, [dispatch, files.length, sourceContentMode, generationAttemptActive]);

  const openMasterCatalog = useCallback(() => {
    setMasterCatalogEntryMode("catalog");
    setMasterCatalogOpen(true);
  }, []);

  const selectAiMasterSource = useCallback((file: File) => {
    if (!/\.(?:ppt|pptx)$/i.test(file.name)) return;
    if (file.size > uploadLimits.document.bytes) {
      notify.error(
        "파일이 너무 큽니다",
        `파일당 최대 크기는 ${uploadLimits.document.mb}MB입니다.`,
      );
      return;
    }
    if (!analysisSourceFile && files.length >= MAX_SUPPORTED_FILES) {
      notify.warning(
        "최대 파일 수 초과",
        `문서는 최대 ${MAX_SUPPORTED_FILES}개까지만 업로드할 수 있습니다.`,
      );
      return;
    }
    const sourceIndex = files.findIndex((item) => /\.(?:ppt|pptx)$/i.test(item.name));
    if (sourceIndex < 0) replaceFiles([...files, file]);
    else {
      const next = [...files];
      next[sourceIndex] = file;
      replaceFiles(next);
    }
  }, [analysisSourceFile, files, replaceFiles, uploadLimits.document.bytes, uploadLimits.document.mb]);

  const rememberAnalysis = useCallback((analysisId: string | null) => {
    setLastAnalysisId(analysisId);
    setAnalyzedSourceSignature(analysisId ? analysisSourceSignature : null);
    try {
      if (analysisId) window.localStorage.setItem(LAST_AI_MASTER_ANALYSIS_KEY, analysisId);
      else window.localStorage.removeItem(LAST_AI_MASTER_ANALYSIS_KEY);
    } catch {
      // Server persistence remains authoritative when browser storage is unavailable.
    }
  }, [analysisSourceSignature]);

  // A master is optional.  Only a previously selected master that failed
  // published-head resolution blocks retrying the authored request.
  const masterBlocksGeneration = Boolean(masterAdmissionError);
  const generationBusy = loadingState.isLoading || isMasterResolving;

  const handleReviewThenGenerate = useCallback(() => {
    if (generationBusy) return;
    if (masterBlocksGeneration) {
      openMasterCatalog();
      return;
    }
    void handleReviewOutline();
  }, [generationBusy, handleReviewOutline, masterBlocksGeneration, openMasterCatalog]);

  const handlePrimaryGenerate = useCallback(() => {
    if (reviewBeforeGeneration) {
      handleReviewThenGenerate();
      return;
    }
    if (generationBusy) return;
    if (masterBlocksGeneration) {
      openMasterCatalog();
      return;
    }
    void handleGeneratePresentation();
  }, [
    generationBusy,
    handleGeneratePresentation,
    handleReviewThenGenerate,
    masterBlocksGeneration,
    openMasterCatalog,
    reviewBeforeGeneration,
  ]);

  const applyMaster = useCallback(
    (published: MasterRevisionEnvelope) => {
      // A manual Apply always wins over an in-flight preference restoration.
      ++masterSelectionEpochRef.current;
      dispatch(
        setPptGenUploadState({
          companyStandard: masterSelectionFromPublished(published),
        })
      );
    },
    [dispatch]
  );

  const clearMaster = useCallback(() => {
    ++masterSelectionEpochRef.current;
    dispatch(setPptGenUploadState({ companyStandard: null }));
    clearLastUsedMasterId();
  }, [dispatch]);

  const dashboardPromptHandoff = useRef<string | null | undefined>(undefined);
  useEffect(() => {
    if (previousGenerationAttempt.requestedId) return;
    try {
      if (dashboardPromptHandoff.current === undefined) {
        dashboardPromptHandoff.current = window.sessionStorage.getItem("devax:dashboard-prompt");
      }
      const dashboardPrompt = dashboardPromptHandoff.current;
      if (!dashboardPrompt) return;
      if (config.prompt !== dashboardPrompt) {
        handleConfigChange("prompt", dashboardPrompt);
        return;
      }
      // Consume only after the controlled input has committed the transferred value.
      window.sessionStorage.removeItem("devax:dashboard-prompt");
      dashboardPromptHandoff.current = null;
    } catch {
      // A blocked session storage should not interrupt the normal upload flow.
    }
  }, [config.prompt, handleConfigChange, previousGenerationAttempt.requestedId]);

  if (generationAttemptActive && rasterReview) return (
    <Wrapper className="max-w-none pb-20">
      <main className="mx-auto max-w-7xl px-4 pt-8 sm:px-6">
        <RasterSourceReview
          analysis={rasterReview}
          variant={rasterAnalysisMode === "auto_redesign" ? "redesign" : "manual"}
          page={rasterPage}
          isLoadingPage={isRasterPageLoading}
          onLoadPage={loadRasterPage}
          onSaveReview={saveRasterReview}
          onSetPageMode={setRasterPageMode}
          onSelectRecommendedPages={selectRecommendedRasterPages}
          onPreserveDeck={preserveRasterDeck}
          onAutoPreserveAndGenerate={canAutoPreserveExistingRaster ? autoPreserveExistingRaster : undefined}
          onConfirmInventory={confirmRasterInventory}
          onApprove={approveRasterReview}
        />
      </main>
    </Wrapper>
  );

  if (generationAttemptActive) return (
    <GenerationPreparation
      status={sourceContentLossPrompt ? "원본 처리 방법을 확인해 주세요." : loadingState.message || "발표자료를 준비하고 있습니다."}
      busy={loadingState.isLoading && !sourceContentLossPrompt}
      progressLabel={loadingState.progressLabel}
      detail={loadingState.extra_info}
      actions={<>
        {canCancelDocumentProcessing && <Button variant="outline" onClick={() => abortDocumentProcessing(decomposeAbortControllerRef.current)}>자료 처리 취소</Button>}
        {!generationBusy && !sourceContentLossPrompt && (rasterRetryAvailable
          ? <Button onClick={() => void retryRasterAnalysis()}>분석 상태 다시 확인</Button>
          : loadingState.awaitingReceipt
            ? <Button onClick={() => void recoverCreate()}>접수 확인·계속</Button>
            : <Button variant="outline" onClick={editGenerationRequest}>요청 수정</Button>)}
      </>}
    >
      <SourceContentLossDialog inline prompt={sourceContentLossPrompt} onDecide={resolveSourceContentLossPrompt} />
    </GenerationPreparation>
  );

  return (
    <Wrapper className="max-w-none pb-20">
      <PreviousAttemptNotice attempt={previousGenerationAttempt} />

      <main className="px-4 sm:px-6 lg:px-10" inert={previousGenerationAttempt.blocked} aria-busy={previousGenerationAttempt.blocked && !previousGenerationAttempt.error}>
        <section
          className="flex flex-col items-center pt-10 md:pt-16"
          aria-labelledby="upload-hero-title"
        >
          <div className="w-full max-w-[880px] text-center">
            <h1
              id="upload-hero-title"
              className="text-[28px] font-semibold leading-tight tracking-[-0.04em] text-slate-950 md:text-[40px] md:leading-[1.2]"
            >
              {t("simplify.title")}
            </h1>
            <p className="mx-auto mt-4 max-w-xl text-sm leading-6 text-slate-500 sm:text-base">
              {t("simplify.description")}
            </p>
          </div>

          <div
            className={cn(
              "mt-8 w-full rounded-3xl border bg-white p-5 transition-colors md:p-7",
              isDragOver
                ? "border-violet-300 ring-4 ring-violet-100/60"
                : "border-slate-200/90"
            )}
            style={{
              inlineSize: "min(880px, 100%)",
              minBlockSize: 114,
              boxShadow: "0 12px 40px -16px rgb(15 23 42 / 14%)",
            }}
            onDragOver={handleCardDragOver}
            onDragLeave={handleCardDragLeave}
            onDrop={handleCardDrop}
            data-testid="compose-card"
          >
            <PromptInput
              value={config.prompt}
              onChange={(value) => handleConfigChange("prompt", value)}
              onSubmit={handlePrimaryGenerate}
              label={
                sourceContentMode ? t("simplify.sourcePromptLabel") : undefined
              }
              placeholder={
                sourceContentMode
                  ? t("simplify.sourcePromptPlaceholder")
                  : t("simplify.promptPlaceholder")
              }
            />

            <div className="mt-2">
              <SupportingDoc
                files={[...files]}
                onFilesChange={replaceFiles}
                compact
              />
              {files.length > 0 ? (
                <label className="mt-4 flex cursor-pointer items-center gap-2 rounded-xl border border-slate-100 bg-slate-50/60 px-3 py-2.5 text-sm font-medium text-[#242629]">
                <input
                  type="checkbox"
                  checked={sourceContentMode}
                  onChange={(event) =>
                    dispatch(
                      setSourceContentMode(event.target.checked),
                    )
                  }
                  data-testid="source-content-mode-checkbox"
                />
                <span>
                  {t("simplify.preserveSource")}
                  <span className="ml-2 font-normal text-[#737780]">
                    {t("simplify.preserveSourceDescription")}
                  </span>
                </span>
              </label>
              ) : null}
            </div>

            <fieldset className="mt-4" data-testid="generation-strategy-selector">
              <legend className="mb-2 text-sm font-semibold text-[#242629]">생성 방식</legend>
              <div className="flex flex-wrap gap-2">
                {([
                  ["authored", "AI 저작", "기본 방식"],
                  ["svg", "SVG", sourceContentMode ? "이미지형 원본 화면을 A4로 보존" : "벡터 원본으로 생성"],
                ] as const).map(([strategy, label, description]) => (
                  <label key={strategy} className="flex cursor-pointer items-center gap-2 rounded-xl border border-slate-200 px-3 py-2 text-sm">
                    <input
                      type="radio"
                      name="generation-strategy"
                      value={strategy}
                      checked={generationStrategy === strategy}
                      onChange={() => dispatch(setPptGenUploadState({ generationStrategy: strategy }))}
                      disabled={sourceContentMode && strategy === "svg" && sourceSvgCapability !== true && config.pageSize !== "A4"}
                      data-testid={`generation-strategy-${strategy}`}
                    />
                    <span><span className="font-medium">{label}</span><span className="ml-1 text-xs text-slate-500">{description}</span></span>
                  </label>
                ))}
              </div>
              {sourceContentMode && sourceSvgCapability === null && config.pageSize !== "A4" ? (
                <p className="mt-2 text-xs text-slate-500" role="status">SVG 원본 보존 지원을 확인하고 있습니다.</p>
              ) : sourceContentMode && sourceSvgCapability === false && config.pageSize !== "A4" ? (
                <p className="mt-2 text-xs text-amber-700" role="status">이 서버에서는 원본 콘텐츠 유지 SVG 생성을 아직 사용할 수 없습니다.</p>
              ) : null}
            </fieldset>

            {sourceContentMode ? <div className="mt-4 rounded-xl border border-[#E4E6E9] bg-[#FAFAFB] px-3 py-3 text-sm text-[#33363A]" data-testid="source-content-selection-summary">
              <p className="font-semibold">콘텐츠 · 원본 유지</p>
              <p className="mt-1 text-[#5E626A]">{generationStrategy === "svg" ? `생성 방식 · SVG · 선택 템플릿 ${authoredStyle}` : `AI 저작 스타일 · ${authoredStyle}`}</p>
              {generationStrategy === "authored" ? <p className="mt-1 text-[#5E626A]">회사 표준 · {companyStandard ? `${companyStandard.name} v${companyStandard.revision}` : "사용 안 함"}</p> : null}
              {generationStrategy === "svg" ? <p className="mt-1 text-[#5E626A]">이미지형 입력은 원본 화면을 편집 가능한 텍스트와 표로 A4에 재구성합니다. 화면 캡처만 이미지로 남습니다. 서버가 확신하지 못한 소수 항목만 생성 전에 확인을 요청할 수 있습니다.</p> : null}
              <p className="mt-1 text-[#5E626A]">원본 페이지 순서와 페이지 수를 유지합니다.</p>
              {isSourceContentAnalyzing ? <p className="mt-2 text-xs text-[#737780]">원본 콘텐츠를 안전하게 분석하는 중입니다.</p> : null}
              {sourceContentSummary ? <p className="mt-2 text-xs text-[#737780]" data-testid="source-content-analysis-summary">
                {sourceContentSummary.sourceType.toUpperCase()} {sourceContentSummary.sourceUnits}페이지 · 텍스트 {sourceContentSummary.textBlocks}개 · 표 {sourceContentSummary.tables}개 · 제외된 차트 {sourceContentSummary.charts}개 · 이미지 {sourceContentSummary.uniqueImages}종(표시 {sourceContentSummary.imageOccurrences}회) · 이미지로 보관 {sourceContentSummary.rasterFallbackBlocks}개
              </p> : null}
            </div> : null}

            {sourceContentMode && generationStrategy === "svg" && config.pageSize === "A4" && authoredStyle === "screenshot-blue" &&
              <details className="mt-3 rounded-lg border border-slate-200 bg-white p-3 text-sm text-slate-700">
                <summary className="cursor-pointer font-medium">고급 옵션 · 모든 영역을 직접 검토</summary>
                <label className="mt-3 flex items-start gap-2">
                  <input type="checkbox" className="mt-1" checked={rasterAdvancedReview}
                    onChange={(event) => setRasterAdvancedReview(event.target.checked)} />
                  <span>기본값은 서버가 자동으로 재구성하고 확신이 낮은 소수 항목만 확인을 요청합니다. 이 옵션을 켜면 모든 영역과 인식 문자를 페이지별로 직접 검토합니다.</span>
                </label>
              </details>}

            <div className="mt-5 flex flex-col gap-3 border-t border-slate-100 pt-4 xl:flex-row xl:items-center xl:justify-between">
              <div className="flex min-w-0 flex-1 flex-wrap items-center gap-3">
                <>
                  <ConfigurationSelects
                    config={config}
                    onConfigChange={handleConfigChange}
                    sourceContentMode={sourceContentMode}
                    authoredStyle={authoredStyle}
                    hasCompanyStandard={Boolean(companyStandard) && !sourceContentMode}
                    generationStrategy={generationStrategy}
                    sourceDocumentCanvas={sourceContentCanvas}
                  />
                </>
              </div>
              <label className="flex min-w-0 cursor-pointer items-start gap-2 rounded-lg px-2 py-1.5 text-left text-sm text-slate-700 transition-colors hover:bg-slate-100 dark:text-slate-200 dark:hover:bg-slate-800">
                <input
                  type="checkbox"
                  checked={reviewBeforeGeneration}
                  onChange={(event) =>
                    setReviewBeforeGeneration(event.target.checked)
                  }
                  data-testid="review-outline-checkbox"
                  className="mt-0.5 h-4 w-4 shrink-0 accent-violet-600"
                />
                <span className="min-w-0">
                  <span className="block font-medium">
                    {t("simplify.reviewBeforeCreate")}
                  </span>
                  <span className="block text-xs text-slate-500 dark:text-slate-400">
                    {t("simplify.reviewBeforeCreateDescription")}
                  </span>
                </span>
              </label>
              <Button
                type="button"
                variant="toolbar"
                size="toolbar"
                onClick={handlePrimaryGenerate}
                disabled={generationBusy || previousGenerationAttempt.blocked}
                data-testid="generate-presentation-button"
                className="h-11 shrink-0 rounded-xl bg-slate-900 px-5 text-white shadow-sm hover:bg-slate-800 focus-visible:ring-violet-400 disabled:cursor-not-allowed"
              >
                <ArrowUp className="h-4 w-4" aria-hidden="true" />
                <span>
                  {loadingState.isLoading
                    ? t("generating")
                    : t("simplify.createPresentation")}
                </span>
              </Button>
            </div>

            {generationStrategy === "authored" && masterAdmissionError ? (
              <div
                className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2.5 text-sm text-rose-900"
                role="alert"
                data-testid="master-admission-error"
              >
                <span>{t("master.unavailable")}</span>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={openMasterCatalog}
                  className="shrink-0 border-rose-300 bg-white text-rose-800 hover:bg-rose-100"
                >
                  {t("master.choose")}
                </Button>
              </div>
            ) : null}
          </div>
        </section>

        <UploadDesignGallery>
        {masterFeatureEnabled && generationStrategy === "authored" ? (
          <section
            className="mx-auto mt-6 max-w-[1080px]"
            aria-labelledby="upload-master-title"
            data-testid="upload-master-selection"
          >
            <div className="flex flex-col gap-4 rounded-2xl border border-slate-200/80 bg-white px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex min-w-0 items-start gap-3">
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-violet-50 text-violet-600">
                  <LayoutTemplate className="h-4 w-4" aria-hidden="true" />
                </span>
                <div className="min-w-0">
                  <h2 id="upload-master-title" className="text-sm font-bold text-[#242629]">
                    {t("simplify.useCompanyTemplate")}
                  </h2>
                  {companyStandard ? (
                    <p className="mt-1 break-words text-sm text-slate-500" data-testid="company-standard-summary">
                      {t("master.selected", {
                        name: companyStandard.name,
                        version: companyStandard.revision,
                        layouts: companyStandard.layoutCount,
                      })}
                    </p>
                  ) : (
                    <p className="mt-1 text-sm text-[#737780]">
                      {t("simplify.companyTemplateDescription")}
                    </p>
                  )}
                </div>
              </div>
              <div className="flex shrink-0 flex-wrap gap-2">
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => { rememberAnalysis(null); setMasterCatalogEntryMode("ai-import"); setMasterCatalogOpen(true); }}
                  data-testid="ppt-ai-master-create"
                >
                  AI 마스터 만들기
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  onClick={openMasterCatalog}
                  data-testid="open-master-catalog"
                >
                  {companyStandard ? t("master.change") : t("master.choose")}
                </Button>
              </div>
            </div>
          </section>
        ) : null}

        <section className="mx-auto mt-10 max-w-[1080px]">
          <TemplateSelection />
        </section>
        </UploadDesignGallery>
        {masterFeatureEnabled && generationStrategy === "authored" ? (
          <MasterCatalogDialog
            analysisSourceFile={analysisSourceFile}
            entryMode={masterCatalogEntryMode}
            open={masterCatalogOpen}
            onAnalysisIdChange={rememberAnalysis}
            onAnalysisSourceFileChange={selectAiMasterSource}
            onOpenChange={setMasterCatalogOpen}
            resumeAnalysisId={resumeAnalysisId}
            selectedMasterId={companyStandard?.masterId}
            onApply={applyMaster}
            onClear={clearMaster}
          />
        ) : null}

        <SourceContentLossDialog
          prompt={sourceContentLossPrompt}
          onDecide={resolveSourceContentLossPrompt}
        />
      </main>
    </Wrapper>
  );
};

export default UploadPage;
