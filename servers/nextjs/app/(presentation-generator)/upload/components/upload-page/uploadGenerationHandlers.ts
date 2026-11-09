"use client";
import { commonHeaderDesignForConfig, hasCommonHeader } from "@/lib/common-header-design";

import type { Dispatch, SetStateAction, MutableRefObject } from "react";
import type { useRouter } from "next/navigation";
import type { Dispatch as ReduxDispatch, UnknownAction } from "redux";
import { clearOutlines, setPresentationId } from "@/store/slices/presentationGeneration";
import { setPptGenUploadState, type CompanyStandardSelection } from "@/store/slices/presentationGenUpload";
import type { RootState } from "@/store/store";
import { requestVerbosity } from "../../utils/requestVerbosity";
import { documentCanvasRequestForConfig, LanguageType, type PresentationConfig } from "../../type";
import { documentCanvasForOrientation } from "@/lib/document-canvas";
import { notify } from "@/components/ui/sonner";
import { uploadExtraTranslator } from "./uploadExtraTranslator";
import { isAbortError, PresentationGenerationApi, type DocumentUploadAdmission } from "../../../services/api/presentation-generation";
import { SOURCE_CONTENT_POLICY, type SourceContentAnalysisResponseV1 } from "../../../services/api/source-content/contract";
import { getApiErrorDisplayMessage } from "../../../services/api/api-error-handler";
import { ImagesApi } from "../../../services/api/images";
import { resolveDeckTheme } from "../applyDeckTheme";
import { clearDurableGenerationTask, createDurableGenerationTask, findDurableGenerationTask, saveDurableGenerationTask } from "../../../outline/hooks/durableGenerationTask";
import type { GenerationAdmissionRecovery } from "../../../generation-workspace/GenerationWorkspaceProvider";
import type { SourceContentAdmission } from "./useSourceContentAdmission";
import type { LoadingState } from "./types";

interface GenerationHandlersContext {
  config: PresentationConfig;
  sourceContentMode: boolean;
  pendingPresentationId: string;
  previousPresentationId?: string | null;
  selectedThemeId: string | null;
  authoredStyle: string;
  llmConfig: RootState["userConfig"]["llm_config"];
  router: ReturnType<typeof useRouter>;
  dispatch: ReduxDispatch<UnknownAction>;
  setLoadingState: Dispatch<SetStateAction<LoadingState>>;
  setCanCancelDocumentProcessing: Dispatch<SetStateAction<boolean>>;
  decomposeAbortControllerRef: MutableRefObject<AbortController | null>;
  setPendingPresentationId: Dispatch<SetStateAction<string>>;
  recoverCreateRef: MutableRefObject<(() => Promise<void>) | null>;
  onPresentationCreated?: (id: string) => void;
  saveAdmission?: (recovery: GenerationAdmissionRecovery | null) => void;
  captureLiveness: () => () => boolean;
}

const STOCK_IMAGE_PROVIDERS = new Set(["pexels", "pixabay"]);

