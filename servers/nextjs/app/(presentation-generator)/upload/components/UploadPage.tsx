/**
 * Upload entry experience: a centered creation composer followed by a
 * container-responsive template catalog. Generation behavior remains owned by
 * useUploadPageState; this component only reorganizes the interaction surface.
 */

"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowUp, LayoutTemplate, Sparkles } from "lucide-react";
import { useTranslations } from "next-intl";
import { useDispatch, useSelector } from "react-redux";

import Wrapper from "@/components/Wrapper";
import { Button } from "@/components/ui/button";
import { OverlayLoader } from "@/components/ui/overlay-loader";
import { notify } from "@/components/ui/sonner";
import { useUploadLimits } from "@/lib/use-upload-limits";
import { cn } from "@/lib/utils";
import {
  setPptGenUploadState,
  setSourceLockedGenerationMode,
  syncSourceLockedAttachmentLatch,
} from "@/store/slices/presentationGenUpload";
import type { RootState } from "@/store/store";
import { isAuthoredVnextMasterEnabled } from "@/lib/authored-vnext/master/contracts";
import type { MasterRevisionEnvelope } from "@/lib/authored-vnext/master/contracts";
import { AuthoredVnextMasterClient } from "@/lib/authored-vnext/master/client";
import {
  clearLastUsedMasterId,
  masterSelectionFromPublished,
  readStoredCompanyStandardPreference,
  saveLastUsedMasterId,
} from "@/lib/authored-vnext/master/selection-storage";

