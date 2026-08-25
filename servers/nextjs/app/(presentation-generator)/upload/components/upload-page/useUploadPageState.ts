"use client";
import { useEffect, useRef, useState } from "react";
import { useRouter, usePathname } from "next/navigation";
import { useDispatch, useSelector } from "react-redux";
import { clearOutlines, setPresentationId } from "@/store/slices/presentationGeneration";
import { LanguageType, PresentationConfig, ToneType, VerbosityType } from "../../type";
import { notify } from "@/components/ui/sonner";
import {
  isAbortError,
  PresentationGenerationApi,
} from "../../../services/api/presentation-generation";
import {
  SourceLockedGenerationApiV1,
  sourceLockedGenerationFromResponse,
  sourceLockedSelectionErrorMessage,
  type SourceLockedAnalysisSummary,
  type SourceLockedArtifactV1,
  validateSourceLockedFiles,
} from "../../../services/api/source-locked/contract";
import { getApiErrorDisplayMessage } from "../../../services/api/api-error-handler";
import {
  setPptGenUploadState,
  type CompanyStandardSelection,
} from "@/store/slices/presentationGenUpload";
import { trackEvent, MixpanelEvent } from "@/utils/mixpanel";
import { RootState } from "@/store/store";
import { ImagesApi } from "../../../services/api/images";
import { resolveDeckTheme } from "../applyDeckTheme";
import { abortDocumentProcessing } from "./abortDocumentProcessing";
import { getFileCategory } from "./fileCategory";
import { getSelectedImageQuality, getSelectedTextModel } from "./llmConfigSummary";
import { LoadingState } from "./types";
import { AuthoredVnextMasterClient } from "@/lib/authored-vnext/master/client";
import {
  isAuthoredVnextMasterEnabled,
  type MasterRevisionEnvelope,
} from "@/lib/authored-vnext/master/contracts";
import { masterSelectionFromPublished } from "@/lib/authored-vnext/master/selection-storage";
import {
  createDurableGenerationTask,
  saveDurableGenerationTask,
} from "../../../outline/hooks/durableGenerationTask";

const STOCK_IMAGE_PROVIDERS = new Set(["pexels", "pixabay"]);
const masterClient = new AuthoredVnextMasterClient();

export type UploadMasterAdmissionError = "required" | "unavailable";

interface UseUploadPageStateArgs {
  files: File[];
  selectedThemeId: string | null;
  masterFeatureEnabled?: boolean;
  onMasterRequired?: () => void;
}

/**
 * Owns the presentation configuration form state and the generation workflow
 * (validation, document processing, direct generation, error handling).
 */