// Every asynchronous branch receives the same click-time canvas configuration.
export function createUploadGenerationHandlers({
  config: initialConfig, sourceContentMode, pendingPresentationId, previousPresentationId, selectedThemeId,
  authoredStyle, llmConfig, router,
  dispatch, setLoadingState, setCanCancelDocumentProcessing, decomposeAbortControllerRef,
  setPendingPresentationId, recoverCreateRef, onPresentationCreated, saveAdmission, captureLiveness,
}: GenerationHandlersContext) {
  const sourceContentCanvasForConfig = (sourceContent: SourceContentAnalysisResponseV1, config: PresentationConfig) =>
    config.pageSize === "A4" && config.orientation
      ? documentCanvasForOrientation(config.orientation)
      : config.sourceCanvas ? sourceContent.documentCanvas : undefined;

  const stageAutoGeneration = (
    presentationId: string,
    master: CompanyStandardSelection | undefined,
    sourceContent: SourceContentAnalysisResponseV1 | null = null,
    config: PresentationConfig = initialConfig,
  ) => {
    const isLive = captureLiveness();
    if (!isLive()) return;
    const sourceContentInstructions = [config?.prompt, config?.instructions]
      .map((value) => value?.trim())
      .filter((value): value is string => Boolean(value))
      .join("\n");
    const sourceContentCanvas = sourceContent && sourceContentCanvasForConfig(sourceContent, config);
    if (sourceContent) {
      dispatch(setPptGenUploadState({
        sourceContentMode: true,
        sourceContentAdmission: {
          manifestPin: sourceContent.manifestPin,
          referenceSnapshotId: sourceContent.referenceSnapshotBinding.referenceSnapshotId,
          ...(sourceContentCanvas
            ? { documentCanvas: sourceContentCanvas }
            : {}),
        },
      }));
    }
    const requestedSlides = config.slides ? Number.parseInt(config.slides, 10) : 8;
    const estimatedSlides = Number.isFinite(requestedSlides) ? requestedSlides : 8;
    const baseSeconds = Math.max(120, estimatedSlides * 25);
    const progressDurationSeconds = Math.min(600, baseSeconds);
    saveDurableGenerationTask({
      ...createDurableGenerationTask({
        mode: "svg",
        sourcePresentationId: presentationId,
        timeoutMs: 30 * 60 * 1000,
        progressDurationSeconds,
      }),
      authoredRequest: {
        strategy: "svg" as const,
        commonHeaderMode: config.commonHeaderMode ?? "uniform",
        commonHeaderDesign: commonHeaderDesignForConfig(config),
        commonHeaderCoverException: hasCommonHeader(config.commonHeaderMode) && (config.commonHeaderCoverException ?? false),
        language:
          config.language && config.language !== LanguageType.Auto
            ? config.language
            : null,
        ...(sourceContent ? { instructions: sourceContentInstructions || null } : {}),
        authoredStyle,
        ...(config.svgDesignReferenceSnapshotId ? { svgDesignReferenceSnapshotId: config.svgDesignReferenceSnapshotId } : {}),
      ...(config.svgTemplatePin ? { svgTemplatePin: config.svgTemplatePin, svgTemplateMode: config.svgTemplateMode, svgTemplateFrameMap: config.svgTemplateFrameMap } : {}),
        ...(sourceContentCanvas
          ? { documentCanvas: sourceContentCanvas }
          : {}),
        ...(config.sourceCanvas ? { sourceCanvas: true }
          : config.svgCanvas ? { svgCanvas: config.svgCanvas }
          : config.pageSize === "A4" && config.orientation
            ? { pageSize: "A4" as const, orientation: config.orientation }
            : { aspectRatio: config.aspectRatio }),
        ...(config.codexGenerationPreset
          ? { codexGenerationPreset: config.codexGenerationPreset }
          : {}),
        ...(sourceContent
          ? {
              sourceMode: "source_content_restyle" as const,
              sourceContentManifestPin: sourceContent.manifestPin,
              contentPolicy: SOURCE_CONTENT_POLICY,
            }
          : {}),
        ...(master && !sourceContent
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
      const t = uploadExtraTranslator();
      notify.error(
        t("cannotConnectImageProvider"),
        error?.message ||
        t("cannotConnectSelectedProvider", { provider: selectedProvider })
      );
      return false;
    }
  };

  /**
   * Handles document processing
   */
  const handleDocumentProcessing = async (
    selectedFiles: File[],
    auto: boolean,
    resolvedMaster: CompanyStandardSelection | undefined,
    admittedSourceContent: SourceContentAdmission | null = null,
    config: PresentationConfig = initialConfig,
  ) => {
    const isLive = captureLiveness();
    if (!isLive()) return;
    setLoadingState({
      isLoading: true,
      message: sourceContentMode
        ? "원본 분석을 마치고 SVG 배치를 준비하는 중…"
        : "문서를 처리하는 중…",
      showProgress: true,
      duration: 90,
      progress: 0,
      progressLabel: `0/${selectedFiles.length}`,
      extra_info: sourceContentMode
        ? "원문·표·이미지를 유지하며 SVG 생성과 검사를 진행합니다."
        : selectedFiles.length > 0 ? "용량이 큰 문서는 몇 분 정도 걸릴 수 있습니다." : "",
    });

    let documentAdmission: DocumentUploadAdmission | null = admittedSourceContent?.documentAdmission ?? null;

    if (selectedFiles.length > 0 && !documentAdmission) {
      documentAdmission = await PresentationGenerationApi.uploadDoc(
        selectedFiles,
        pendingPresentationId,
      );
      if (!isLive()) return;
    }

    const sourceContentAnalysis = admittedSourceContent?.analysis ?? null;
    if (sourceContentMode && !sourceContentAnalysis) {
      throw new Error("source_content_manifest_invalid");
    }
    const sourceContentCanvas = sourceContentAnalysis && sourceContentCanvasForConfig(sourceContentAnalysis, config);
    if (sourceContentAnalysis) {
      dispatch(setPptGenUploadState({
        sourceContentMode: true,
        sourceContentAdmission: {
          manifestPin: sourceContentAnalysis.manifestPin,
          referenceSnapshotId: sourceContentAnalysis.referenceSnapshotBinding.referenceSnapshotId,
          ...(sourceContentCanvas ? { documentCanvas: sourceContentCanvas } : {}),
        },
      }));
    }

    const selectedLanguage = config?.language ?? "";

    const promises: Promise<any>[] = [];

    if (documentAdmission && !sourceContentMode) {
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
              if (!isLive()) return;
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
    if (!isLive()) return;
    dispatch(setPptGenUploadState({
      config,
      files: responses,
    }));
    dispatch(clearOutlines())

    const presentationId = pendingPresentationId;
    const selectedTheme = await resolveDeckTheme(selectedThemeId);
    if (!isLive()) return;
    const sourceContentInstructions = [config?.prompt, config?.instructions]
      .map((value) => value?.trim())
      .filter((value): value is string => Boolean(value))
      .join("\n");
    const createRequest = {
      presentation_id: presentationId,
      previous_presentation_id: previousPresentationId ?? undefined,
      idempotency_key: `presentation-create:${presentationId}`,
      content: sourceContentMode ? "" : (config?.prompt ?? ""),
      n_slides: sourceContentMode ? null : (config?.slides ? parseInt(config.slides, 10) : null),
      reference_snapshot_ids: documentAdmission
        ? [documentAdmission.referenceSnapshotId]
        : [],
      language: sourceContentMode ? null : selectedLanguage,
      tone: sourceContentMode ? undefined : config?.tone,
      verbosity: sourceContentMode ? undefined : requestVerbosity(config),
      instructions: sourceContentMode
        ? (sourceContentInstructions || null)
        : (config?.instructions || null),
      include_table_of_contents: sourceContentMode ? false : !!config?.includeTableOfContents,
      include_title_slide: sourceContentMode ? false : !!config?.includeTitleSlide,
      web_search: sourceContentMode ? false : !!config?.webSearch,
      common_header_mode: config.commonHeaderMode ?? "uniform",
      common_header_design: commonHeaderDesignForConfig(config),
      common_header_cover_exception: hasCommonHeader(config.commonHeaderMode) && (config.commonHeaderCoverException ?? false),
      ...(config.svgDesignReferenceSnapshotId ? { svg_design_reference_snapshot_id: config.svgDesignReferenceSnapshotId } : {}),
      ...(config.svgTemplatePin ? { svgTemplatePin: config.svgTemplatePin, svgTemplateMode: config.svgTemplateMode, svgTemplateFrameMap: config.svgTemplateFrameMap } : {}),
      ...documentCanvasRequestForConfig(config),
      theme: selectedTheme,
      strategy: "svg" as const,
    } satisfies Parameters<typeof PresentationGenerationApi.createPresentation>[0];

    const submitPreparedRequest = async () => {
    if (!isLive()) return;
    const createResponse = await PresentationGenerationApi.createPresentation(createRequest);
    if (!isLive()) return;
    onPresentationCreated?.(createResponse.id);
    dispatch(setPresentationId(createResponse.id));
    const outlineDestination = auto
      ? `/outline?auto=1&id=${encodeURIComponent(createResponse.id)}`
      : `/outline?id=${encodeURIComponent(createResponse.id)}`;
    router.push(outlineDestination);
    recoverCreateRef.current = null;
    saveAdmission?.(null);
    };
    recoverCreateRef.current = submitPreparedRequest;
    if (auto) stageAutoGeneration(presentationId, resolvedMaster, sourceContentAnalysis, config);
    saveAdmission?.({ presentationId, auto, request: createRequest, config, uploadState: {
      sourceContentMode, authoredStyle,
      companyStandard: resolvedMaster ?? null,
      sourceContentAdmission: sourceContentAnalysis ? {
        manifestPin: sourceContentAnalysis.manifestPin,
        referenceSnapshotId: sourceContentAnalysis.referenceSnapshotBinding.referenceSnapshotId,
        ...(sourceContentCanvas
          ? { documentCanvas: sourceContentCanvas }
          : {}),
      } : null,
    } });
    await submitPreparedRequest();
  };

  /**
   * Handles direct presentation generation without documents
   */
  const handleDirectPresentationGeneration = async (
    auto: boolean,
    resolvedMaster: CompanyStandardSelection | undefined,
    config: PresentationConfig = initialConfig,
  ) => {
    const isLive = captureLiveness();
    if (!isLive()) return;
    setLoadingState({
      isLoading: true,
      message: "개요를 생성하는 중…",
      showProgress: true,
      duration: 30,
    });

    const selectedLanguage = config?.language ?? "";
    dispatch(setPptGenUploadState({ config }));

    // Use the first available layout group for direct generation
    const presentationId = pendingPresentationId;
    const selectedTheme = await resolveDeckTheme(selectedThemeId);
    if (!isLive()) return;
    const createRequest = {
      presentation_id: presentationId,
      previous_presentation_id: previousPresentationId ?? undefined,
      idempotency_key: `presentation-create:${presentationId}`,
      content: config?.prompt ?? "",
      n_slides: config?.slides ? parseInt(config.slides, 10) : null,
      language: selectedLanguage,
      tone: config?.tone,
      verbosity: requestVerbosity(config),
      instructions: config?.instructions || null,
      include_table_of_contents: !!config?.includeTableOfContents,
      include_title_slide: !!config?.includeTitleSlide,
      web_search: !!config?.webSearch,
      common_header_mode: config.commonHeaderMode ?? "uniform",
      common_header_design: commonHeaderDesignForConfig(config),
      common_header_cover_exception: hasCommonHeader(config.commonHeaderMode) && (config.commonHeaderCoverException ?? false),
      ...(config.svgDesignReferenceSnapshotId ? { svg_design_reference_snapshot_id: config.svgDesignReferenceSnapshotId } : {}),
      ...(config.svgTemplatePin ? { svgTemplatePin: config.svgTemplatePin, svgTemplateMode: config.svgTemplateMode, svgTemplateFrameMap: config.svgTemplateFrameMap } : {}),
      ...documentCanvasRequestForConfig(config),
      theme: selectedTheme,
      strategy: "svg" as const,
    } satisfies Parameters<typeof PresentationGenerationApi.createPresentation>[0];


    const submitPreparedRequest = async () => {
    if (!isLive()) return;
    const createResponse = await PresentationGenerationApi.createPresentation(createRequest);
    if (!isLive()) return;
    onPresentationCreated?.(createResponse.id);
    dispatch(setPresentationId(createResponse.id));
    dispatch(clearOutlines())
    const outlineDestination = auto
      ? `/outline?auto=1&id=${encodeURIComponent(createResponse.id)}`
      : `/outline?id=${encodeURIComponent(createResponse.id)}`;
    router.push(outlineDestination);
    recoverCreateRef.current = null;
    saveAdmission?.(null);
    };
    recoverCreateRef.current = submitPreparedRequest;
    if (auto) stageAutoGeneration(presentationId, resolvedMaster, null, config);
    saveAdmission?.({ presentationId, auto, request: createRequest, config, uploadState: {
      sourceContentMode: false, sourceContentAdmission: null, authoredStyle,
      companyStandard: resolvedMaster ?? null,
    } });
    await submitPreparedRequest();
  };

  /**
   * Handles errors during presentation generation
   */
  const handleGenerationError = (error: any) => {
    if (!captureLiveness()()) return;
    // A create request may already have committed even if its response was lost.
    // Replay only its exact prepared payload/key; never upload a new snapshot
    // or allocate a second deck until the receipt is known.
    const receiptUncertain = Boolean(recoverCreateRef.current) &&
      (error instanceof TypeError || !error?.status || error.status >= 500);
    if (!receiptUncertain) {
      recoverCreateRef.current = null;
      saveAdmission?.(null);
      const staged = findDurableGenerationTask(pendingPresentationId);
      if (staged && !staged.taskId) clearDurableGenerationTask(staged.idempotencyKey);
      setPendingPresentationId(crypto.randomUUID());
    }

    // Aborts are expected when the user cancels, leaves this screen, or a
    // development refresh remounts it. Reset the local loader without
    // presenting the cancellation as a failed presentation generation.
    if (isAbortError(error) && !receiptUncertain) {
      setLoadingState({
        isLoading: false,
        message: "자료 처리를 취소했습니다.",
        duration: 0,
        showProgress: false,
      });
      return;
    }

    // The upload flow presents this failure below. Keep it out of Next.js's
    // development runtime overlay, which treats console.error as a page error.
    console.warn("Error in upload page", error);
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
    setLoadingState({
      isLoading: false,
      message: receiptUncertain ? "요청의 접수 여부를 확인하지 못했습니다. 같은 요청으로 접수를 확인해 주세요." : errorMessage,
      duration: 0,
      showProgress: false,
      awaitingReceipt: receiptUncertain,
    });
    notify.error(errorTitle, errorMessage);
  };

  return { handleDocumentProcessing, handleDirectPresentationGeneration,
    handleGenerationError, ensureStockImageProviderReady };
}