import TemplateSelection from "../../outline/components/TemplateSelection";
import MasterCatalogDialog from "../../authored-vnext/master-setup/MasterCatalogDialog";
import type { MasterCatalogEntryMode } from "../../authored-vnext/master-setup/MasterCatalogDialog";
import { ConfigurationSelects } from "./ConfigurationSelects";
import CurrentConfig from "./CurrentConfig";
import { PromptInput } from "./PromptInput";
import SupportingDoc, { MAX_SUPPORTED_FILES } from "./SupportingDoc";
import { SourceLockedHistory } from "./source-locked/SourceLockedHistory";
import { abortDocumentProcessing } from "./upload-page/abortDocumentProcessing";
import { useSupportingFiles } from "./upload-page/useSupportingFiles";
import { useUploadPageState } from "./upload-page/useUploadPageState";

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
  const companyStandard = useSelector(
    (state: RootState) => state.pptGenUpload.companyStandard
  );
  const sourceLockedGenerationMode = useSelector(
    (state: RootState) => state.pptGenUpload.sourceLockedGenerationMode,
  );
  const sourceLockedGenerationModeLatched = useSelector(
    (state: RootState) => state.pptGenUpload.sourceLockedGenerationModeLatched,
  );
  const sourceLocked = sourceLockedGenerationMode === "source_locked";
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

    let active = true;
    const epoch = ++masterSelectionEpochRef.current;
    const preference = readStoredCompanyStandardPreference();
    // A preference only identifies a master. Resolve the current published
    // pointer for each fresh upload entry instead of retaining a stale pin.
    dispatch(setPptGenUploadState({ companyStandard: null }));
    const applyPublished = (published: MasterRevisionEnvelope) => {
      if (!active || masterSelectionEpochRef.current !== epoch) return;
      dispatch(
        setPptGenUploadState({
          companyStandard: masterSelectionFromPublished(published),
        })
      );
    };
    const resolvePublished = (masterId: string) =>
      masterClient.getPublished(masterId).then(applyPublished);

    if (preference.kind !== "last-used") {
      return () => {
        active = false;
      };
    }
    void resolvePublished(preference.masterId).catch(() => {
      // Deleted, unpublished, or inaccessible choices stay unselected.
    });

    return () => {
      active = false;
    };
  }, [dispatch, masterFeatureEnabled]);

  const {
    files,
    replaceFiles,
    isDragOver,
    handleCardDragOver,
    handleCardDragLeave,
    handleCardDrop,
  } = useSupportingFiles({
    sourceLocked,
    onFilesChange: (nextFiles) =>
      dispatch(syncSourceLockedAttachmentLatch(nextFiles.length)),
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
    handleConfigChange,
    loadingState,
    canCancelDocumentProcessing,
    decomposeAbortControllerRef,
    handleGeneratePresentation,
    handleReviewOutline,
    isMasterResolving,
    masterAdmissionError,
    sourceLockedSummary,
    sourceLockedArtifact,
    sourceLockedHistory,
    canCancelSourceLockedJob,
    handleCancelSourceLockedJob,
    cancelSourceLockedJob,
  } = useUploadPageState({
    files,
    selectedThemeId,
    masterFeatureEnabled,
    onMasterRequired: () => {
      setMasterCatalogEntryMode("catalog");
      setMasterCatalogOpen(true);
    },
  });

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

  const handlePrimaryGenerate = useCallback(() => {
    if (generationBusy) return;
    if (masterBlocksGeneration) {
      openMasterCatalog();
      return;
    }
    void handleGeneratePresentation();
  }, [generationBusy, handleGeneratePresentation, masterBlocksGeneration, openMasterCatalog]);

  const handleReviewThenGenerate = useCallback(() => {
    if (generationBusy) return;
    if (masterBlocksGeneration) {
      openMasterCatalog();
      return;
    }
    void handleReviewOutline();
  }, [generationBusy, handleReviewOutline, masterBlocksGeneration, openMasterCatalog]);

  const applyMaster = useCallback(
    (published: MasterRevisionEnvelope) => {
      // A manual Apply always wins over an in-flight preference restoration.
      ++masterSelectionEpochRef.current;
      dispatch(
        setPptGenUploadState({
          companyStandard: masterSelectionFromPublished(published),
        })
      );
      saveLastUsedMasterId(published.masterId);
    },
    [dispatch]
  );

  const clearMaster = useCallback(() => {
    ++masterSelectionEpochRef.current;
    dispatch(setPptGenUploadState({ companyStandard: null }));
    clearLastUsedMasterId();
  }, [dispatch]);

  return (
    <Wrapper className="max-w-none pb-20">
      <OverlayLoader
        show={loadingState.isLoading}
        text={loadingState.message}
        showProgress={loadingState.showProgress}
        duration={loadingState.duration}
        progress={loadingState.progress}
        progressLabel={loadingState.progressLabel}
        extra_info={loadingState.extra_info}
        onCancel={
          canCancelDocumentProcessing
            ? () => abortDocumentProcessing(decomposeAbortControllerRef.current)
            : canCancelSourceLockedJob
              ? () => { void handleCancelSourceLockedJob(); }
              : undefined
        }
      />

      <main className="px-4 sm:px-6 lg:px-8">
        <section
          className="flex flex-col items-center pt-10 md:pt-16"
          aria-labelledby="upload-hero-title"
        >
          <div className="w-full max-w-[760px] text-center">
            <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-[#E7E8EB] bg-[#FAFAFB] px-3 py-1.5 text-xs font-semibold text-[#5E626A]">
              <Sparkles
                className="h-3.5 w-3.5 text-[#6B5CE7]"
                aria-hidden="true"
              />
              {t("eyebrow")}
            </div>
            <h1
              id="upload-hero-title"
              className="font-syne text-[28px] font-bold leading-tight tracking-[-0.035em] text-[#242629] md:text-[36px] md:leading-[1.3]"
            >
              {t("heroTitle")}
            </h1>
            <p className="mx-auto mt-2 max-w-xl font-syne text-sm leading-6 text-[#737780] md:text-[15px]">
              {t("heroDescription")}
            </p>
          </div>

          <div
            className={cn(
              "mt-6 rounded-2xl border bg-white p-4 transition-colors md:p-5",
              isDragOver
                ? "border-[#5141E5] ring-2 ring-[#5141E5]/20"
                : "border-[#E4E6E9]"
            )}
            style={{
              inlineSize: "min(760px, 100%)",
              minBlockSize: 114,
              boxShadow: "0 6px 30px rgb(0 0 0 / 8%)",
            }}
            onDragOver={handleCardDragOver}
            onDragLeave={handleCardDragLeave}
            onDrop={handleCardDrop}
            data-testid="compose-card"
          >
            {!sourceLocked ? (
              <PromptInput
                value={config.prompt}
                onChange={(value) => handleConfigChange("prompt", value)}
                onSubmit={handlePrimaryGenerate}
              />
            ) : (
              <p className="px-1 py-2 text-sm text-[#5E626A]" data-testid="source-locked-mode-notice">
                원본 페이지 수와 이미지 배치를 유지하여 PPT를 생성합니다.
              </p>
            )}

            {!sourceLockedSummary ? <div className="mt-2">
              <SupportingDoc
                files={[...files]}
                onFilesChange={replaceFiles}
                compact
                sourceLocked={sourceLocked}
              />
              <label className="mt-3 flex cursor-pointer items-center gap-2 text-sm font-medium text-[#242629]">
                <input
                  type="checkbox"
                  checked={sourceLocked}
                  disabled={sourceLockedGenerationModeLatched}
                  onChange={(event) =>
                    dispatch(
                      setSourceLockedGenerationMode(
                        event.target.checked ? "source_locked" : "adaptive",
                      ),
                    )
                  }
                  data-testid="source-locked-generation-checkbox"
                />
                원본 문서 그대로 생성
              </label>
            </div> : (
              <div className="mt-3">
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => replaceFiles([])}
                  data-testid="source-locked-reset-button"
                >
                  다른 원본 선택
                </Button>
              </div>
            )}

            {sourceLockedSummary ? (
              <div className="mt-4 rounded-xl border border-[#E4E6E9] bg-[#FAFAFB] px-3 py-3 text-sm text-[#33363A]" data-testid="source-locked-analysis-summary">
                <p className="font-semibold">{sourceLockedSummary.source_format.toUpperCase()} {sourceLockedSummary.source_page_count}장 → {sourceLockedSummary.output_slide_count}슬라이드</p>
                <p className="mt-1 text-[#5E626A]">미디어 {sourceLockedSummary.media_asset_count}개 · 이미지 occurrence {sourceLockedSummary.image_occurrence_count}개 · inherited occurrence {sourceLockedSummary.inherited_image_occurrence_count}개</p>
                {sourceLockedArtifact ? <>
                  <p className="mt-1 text-xs text-[#737780]">원본 분석 완료 · 슬라이드·이미지 복원 완료 · 누락 검증 완료</p>
                  <p className="mt-1 text-xs text-[#737780]">{sourceLockedArtifact.filename} · {sourceLockedArtifact.byte_length.toLocaleString()} bytes · {sourceLockedArtifact.sha256}</p>
                  <a
                    href={sourceLockedArtifact.download_url}
                    className="mt-3 inline-flex h-9 items-center rounded-full bg-[#242629] px-4 text-sm font-semibold text-white hover:bg-[#111214]"
                    data-testid="source-locked-download-link"
                  >
                    원본대로 생성된 PPTX 다운로드
                  </a>
                </> : null}
              </div>
            ) : null}

            <SourceLockedHistory
              entries={sourceLockedHistory}
              onCancel={(jobId) => { void cancelSourceLockedJob(jobId); }}
            />

            {!sourceLockedSummary ? <div className="mt-4 flex flex-col gap-3 border-t border-[#F0F1F3] pt-3 lg:flex-row lg:items-center lg:justify-between">
              <div className="flex min-w-0 flex-1 flex-wrap items-center gap-3">
                <>
                  <ConfigurationSelects
                    config={config}
                    onConfigChange={handleConfigChange}
                    sourceLocked={sourceLocked}
                  />
                  {!sourceLocked ?
                  <div className="min-w-0 max-w-full overflow-hidden">
                    <CurrentConfig />
                  </div> : null}
                </>
              </div>
              {!sourceLocked ? <Button
                type="button"
                variant="outline"
                onClick={handleReviewThenGenerate}
                disabled={generationBusy}
                data-testid="review-outline-button"
                className="h-11 shrink-0 rounded-full px-5 font-syne text-sm font-semibold"
              >
                {t("reviewOutline")}
              </Button> : null}
              <Button
                type="button"
                onClick={handlePrimaryGenerate}
                disabled={generationBusy}
                data-testid="generate-presentation-button"
                className="h-11 shrink-0 rounded-full bg-[#242629] px-5 font-syne text-sm font-semibold text-white shadow-[0_6px_16px_rgba(36,38,41,0.18)] transition hover:bg-[#111214] disabled:cursor-not-allowed disabled:opacity-60"
              >
                <span>
                  {loadingState.isLoading ? t("generating") : sourceLocked ? "원본대로 PPT 생성" : t("generate")}
                </span>
                <ArrowUp className="ml-2 h-4 w-4" aria-hidden="true" />
              </Button>
            </div> : null}

            {masterAdmissionError ? (
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

        {masterFeatureEnabled && !sourceLocked ? (
          <section
            className="mx-auto mt-6 max-w-[760px]"
            aria-labelledby="upload-master-title"
            data-testid="upload-master-selection"
          >
            <div className="flex flex-col gap-3 rounded-2xl border border-[#E5E7EA] bg-white px-4 py-3 shadow-sm sm:flex-row sm:items-center sm:justify-between">
              <div className="flex min-w-0 items-start gap-3">
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-violet-50 text-violet-700">
                  <LayoutTemplate className="h-4 w-4" aria-hidden="true" />
                </span>
                <div className="min-w-0">
                  <h2 id="upload-master-title" className="text-sm font-bold text-[#242629]">
                    {t("master.title")}
                  </h2>
                  {companyStandard ? (
                    <p className="mt-1 truncate text-sm text-[#5E626A]" data-testid="company-standard-summary">
                      {t("master.selected", {
                        name: companyStandard.name,
                        version: companyStandard.revision,
                        layouts: companyStandard.layoutCount,
                      })}
                    </p>
                  ) : (
                    <p className="mt-1 text-sm text-[#737780]">
                      {t("master.optional")}
                    </p>
                  )}
                </div>
              </div>
              <div className="flex shrink-0 flex-wrap gap-2">
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

        {!sourceLocked ? <section className="mx-auto mt-10 max-w-[1180px]">
          <TemplateSelection />
        </section> : null}
        {masterFeatureEnabled ? (
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
      </main>
    </Wrapper>
  );
};

export default UploadPage;
