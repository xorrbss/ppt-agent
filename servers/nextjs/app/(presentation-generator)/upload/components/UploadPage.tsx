"use client";

import { useTranslations as useA2bTranslations } from "next-intl";
/**
 * Upload entry experience: a centered creation composer followed by a
 * container-responsive template catalog. Generation behavior remains owned by
 * useUploadPageState; this component only reorganizes the interaction surface.
 */

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { LayoutTemplate } from "lucide-react";
import { useTranslations } from "next-intl";
import { useDispatch, useSelector } from "react-redux";

import Wrapper from "@/components/Wrapper";
import { Button } from "@/components/ui/button";
import { useUploadLimits } from "@/lib/use-upload-limits";
import { cn } from "@/lib/utils";
import {
  setPptGenUploadState,
  setSourceContentMode,
} from "@/store/slices/presentationGenUpload";
import type { RootState } from "@/store/store";
import { isAuthoredVnextMasterEnabled } from "@/lib/authored-vnext/master/contracts";
import { AuthoredVnextMasterClient } from "@/lib/authored-vnext/master/client";
import { clearLastUsedMasterId } from "@/lib/authored-vnext/master/selection-storage";

import TemplateSelection from "../../outline/components/TemplateSelection";
import MasterCatalogDialog from "../../authored-vnext/master-setup/MasterCatalogDialog";
import { ConfigurationSelects } from "./ConfigurationSelects";
import { PromptInput } from "./PromptInput";
import SupportingDoc from "./SupportingDoc";
import SvgDesignReferenceInput from "./SvgDesignReferenceInput";
import { abortDocumentProcessing } from "./upload-page/abortDocumentProcessing";
import { useSupportingFiles } from "./upload-page/useSupportingFiles";
import SourceContentLossDialog from "./upload-page/SourceContentLossDialog";
import RasterSourceReview from "./upload-page/RasterSourceReview";
import { useUploadPageState } from "./upload-page/useUploadPageState";
import { useMasterCatalogController } from "./upload-page/useMasterCatalogController";
import { useSourceSvgCapability } from "./upload-page/useSourceSvgCapability";
import { useDashboardPromptHandoff } from "./upload-page/useDashboardPromptHandoff";
import { SourceContentSummaryPanel } from "./upload-page/SourceContentSummaryPanel";
import { GenerationStrategySelector } from "./upload-page/GenerationStrategySelector";
import { GenerateActionRow } from "./upload-page/GenerateActionRow";
import UploadDesignGallery from "./UploadDesignGallery";
import { GenerationPreparation } from "../../generation-workspace/GenerationPreparation";
import { PreviousAttemptNotice } from "./upload-page/PreviousAttemptNotice";

const masterClient = new AuthoredVnextMasterClient();

export interface UploadPageProps {
  /** Component harness seam; production keeps the environment feature gate. */
  masterFeatureEnabled?: boolean;
}