export const useUploadPageState = ({
  files,
  selectedThemeId,
  masterFeatureEnabled = isAuthoredVnextMasterEnabled(),
  onMasterRequired,
}: UseUploadPageStateArgs) => {
  // Allocate the server-visible deck id before the create command so the
  // workspace operation and its retries hash the exact same stable id.
  const [pendingPresentationId, setPendingPresentationId] = useState(
    () => crypto.randomUUID(),
  );
  const router = useRouter();
  const pathname = usePathname();
  const dispatch = useDispatch();
  const llmConfig = useSelector((state: RootState) => state.userConfig.llm_config);
  const companyStandard = useSelector(
    (state: RootState) => state.pptGenUpload.companyStandard,
  );
  const authoredStyle = useSelector(
    (state: RootState) => state.pptGenUpload.authoredStyle,
  );
  const authoredVisionQa = useSelector(
    (state: RootState) => state.pptGenUpload.authoredVisionQa,
  );
  const sourceLockedGenerationMode = useSelector(
    (state: RootState) => state.pptGenUpload.sourceLockedGenerationMode,
  );
  const sourceLocked = sourceLockedGenerationMode === "source_locked";
  const [sourceLockedSummary, setSourceLockedSummary] =
    useState<SourceLockedAnalysisSummary | null>(null);
  const [sourceLockedArtifact, setSourceLockedArtifact] =
    useState<SourceLockedArtifactV1 | null>(null);
  const [sourceLockedAnalysisId, setSourceLockedAnalysisId] = useState(
    () => crypto.randomUUID(),
  );
  const sourceLockedFileIdentity = files
    .map((file) => `${file.name}:${file.size}:${file.lastModified}`)
    .join("|");
  const [masterAdmissionError, setMasterAdmissionError] =
    useState<UploadMasterAdmissionError | null>(null);
  const [isMasterResolving, setIsMasterResolving] = useState(false);

  const [config, setConfig] = useState<PresentationConfig>({
    slides: "8",
    language: LanguageType.Korean,
    prompt: "",
    tone: ToneType.Default,
    verbosity: VerbosityType.Standard,
    instructions: "",
    includeTableOfContents: false,
    includeTitleSlide: false,
    webSearch: false,
  });

  const [loadingState, setLoadingState] = useState<LoadingState>({
    isLoading: false,
    message: "",
    duration: 4,
    showProgress: false,
    extra_info: "",
  });
  const decomposeAbortControllerRef = useRef<AbortController | null>(null);
  const companyStandardRef = useRef(companyStandard);
  const startInFlightRef = useRef(false);
  const [canCancelDocumentProcessing, setCanCancelDocumentProcessing] =
    useState(false);

  // A queued decomposition keeps running on FastAPI until it is explicitly
  // cancelled. Abort it when this upload screen unmounts (navigation/close).
  useEffect(() => {
    return () => abortDocumentProcessing(decomposeAbortControllerRef.current);
  }, []);

  // A seal/summary belongs to the exact selected source. Never show a prior
  // analysis after replacement or after the user clears attachments to unlock.
  useEffect(() => {
    setSourceLockedSummary(null);
    setSourceLockedArtifact(null);
    setSourceLockedAnalysisId(crypto.randomUUID());
  }, [sourceLockedFileIdentity, sourceLockedGenerationMode]);

  useEffect(() => {
    setMasterAdmissionError(null);
  }, [
    companyStandard?.masterId,
    companyStandard?.revision,
    companyStandard?.digest,
  ]);

  companyStandardRef.current = companyStandard;

  const resolveMasterForGeneration = async (): Promise<
    CompanyStandardSelection | null | undefined
  > => {
    if (!masterFeatureEnabled) return undefined;
    // A master is an optional structural layer.  Authored styles still own
    // the full-canvas path when no master was selected, so do not make the
    // ordinary template-only flow wait for a catalog choice.
    if (!companyStandard) return undefined;

    setIsMasterResolving(true);
    setMasterAdmissionError(null);
    try {
      // The preference stores identity only. Pin the owner's current published
      // pointer at the explicit generate click so a newly published revision is
      // used by this new document while existing documents keep their old pin.
      // Applying a different catalog card while this request is pending must
      // never let the older response overwrite the new choice. Resolve again
      // until the response matches the identity that is currently selected.
      for (;;) {
        const requested = companyStandardRef.current;
        if (!requested) {
          setMasterAdmissionError("required");
          onMasterRequired?.();
          return null;
        }
        let published: MasterRevisionEnvelope;
        try {
          published = await masterClient.getPublished(requested.masterId);
        } catch (error) {
          if (companyStandardRef.current?.masterId !== requested.masterId) continue;
          throw error;
        }
        if (companyStandardRef.current?.masterId !== requested.masterId) continue;
        const selection = masterSelectionFromPublished(published);
        companyStandardRef.current = selection;
        dispatch(setPptGenUploadState({ companyStandard: selection }));
        return selection;
      }
    } catch {
      setMasterAdmissionError("unavailable");
      onMasterRequired?.();
      return null;
    } finally {
      setIsMasterResolving(false);
    }
  };

  const stageAutoAuthoredGeneration = (
    presentationId: string,
    master: CompanyStandardSelection | undefined,
  ) => {
    const requestedSlides = config.slides ? Number.parseInt(config.slides, 10) : 8;
    const estimatedSlides = Number.isFinite(requestedSlides) ? requestedSlides : 8;
    const baseSeconds = Math.max(120, estimatedSlides * 25);
    const progressDurationSeconds = authoredVisionQa
      ? Math.min(30 * 60, baseSeconds * 2)
      : Math.min(600, baseSeconds);
    saveDurableGenerationTask({
      ...createDurableGenerationTask({
        mode: "authored",
        sourcePresentationId: presentationId,
        timeoutMs: 30 * 60 * 1000,
        progressDurationSeconds,
      }),
      authoredRequest: {
        language:
          config.language && config.language !== LanguageType.Auto
            ? config.language
            : null,
        visionQa: authoredVisionQa,
        authoredStyle,
        ...(master
          ? {
              masterPin: {
                masterId: master.masterId,
                revision: master.revision,
                digest: master.digest,
              },
            }
          : {}),
      },
    });
  };

  const getUploadSnapshotProps = () => {
    const trimmedPrompt = config.prompt.trim();
    const trimmedInstructions = (config.instructions || "").trim();
    const attachmentCategories = Array.from(new Set(files.map(getFileCategory))).sort();
    const imageGenerationEnabled = !llmConfig?.DISABLE_IMAGE_GENERATION;
    const parsedSlides =
      config.slides && /^\d+$/.test(config.slides) ? Number(config.slides) : null;

    return {
      pathname,
      generation_path: files.length > 0 ? "documents" : "prompt_only",
      slides_selected: parsedSlides,
      slides_mode: config.slides ? "selected" : "auto",
      language: config.language || "",
      tone: config.tone,
      verbosity: config.verbosity,
      include_table_of_contents: !!config.includeTableOfContents,
      include_title_slide: !!config.includeTitleSlide,
      web_search: !!config.webSearch,
      has_prompt: Boolean(trimmedPrompt),
      prompt_char_count: trimmedPrompt.length,
      prompt_word_count: trimmedPrompt ? trimmedPrompt.split(/\s+/).filter(Boolean).length : 0,
      has_instructions: Boolean(trimmedInstructions),
      instructions_char_count: trimmedInstructions.length,
      has_attachments: files.length > 0,
      attachments_count: files.length,
      attachment_categories: attachmentCategories.join(","),
      text_provider: llmConfig?.LLM || "",
      text_model: getSelectedTextModel(llmConfig),
      image_generation_enabled: imageGenerationEnabled,
      image_provider: imageGenerationEnabled ? (llmConfig?.IMAGE_PROVIDER || "") : "disabled",
      image_quality: imageGenerationEnabled ? getSelectedImageQuality(llmConfig) : "",
    };
  };

  const trackUploadValidationFailure = (reason: string) => {
    trackEvent(MixpanelEvent.Upload_Configuration_Invalid, {
      ...getUploadSnapshotProps(),
      reason,
    });
  };

  const handleConfigChange = (key: keyof PresentationConfig, value: unknown) => {
    setConfig((prev) => ({ ...prev, [key]: value } as PresentationConfig));
  };

  const ensureStockImageProviderReady = async (): Promise<boolean> => {
    if (llmConfig?.DISABLE_IMAGE_GENERATION) {
      return true;
    }

    const selectedProvider = (llmConfig?.IMAGE_PROVIDER || "").toLowerCase();
    if (!STOCK_IMAGE_PROVIDERS.has(selectedProvider)) {
      return true;
    }

    try {
      const providerApiKey =
        selectedProvider === "pexels"
          ? llmConfig?.PEXELS_API_KEY
          : llmConfig?.PIXABAY_API_KEY;
      await ImagesApi.searchStockImages("business", 1, {
        provider: selectedProvider,
        apiKey: providerApiKey,
        strictApiKey: true,
      });
      return true;
    } catch (error: any) {
      notify.error(
        "이미지 제공자에 연결할 수 없습니다",
        error?.message ||
        `현재 ${selectedProvider}에 연결할 수 없습니다. API 키와 설정을 확인한 후 다시 시도해 주세요.`
      );
      return false;
    }
  };

  /**
   * Validates the current configuration and files
   * @returns boolean indicating if the configuration is valid
   */
  const validateConfiguration = (): boolean => {
    if (sourceLocked) {
      const sourceLockedError = validateSourceLockedFiles(files);
      if (sourceLockedError) {
        trackUploadValidationFailure(sourceLockedError);
        notify.warning(
          "원본 문서 그대로 생성 파일 제한",
          sourceLockedSelectionErrorMessage(sourceLockedError),
        );
        return false;
      }
      return true;
    }
    if (!config.language) {
      trackUploadValidationFailure("language_missing");
      notify.warning("언어를 선택해 주세요", "언어를 선택해 주세요.");
      return false;
    }

    if (files.length > 0 && config.language === LanguageType.Auto) {
      trackUploadValidationFailure("language_auto_with_documents");
      notify.warning("언어를 선택해 주세요", "업로드한 문서를 처리하기 전에 언어를 선택해 주세요.");
      return false;
    }

    if (!config.prompt.trim() && files.length === 0) {
      trackUploadValidationFailure("prompt_or_document_missing");
      notify.warning("입력이 필요합니다", "프롬프트를 입력하거나 문서를 하나 이상 업로드해 주세요.");
      return false;
    }
    return true;
  };

  /**
   * Handles the presentation generation process
   */
  const startPresentation = async (auto: boolean) => {
    if (startInFlightRef.current) return;
    if (!validateConfiguration()) return;
    startInFlightRef.current = true;
    if (sourceLocked) {
      try {
        await handleSourceLockedGeneration();
      } catch (error) {
        handleGenerationError(error);
      } finally {
        startInFlightRef.current = false;
      }
      return;
    }
    const resolvedMaster = await resolveMasterForGeneration();
    if (resolvedMaster === null) {
      startInFlightRef.current = false;
      return;
    }
    trackEvent(MixpanelEvent.Upload_Generation_Started, getUploadSnapshotProps());


    const isStockProviderReady = await ensureStockImageProviderReady();
    if (!isStockProviderReady) {
      trackUploadValidationFailure("stock_image_provider_unreachable");
      startInFlightRef.current = false;
      return;
    }

    try {
      const hasUploadedAssets = files.length > 0;

      if (hasUploadedAssets) {
        await handleDocumentProcessing(auto, resolvedMaster);
      } else {
        await handleDirectPresentationGeneration(auto, resolvedMaster);
      }
    } catch (error) {
      handleGenerationError(error);
    } finally {
      startInFlightRef.current = false;
    }
  };

  const handleGeneratePresentation = () => startPresentation(true);
  const handleReviewOutline = () => startPresentation(false);

  /**
   * This is a terminal source-locked job: the backend must complete analysis,
   * restoration, and missing-content validation before an artifact is shown.
   */
  const handleSourceLockedGeneration = async () => {
    setSourceLockedSummary(null);
    setSourceLockedArtifact(null);
    setLoadingState({
      isLoading: true,
      message: "원본 문서 그대로 PPT를 생성하는 중…",
      showProgress: true,
      duration: 180,
      progress: 5,
      progressLabel: "원본 분석 → 슬라이드·이미지 복원 → 누락 검증",
      extra_info: "세 단계가 모두 완료될 때만 다운로드를 제공합니다.",
    });
    const response = await SourceLockedGenerationApiV1.generate(
      files,
      `source-locked-generate:v1:${sourceLockedAnalysisId}`,
    );
    const result = sourceLockedGenerationFromResponse(response);
    if (!result) {
      throw new Error("원본 복원 또는 누락 검증이 완료되지 않아 PPT를 제공할 수 없습니다.");
    }
    setLoadingState({
      isLoading: false,
      message: "",
      showProgress: false,
      duration: 0,
      progress: 100,
      progressLabel: "원본 분석 · 슬라이드·이미지 복원 · 누락 검증 완료",
      extra_info: "",
    });
    setSourceLockedSummary(result.summary);
    setSourceLockedArtifact(result.artifact);
  };

  /**
   * Handles document processing
   */
  const handleDocumentProcessing = async (
    auto: boolean,
    resolvedMaster: CompanyStandardSelection | undefined,
  ) => {
    setLoadingState({
      isLoading: true,
      message: "문서를 처리하는 중…",
      showProgress: true,
      duration: 90,
      progress: 0,
      progressLabel: `0/${files.length}`,
      extra_info: files.length > 0 ? "용량이 큰 문서는 몇 분 정도 걸릴 수 있습니다." : "",
    });

    let documentAdmission: Awaited<
      ReturnType<typeof PresentationGenerationApi.uploadDoc>
    > | null = null;

    if (files.length > 0) {
      documentAdmission = await PresentationGenerationApi.uploadDoc(
        files,
        pendingPresentationId,
      );
    }

    const selectedLanguage = config?.language ?? "";

    const promises: Promise<any>[] = [];

    if (documentAdmission) {
      const decomposeController = new AbortController();
      decomposeAbortControllerRef.current = decomposeController;
      setCanCancelDocumentProcessing(true);
      promises.push(
        PresentationGenerationApi.decomposeDocuments(
          {
            referenceSnapshotId: documentAdmission.referenceSnapshotId,
            documentCount: documentAdmission.assetIds.length,
          },
          selectedLanguage,
          {
            idempotencyKey: `document-decompose:${pendingPresentationId}:${documentAdmission.referenceSnapshotId}`,
            signal: decomposeController.signal,
            onProgress: (job) => {
              const completed = job.progress?.completed_files ?? 0;
              const total =
                job.progress?.total_files ?? documentAdmission.assetIds.length;
              setLoadingState((previous) => ({
                ...previous,
                progress: total > 0 ? (completed / total) * 100 : undefined,
                progressLabel:
                  total > 0 ? `${completed}/${total}` : undefined,
                extra_info:
                  total > 0
                    ? `${completed}/${total}개 파일 처리 완료`
                    : previous.extra_info,
              }));
            },
          },
        )
      );
    }
    let responses: any[];
    try {
      responses = await Promise.all(promises);
    } finally {
      decomposeAbortControllerRef.current = null;
      setCanCancelDocumentProcessing(false);
    }
    dispatch(setPptGenUploadState({
      config,
      files: responses,
    }));
    dispatch(clearOutlines())

    const presentationId = pendingPresentationId;
    const selectedTheme = await resolveDeckTheme(selectedThemeId);
    const createResponse = await PresentationGenerationApi.createPresentation({
      presentation_id: presentationId,
      idempotency_key: `presentation-create:${presentationId}`,
      content: config?.prompt ?? "",
      n_slides: config?.slides ? parseInt(config.slides, 10) : null,
      reference_snapshot_ids: documentAdmission
        ? [documentAdmission.referenceSnapshotId]
        : [],
      language: selectedLanguage,
      tone: config?.tone,
      verbosity: config?.verbosity,
      instructions: config?.instructions || null,
      include_table_of_contents: !!config?.includeTableOfContents,
      include_title_slide: !!config?.includeTitleSlide,
      web_search: !!config?.webSearch,
      theme: selectedTheme,
    });

    dispatch(setPresentationId(createResponse.id));
    if (auto) stageAutoAuthoredGeneration(createResponse.id, resolvedMaster);
    const outlineDestination = auto
      ? `/outline?auto=1&id=${encodeURIComponent(createResponse.id)}`
      : `/outline?id=${encodeURIComponent(createResponse.id)}`;
    trackEvent(MixpanelEvent.Upload_Documents_Processed, {
      ...getUploadSnapshotProps(),
      uploaded_documents_count: documentAdmission?.assetIds.length ?? 0,
      decompose_job_count: responses.length,
      presentation_id: createResponse.id,
      destination: outlineDestination,
    });
    trackEvent(MixpanelEvent.Navigation, { from: pathname, to: outlineDestination });
    router.push(outlineDestination);
  };

  /**
   * Handles direct presentation generation without documents
   */
  const handleDirectPresentationGeneration = async (
    auto: boolean,
    resolvedMaster: CompanyStandardSelection | undefined,
  ) => {
    setLoadingState({
      isLoading: true,
      message: "개요를 생성하는 중…",
      showProgress: true,
      duration: 30,
    });

    const selectedLanguage = config?.language ?? "";

    // Use the first available layout group for direct generation
    const presentationId = pendingPresentationId;
    const selectedTheme = await resolveDeckTheme(selectedThemeId);
    const createResponse = await PresentationGenerationApi.createPresentation({
      presentation_id: presentationId,
      idempotency_key: `presentation-create:${presentationId}`,
      content: config?.prompt ?? "",
      n_slides: config?.slides ? parseInt(config.slides, 10) : null,
      language: selectedLanguage,
      tone: config?.tone,
      verbosity: config?.verbosity,
      instructions: config?.instructions || null,
      include_table_of_contents: !!config?.includeTableOfContents,
      include_title_slide: !!config?.includeTitleSlide,
      web_search: !!config?.webSearch,
      theme: selectedTheme,
    });


    dispatch(setPresentationId(createResponse.id));
    dispatch(clearOutlines())
    if (auto) stageAutoAuthoredGeneration(createResponse.id, resolvedMaster);
    const outlineDestination = auto
      ? `/outline?auto=1&id=${encodeURIComponent(createResponse.id)}`
      : `/outline?id=${encodeURIComponent(createResponse.id)}`;
    trackEvent(MixpanelEvent.Upload_Outline_Generation_Requested, {
      ...getUploadSnapshotProps(),
      presentation_id: createResponse.id,
      destination: outlineDestination,
    });
    trackEvent(MixpanelEvent.Navigation, { from: pathname, to: outlineDestination });
    router.push(outlineDestination);
  };

  /**
   * Handles errors during presentation generation
   */
  const handleGenerationError = (error: any) => {
    // This attempt can no longer be resumed safely. In particular, document
    // cleanup may replace its reference snapshot, which changes the create
    // payload while the old idempotency key still points at the prior payload.
    // Give the next explicit generate click a fresh deck id/key.
    setPendingPresentationId(crypto.randomUUID());

    // Aborts are expected when the user cancels, leaves this screen, or a
    // development refresh remounts it. Reset the local loader without
    // presenting the cancellation as a failed presentation generation.
    if (isAbortError(error)) {
      setLoadingState({
        isLoading: false,
        message: "",
        duration: 0,
        showProgress: false,
      });
      return;
    }

    // The upload flow presents this failure below. Keep it out of Next.js's
    // development runtime overlay, which treats console.error as a page error.
    console.warn("Error in upload page", error);
    setLoadingState({
      isLoading: false,
      message: "",
      duration: 0,
      showProgress: false,
    });
    const extractionFailed =
      error?.code === "uploaded_reference_extraction_failed";
    const errorTitle = extractionFailed
      ? "첨부파일을 읽지 못했습니다"
      : error?.code === "DOCUMENT_DRM_PROTECTED"
        ? "DRM 보호 파일을 읽을 수 없습니다"
        : "생성에 실패했습니다";
    const filenamePrefix = error?.filename ? `"${error.filename}" ` : "";
    const errorMessage = extractionFailed
      ? `${filenamePrefix}텍스트 추출에 실패했습니다. 잠시 후 다시 시도하거나 PDF로 변환해 업로드해 주세요.`
      : getApiErrorDisplayMessage(
          error,
          "발표자료를 시작하는 중 문제가 발생했습니다.",
        );
    notify.error(errorTitle, errorMessage);
  };

  return {
    config,
    handleConfigChange,
    loadingState,
    canCancelDocumentProcessing,
    decomposeAbortControllerRef,
    handleGeneratePresentation,
    handleReviewOutline,
    masterAdmissionError,
    isMasterResolving,
    sourceLockedSummary,
    sourceLockedArtifact,
  };
};