const UploadPage = ({
  masterFeatureEnabled = isAuthoredVnextMasterEnabled(),
}: UploadPageProps = {}) => {
  const tA2b = useA2bTranslations("uploadExtras");
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
  const [reviewBeforeGeneration, setReviewBeforeGeneration] = useState(false);
  const sourceSvgCapability = useSourceSvgCapability(sourceContentMode, authoredStyle);

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

  const {
    masterCatalogOpen,
    setMasterCatalogOpen,
    masterCatalogEntryMode,
    resumeAnalysisId,
    openMasterCatalog,
    openAiMasterImport,
    selectAiMasterSource,
    rememberAnalysis,
    applyMaster,
    clearMaster,
    masterSelectionEpochRef,
  } = useMasterCatalogController({
    dispatch,
    files,
    replaceFiles,
    analysisSourceFile,
    uploadLimits,
  });

  useEffect(() => {
    if (!masterFeatureEnabled) return;
    // Company-standard selection is document-scoped. A fresh upload must never
    // inherit browser state created by another user, automation, or document.
    ++masterSelectionEpochRef.current;
    dispatch(setPptGenUploadState({ companyStandard: null }));
    clearLastUsedMasterId();
  }, [dispatch, masterFeatureEnabled, masterSelectionEpochRef]);

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
    onMasterRequired: () => openMasterCatalog(),
  });
  useEffect(() => {
    if (!generationAttemptActive && files.length === 0 && sourceContentMode) {
      dispatch(setSourceContentMode(false));
    }
  }, [dispatch, files.length, sourceContentMode, generationAttemptActive]);

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

  useDashboardPromptHandoff(config.prompt, handleConfigChange, previousGenerationAttempt.requestedId);

  if (generationAttemptActive && rasterReview) return (
    <Wrapper className="max-w-none pb-20">
      <main className="mx-auto max-w-7xl px-4 pt-8 sm:px-6">
        <div className="mb-4 flex flex-wrap items-center gap-3 rounded-lg border border-slate-200 bg-white p-3 text-sm text-slate-700">
          <label htmlFor="raster-common-header-mode">페이지 상단</label>
          <select id="raster-common-header-mode" value={config.commonHeaderMode ?? "uniform"} onChange={(event) => handleConfigChange("commonHeaderMode", event.target.value)} className="rounded border border-slate-200 bg-white px-2 py-1.5">
            <option value="uniform">상단 통일</option>
            <option value="free">페이지별 자유 구성</option>
          </select>
          {(config.commonHeaderMode ?? "uniform") === "uniform" ? <label className="flex items-center gap-1.5"><input type="checkbox" checked={config.commonHeaderCoverException ?? false} onChange={(event) => handleConfigChange("commonHeaderCoverException", event.target.checked)} />표지 제외</label> : null}
          <span className="text-slate-500">원본 화면을 그대로 보존하는 페이지가 있으면 페이지별 자유 구성을 선택하세요.</span>
          {(config.commonHeaderMode ?? "uniform") === "uniform" && rasterReview.pages.some((page) => page.renderMode === "full_page_preserve") ? <p className="basis-full text-amber-700" role="alert">원본 화면을 그대로 보존하는 페이지에는 상단 통일을 적용할 수 없습니다. 페이지별 자유 구성을 선택하세요.</p> : null}
        </div>
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
        {canCancelDocumentProcessing && <Button variant="outline" onClick={() => abortDocumentProcessing(decomposeAbortControllerRef.current)}>{tA2b("cancelDocumentProcessing")}</Button>}
        {!generationBusy && !sourceContentLossPrompt && (rasterRetryAvailable
          ? <Button onClick={() => void retryRasterAnalysis()}>{tA2b("checkAnalysisStatusAgain")}</Button>
          : loadingState.awaitingReceipt
            ? <Button onClick={() => void recoverCreate()}>{tA2b("checkAdmissionAndContinue")}</Button>
            : <Button variant="outline" onClick={editGenerationRequest}>{tA2b("editRequest")}</Button>)}
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

            <GenerationStrategySelector
              sourceContentMode={sourceContentMode}
              generationStrategy={generationStrategy}
              onChangeStrategy={(strategy) => dispatch(setPptGenUploadState({ generationStrategy: strategy }))}
              sourceSvgCapability={sourceSvgCapability}
              pageSize={config.pageSize}
            />

            {sourceContentMode ? (
              <SourceContentSummaryPanel
                generationStrategy={generationStrategy}
                authoredStyle={authoredStyle}
                companyStandard={companyStandard}
                isSourceContentAnalyzing={isSourceContentAnalyzing}
                sourceContentSummary={sourceContentSummary}
              />
            ) : null}

            {sourceContentMode && generationStrategy === "svg" && config.pageSize === "A4" && authoredStyle === "screenshot-blue" &&
              <details className="mt-3 rounded-lg border border-slate-200 bg-white p-3 text-sm text-slate-700">
                <summary className="cursor-pointer font-medium">{tA2b("advancedOptionReviewEveryRegionManually")}</summary>
                <label className="mt-3 flex items-start gap-2">
                  <input type="checkbox" className="mt-1" checked={rasterAdvancedReview}
                    onChange={(event) => setRasterAdvancedReview(event.target.checked)} />
                  <span>{tA2b("byDefaultTheServerReconstructsContentAutomatically")}</span>
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
              <GenerateActionRow
                reviewBeforeGeneration={reviewBeforeGeneration}
                onReviewBeforeGenerationChange={setReviewBeforeGeneration}
                onGenerate={handlePrimaryGenerate}
                disabled={generationBusy || previousGenerationAttempt.blocked}
                generating={loadingState.isLoading}
              />
            </div>

            {generationStrategy === "svg" ? <SvgDesignReferenceInput config={config} onConfigChange={handleConfigChange} disabled={Boolean(config.svgDesignReferencePin || companyStandard)} /> : null}

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
                  onClick={() => { rememberAnalysis(null); openAiMasterImport(); }}
                  data-testid="ppt-ai-master-create"
                >
                  {t("master.aiCreate")}
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
