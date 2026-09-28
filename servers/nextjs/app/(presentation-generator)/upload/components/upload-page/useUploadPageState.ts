"use client";
import { useTranslations as useA2bTranslations } from "next-intl";
import { useEffect, useRef, useState, type RefObject } from "react";
import { useRouter, usePathname } from "next/navigation";
import { useDispatch, useSelector } from "react-redux";
import {
  LanguageType,
  PresentationConfig,
  ToneType,
  VerbosityType,
} from "../../type";
import { notify } from "@/components/ui/sonner";
import {
  sourceContentSelectionErrorMessage,
  validateSourceContentFiles,
} from "../../../services/api/source-content/contract";
import {
  setPptGenUploadState,
  type CompanyStandardSelection,
} from "@/store/slices/presentationGenUpload";
import { trackEvent, MixpanelEvent } from "@/utils/mixpanel";
import { RootState } from "@/store/store";
import AuthoredStylesApi, {
  DEFAULT_AUTHORED_STYLE,
} from "../../../services/api/authored";
import { abortDocumentProcessing } from "./abortDocumentProcessing";
import { getFileCategory } from "./fileCategory";
import {
  fileIdentityOf,
  useSourceContentAdmission,
  type SourceContentAdmission,
} from "./useSourceContentAdmission";
import { getSelectedImageQuality, getSelectedTextModel } from "./llmConfigSummary";
import { LoadingState } from "./types";
import { AuthoredVnextMasterClient } from "@/lib/authored-vnext/master/client";
import {
  isAuthoredVnextMasterEnabled,
  type MasterRevisionEnvelope,
} from "@/lib/authored-vnext/master/contracts";
import { masterSelectionFromPublished } from "@/lib/authored-vnext/master/selection-storage";
import { createUploadGenerationHandlers } from "./uploadGenerationHandlers";
import { useGenerationWorkspace } from "../../../generation-workspace/GenerationWorkspaceProvider";
import { PresentationGenerationApi } from "../../../services/api/presentation-generation";
import { clearOutlines, setPresentationId } from "@/store/slices/presentationGeneration";
import { AUTH_SESSION_EXPIRED_EVENT } from "../../../services/api/api-error-handler";
import { usePreviousGenerationAttempt } from "./usePreviousGenerationAttempt";
import { rasterReviewSummary } from "../../../services/api/source-content/raster-contract";
import type { RasterReviewRecovery } from "./raster-review-recovery";
import { useRasterHandoff } from "./useRasterHandoff";
import { useRasterReviewActions } from "./useRasterReviewActions";
import { useRasterBatchPreserve } from "./useRasterBatchPreserve";
import { useRasterSessionRecovery } from "./useRasterSessionRecovery";
import { createRasterRedesignStarter } from "./createRasterRedesignStarter";

const masterClient = new AuthoredVnextMasterClient();

export type UploadMasterAdmissionError = "required" | "unavailable";

interface UseUploadPageStateArgs {
  files: File[];
  filesRef?: RefObject<File[]>;
  selectedThemeId: string | null;
  masterFeatureEnabled?: boolean;
  onMasterRequired?: () => void;
}

/**
 * Owns the presentation configuration form state and the generation workflow
 * (validation, document processing, direct generation, error handling). The
 * raster-review (image-only source redesign) sub-flow is split across
 * useRasterHandoff, useRasterReviewActions, useRasterBatchPreserve,
 * useRasterSessionRecovery and createRasterRedesignStarter.
 */
export const useUploadPageState = ({
  files,
  filesRef,
  selectedThemeId,
  masterFeatureEnabled = isAuthoredVnextMasterEnabled(),
  onMasterRequired,
}: UseUploadPageStateArgs) => {
  const tA2b = useA2bTranslations("uploadExtras");
  // Allocate the server-visible deck id before the create command so the
  // workspace operation and its retries hash the exact same stable id.
  const [pendingPresentationId, setPendingPresentationId] = useState<string>(
    () => crypto.randomUUID(),
  );
  const router = useRouter();
  const workspace = useGenerationWorkspace();
  const [generationAttemptActive, setGenerationAttemptActive] = useState(false);
  const [rasterRecovery, setRasterRecovery] = useState<RasterReviewRecovery | null>(null);
  const [rasterAdvancedReview, setRasterAdvancedReview] = useState(false);
  const recoverCreateRef = useRef<(() => Promise<void>) | null>(null);
  const previousAttemptIdRef = useRef<string | null>(null);
  const recoveringPersistedAdmissionRef = useRef(false);
  const lifecycleEpochRef = useRef(0);
  const aliveRef = useRef(true);
  useEffect(() => {
    aliveRef.current = true;
    const invalidate = () => {
      aliveRef.current = false;
      lifecycleEpochRef.current += 1;
      recoverCreateRef.current = null;
    };
    window.addEventListener(AUTH_SESSION_EXPIRED_EVENT, invalidate);
    window.addEventListener("presenton:auth-logout", invalidate);
    return () => {
      invalidate();
      window.removeEventListener(AUTH_SESSION_EXPIRED_EVENT, invalidate);
      window.removeEventListener("presenton:auth-logout", invalidate);
    };
  }, []);
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
  const generationStrategy = useSelector(
    (state: RootState) => state.pptGenUpload.generationStrategy,
  );
  const sourceContentMode = useSelector(
    (state: RootState) => state.pptGenUpload.sourceContentMode,
  );
  const sourceContentGate = useSourceContentAdmission({
    pendingPresentationId,
    setLoadingState: (state) => setLoadingState(state),
    trackUploadValidationFailure: (reason) =>
      trackUploadValidationFailure(reason),
  });
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
    pageSize: "A4",
    orientation: "landscape",
    aspectRatio: "16:9",
    codexGenerationPreset: ["codex", "chatgpt"].includes(llmConfig.LLM || "")
      ? "sol6-medium"
      : null,
  });
  const previousGenerationAttempt = usePreviousGenerationAttempt(setConfig, Boolean(workspace?.pendingAdmission));

  const handoffRasterGeneration = useRasterHandoff(router, setRasterRecovery);

  useEffect(() => {
    if (
      !sourceContentMode &&
      config.pageSize !== "A4" && (
        (config.aspectRatio === "9:16" && authoredStyle !== DEFAULT_AUTHORED_STYLE.id) ||
        (config.aspectRatio === "9:13" &&
          ![DEFAULT_AUTHORED_STYLE.id, "kbs-company-proposal"].includes(authoredStyle))
      )
    ) {
      dispatch(
        setPptGenUploadState({ authoredStyle: DEFAULT_AUTHORED_STYLE.id }),
      );
    }
  }, [authoredStyle, config.aspectRatio, config.pageSize, dispatch, sourceContentMode]);

  const [loadingState, setLoadingState] = useState<LoadingState>({
    isLoading: false,
    message: "",
    duration: 4,
    showProgress: false,
    extra_info: "",
  });
  const selectedFileIdentity = fileIdentityOf(files);
  const priorRasterSourceIdentityRef = useRef(selectedFileIdentity);
  useEffect(() => {
    if (priorRasterSourceIdentityRef.current === selectedFileIdentity) return;
    priorRasterSourceIdentityRef.current = selectedFileIdentity;
    setRasterAdvancedReview(false);
  }, [selectedFileIdentity]);
  const decomposeAbortControllerRef = useRef<AbortController | null>(null);
  const companyStandardRef = useRef(companyStandard);

  const startInFlightRef = useRef(false);
  const [canCancelDocumentProcessing, setCanCancelDocumentProcessing] =
    useState(false);

  const getUploadSnapshotProps = (
    selectedFiles: File[] = filesRef?.current ?? files,
  ) => {
    const trimmedPrompt = config.prompt.trim();
    const trimmedInstructions = (config.instructions || "").trim();
    const attachmentCategories = Array.from(
      new Set(selectedFiles.map(getFileCategory)),
    ).sort();
    const imageGenerationEnabled = !llmConfig?.DISABLE_IMAGE_GENERATION;
    const parsedSlides =
      config.slides && /^\d+$/.test(config.slides) ? Number(config.slides) : null;

    return {
      pathname,
      generation_path: selectedFiles.length > 0 ? "documents" : "prompt_only",
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
      has_attachments: selectedFiles.length > 0,
      attachments_count: selectedFiles.length,
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

  const { handleDocumentProcessing, handleDirectPresentationGeneration,
    handleGenerationError, ensureStockImageProviderReady } = createUploadGenerationHandlers({
      config, sourceContentMode, pendingPresentationId, selectedThemeId,
      previousPresentationId: previousGenerationAttempt.presentationId,
      generationStrategy, authoredVisionQa, authoredStyle, llmConfig, router, pathname,
      dispatch, setLoadingState, setCanCancelDocumentProcessing, decomposeAbortControllerRef,
      setPendingPresentationId, getUploadSnapshotProps, recoverCreateRef,
      onPresentationCreated: workspace?.bindPresentation,
      saveAdmission: workspace?.saveAdmission,
      captureLiveness: () => {
        const epoch = lifecycleEpochRef.current;
        return () => aliveRef.current && lifecycleEpochRef.current === epoch;
      },
    });

  const rasterReviewState = useRasterReviewActions({
    rasterRecovery,
    setRasterRecovery,
    pendingPresentationId,
    config,
    authoredStyle,
    aliveRef,
    startInFlightRef,
    setLoadingState,
    handoffRasterGeneration,
    handleGenerationError,
  });
  const { selectRecommendedRasterPages, preserveRasterDeck } = useRasterBatchPreserve({
    rasterOperation: rasterReviewState.rasterOperation,
    setRasterOperation: rasterReviewState.setRasterOperation,
    rasterPage: rasterReviewState.rasterPage,
    setRasterPage: rasterReviewState.setRasterPage,
  });

  useRasterSessionRecovery({
    setRasterAdvancedReview,
    setPendingPresentationId,
    setRasterRecovery,
    setConfig,
    dispatch,
    setGenerationAttemptActive,
    setLoadingState,
    setRasterRetryAvailable: rasterReviewState.setRasterRetryAvailable,
    setRasterOperation: rasterReviewState.setRasterOperation,
    setRasterPage: rasterReviewState.setRasterPage,
    handoffRasterGeneration,
  });

  useEffect(() => {
    const recovery = workspace?.pendingAdmission;
    if (!recovery || startInFlightRef.current || recoverCreateRef.current) return;
    recoveringPersistedAdmissionRef.current = true;
    setPendingPresentationId(recovery.presentationId);
    setConfig(recovery.config);
    previousAttemptIdRef.current = recovery.presentationId;
    setGenerationAttemptActive(true);
    setLoadingState({ isLoading: false, message: "이전 요청의 접수 확인이 필요합니다. 같은 요청으로 계속할 수 있습니다.", awaitingReceipt: true, showProgress: false });
    recoverCreateRef.current = async () => {
      const epoch = lifecycleEpochRef.current;
      if (!aliveRef.current) return;
      const created = await PresentationGenerationApi.createPresentation(recovery.request);
      if (!aliveRef.current || lifecycleEpochRef.current !== epoch) return;
      dispatch(setPptGenUploadState({ ...recovery.uploadState, config: recovery.config }));
      dispatch(clearOutlines());
      dispatch(setPresentationId(created.id));
      workspace.bindPresentation(created.id);
      router.push(`/outline?${recovery.auto ? "auto=1&" : ""}id=${encodeURIComponent(created.id)}`);
      recoverCreateRef.current = null;
      workspace.saveAdmission(null);
    };
  }, [workspace?.pendingAdmission, workspace?.bindPresentation, workspace?.saveAdmission, dispatch, router]);

  // A queued decomposition keeps running on FastAPI until it is explicitly
  // cancelled. Abort it when this upload screen unmounts (navigation/close).
  useEffect(() => {
    return () => abortDocumentProcessing(decomposeAbortControllerRef.current);
  }, []);

  useEffect(() => {
    if (recoveringPersistedAdmissionRef.current) return;
    sourceContentGate.reset();
    rasterReviewState.setRasterOperation(null);
    rasterReviewState.setRasterPage(null);
    dispatch(setPptGenUploadState({ sourceContentAdmission: null }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dispatch, selectedFileIdentity, sourceContentMode]);

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
          return undefined;
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

  const handleConfigChange = (key: keyof PresentationConfig, value: unknown) => {
    setConfig((prev) => ({ ...prev, [key]: value } as PresentationConfig));
  };

  /**
   * Validates the current configuration and files
   * @returns boolean indicating if the configuration is valid
   */
  const validateConfiguration = (selectedFiles: File[]): boolean => {
        if (
            !sourceContentMode &&
            config.pageSize !== "A4" &&
            config.aspectRatio !== "16:9" &&
            companyStandard
        ) {
      trackUploadValidationFailure("portrait_company_standard_unsupported");
      notify.warning(
        tA2b("portraitGenerationLimitation"),
        tA2b("portraitAuthoredGenerationCannotBeUsedWith"),
      );
      return false;
    }
    if (sourceContentMode) {
      const sourceContentError = validateSourceContentFiles(selectedFiles);
      if (sourceContentError) {
        trackUploadValidationFailure(sourceContentError);
        notify.warning(
          tA2b("fileLimitForSourceContentPreservation"),
          sourceContentSelectionErrorMessage(sourceContentError),
        );
        return false;
      }
      // Source SVG binds its canvas from the analysed source manifest. The
      // form's default A4 settings (or a prior aspect ratio) do not override it.
      // Check server capability and the analysed canvas in startPresentation.
      return true;
    }
    if (!config.language) {
      trackUploadValidationFailure("language_missing");
      notify.warning("언어를 선택해 주세요", "언어를 선택해 주세요.");
      return false;
    }

    if (selectedFiles.length > 0 && config.language === LanguageType.Auto) {
      trackUploadValidationFailure("language_auto_with_documents");
      notify.warning("언어를 선택해 주세요", "업로드한 문서를 처리하기 전에 언어를 선택해 주세요.");
      return false;
    }

    if (!config.prompt.trim() && selectedFiles.length === 0) {
      trackUploadValidationFailure("prompt_or_document_missing");
      notify.warning("입력이 필요합니다", "프롬프트를 입력하거나 문서를 하나 이상 업로드해 주세요.");
      return false;
    }
    return true;
  };

  const ensureSourceContentAuthoredStyle = async (): Promise<boolean> => {
    try {
      const styles = await AuthoredStylesApi.getStyles();
      if (styles.some((style) => style.id === authoredStyle)) return true;
    } catch {
      // A source-content run cannot silently substitute another template.
    }
    notify.warning(
      tA2b("pleaseSelectTheAiAuthoredStyleAgain"),
      tA2b("theSelectedStyleIsNotInThe"),
    );
    return false;
  };

  /**
   * Handles the presentation generation process
   */
  const startPresentation = async (auto: boolean) => {
    let runConfig = config;
    if (!aliveRef.current || startInFlightRef.current || previousGenerationAttempt.blocked) return;
    const selectedFiles = filesRef?.current ?? files;
    if (previousGenerationAttempt.info?.attachment_file_names.length && selectedFiles.length === 0) {
      notify.error(tA2b("pleaseCheckTheAttachments"), tA2b("attachTheFilesForTheRevisedRequest"));
      return;
    }
    const selectedFileIdentity = fileIdentityOf(selectedFiles);
    if (!validateConfiguration(selectedFiles)) return;
    startInFlightRef.current = true;
    if (sourceContentMode && generationStrategy === "svg" && config.pageSize !== "A4") {
      setLoadingState({
        isLoading: true,
        message: "SVG 원본 보존 지원을 확인하고 있습니다.",
        showProgress: false,
      });
      const capability = await AuthoredStylesApi.getDocumentCanvasCapabilities({
        generationStrategy: "svg",
        authoredStyle,
        sourceContentMode: true,
        hasCompanyStandard: false,
      });
      if (!capability.sourceContentSvg.enabled) {
        trackUploadValidationFailure(
          capability.sourceContentSvg.reason || "svg_source_content_unsupported",
        );
        notify.warning(
          tA2b("svgSourcePreservationGenerationLimitation"),
          tA2b("sourceContentPreservingSvgGenerationIsNot"),
        );
        setLoadingState({ isLoading: false, message: "", showProgress: false });
        startInFlightRef.current = false;
        return;
      }
    }
    setGenerationAttemptActive(true);
    workspace?.beginSession({
      presentationId: pendingPresentationId,
      prompt: config.prompt,
      files: selectedFiles.map((file) => ({ name: file.name, size: file.size })),
      settings: [sourceContentMode ? "원본 콘텐츠 유지" : `${config.slides}장`, config.language ?? "", authoredStyle],
    }, previousAttemptIdRef.current ?? previousGenerationAttempt.presentationId);
    previousAttemptIdRef.current = pendingPresentationId;
    setLoadingState({ isLoading: true, message: "요청과 생성 설정을 확인하고 있습니다.", showProgress: false });
    // The analysis raises the progress overlay and the generation keeps it up,
    // so every path that gives up in between has to lower it again. Going
    // through one helper is what keeps a new early return from stranding the
    // screen on "analysing" forever.
    const abandonRun = (reason: string) => {
      setLoadingState({
        isLoading: false,
        message: reason,
        duration: 0,
        showProgress: false,
      });
      startInFlightRef.current = false;
    };
    // One click carries the source-content run all the way through: analyse,
    // then generate. The analysis is still a gate, but it only stops the run
    // when it found something the user would not want to lose silently --
    // `admitted` is read from the return value because the state setter behind
    // it has not re-rendered within this same click.
    let admitted: SourceContentAdmission | null = sourceContentMode
      ? sourceContentGate.admission
      : null;
    if (
      sourceContentMode &&
      (generationStrategy === "svg" || admitted?.fileIdentity !== selectedFileIdentity)
    ) {
      try {
        admitted = await sourceContentGate.analyze(selectedFiles, {
          // The extractor revision is sealed into the manifest identity. Re-run
          // source SVG analysis on the owned snapshot so a browser-cached pin
          // never masks a newer extractor after a server restart.
          forceFresh: generationStrategy === "svg",
        });
      } catch (error) {
        handleGenerationError(error);
        startInFlightRef.current = false;
        return;
      }
      if (!admitted) {
        abandonRun("원본 자료를 분석하지 못해 생성을 시작하지 않았습니다. 첨부파일을 확인해 주세요.");
        return;
      }
    }
    const imageOnlySource = Boolean(admitted &&
      admitted.analysis.summary.sourceUnits > 0 &&
      admitted.analysis.summary.textBlocks === 0 &&
      admitted.analysis.summary.tables === 0 &&
      admitted.analysis.summary.charts === 0 &&
      admitted.analysis.summary.imageOccurrences >= admitted.analysis.summary.sourceUnits);
    if (sourceContentMode && generationStrategy === "svg" && config.pageSize === "A4" && imageOnlySource && admitted) {
      await createRasterRedesignStarter({
        config,
        authoredStyle,
        rasterAdvancedReview,
        selectedThemeId,
        pendingPresentationId,
        previousPresentationId: previousGenerationAttempt.presentationId,
        bindPresentation: workspace?.bindPresentation,
        dispatch,
        setRasterOperation: rasterReviewState.setRasterOperation,
        setRasterPage: rasterReviewState.setRasterPage,
        setRasterRecovery,
        setRasterRetryAvailable: rasterReviewState.setRasterRetryAvailable,
        setLoadingState,
        aliveRef,
        handoffRasterGeneration,
        handleGenerationError,
        abandonRun,
      })(admitted);
      startInFlightRef.current = false;
      return;
    }
    if (sourceContentMode && generationStrategy === "svg" && config.pageSize === "A4") {
      const capability = await AuthoredStylesApi.getDocumentCanvasCapabilities({
        generationStrategy: "svg",
        authoredStyle,
        sourceContentMode: true,
        hasCompanyStandard: false,
      });
      if (!capability.sourceContentSvg.enabled) {
        notify.warning(tA2b("svgSourcePreservationGenerationLimitation"), tA2b("sourceContentPreservingSvgGenerationIsNot"));
        abandonRun("원본 SVG 생성을 시작하지 않았습니다. 자료와 생성 방식을 확인해 주세요.");
        return;
      }
    }
    if (admitted && generationStrategy === "svg" && admitted.analysis.summary.charts > 0) {
      const reason = "원본에 차트가 있어 SVG 재구성을 시작하지 않았습니다. 차트를 이미지로 바꾼 PPTX를 사용해 주세요.";
      notify.warning(
        tA2b("pagesContainingChartsCannotBeRearranged"),
        reason,
      );
      abandonRun(reason);
      return;
    }
    if (admitted && generationStrategy === "authored" && !(await sourceContentGate.confirmLosses(admitted.analysis.summary))) {
      abandonRun("원본 콘텐츠 처리 변경에 동의하지 않아 생성을 시작하지 않았습니다. 자료와 처리 방식을 확인해 주세요.");
      return;
    }
    if (sourceContentMode && generationStrategy === "authored" && !(await ensureSourceContentAuthoredStyle())) {
      abandonRun("선택한 스타일을 현재 카탈로그에서 확인할 수 없어 생성을 시작하지 않았습니다. 스타일을 다시 선택해 주세요.");
      return;
    }
    const resolvedMaster = generationStrategy === "authored"
      ? await resolveMasterForGeneration()
      : undefined;
    if (resolvedMaster === null) {
      abandonRun("회사 표준 마스터를 확인하지 못해 생성을 시작하지 않았습니다. 회사 표준 설정을 확인해 주세요.");
      return;
    }
    if (!(sourceContentMode && generationStrategy === "svg")) {
      // Check even before the settings effect has upgraded the initial config.
      const capability = await AuthoredStylesApi.getDocumentCanvasCapabilities({
        generationStrategy,
        authoredStyle,
        sourceContentMode,
        // Match stageAutoAuthoredGeneration: source restyle omits masterPin.
        hasCompanyStandard: Boolean(resolvedMaster) && !sourceContentMode,
      });
      if (capability.a4.reason === "document_canvas_capability_unavailable") {
        notify.warning(tA2b("couldNotCheckPageSettings"), tA2b("couldNotCheckTheServerSPaper"));
        abandonRun("서버의 용지 지원 정보를 확인하지 못해 생성을 시작하지 않았습니다. 잠시 후 다시 시도해 주세요.");
        return;
      }
      if (capability.a4.enabled && runConfig.pageSize !== "A4") {
        runConfig = { ...runConfig, pageSize: "A4", orientation: runConfig.orientation ?? "landscape" };
        setConfig(runConfig);
      }
      if (runConfig.pageSize === "A4" && (!capability.a4.enabled || capability.a4.selection?.enabled !== true)) {
        notify.warning(tA2b("a4GenerationLimitation"), tA2b("a4GenerationIsNotReadyForThe"));
        abandonRun("선택한 생성 방식과 회사 표준 조합은 A4 생성을 지원하지 않아 시작하지 않았습니다. 생성 방식과 회사 표준 설정을 확인해 주세요.");
        return;
      }
    }
    trackEvent(
      MixpanelEvent.Upload_Generation_Started,
      getUploadSnapshotProps(selectedFiles),
    );


    const isStockProviderReady = sourceContentMode || await ensureStockImageProviderReady();
    if (!isStockProviderReady) {
      trackUploadValidationFailure("stock_image_provider_unreachable");
      abandonRun("이미지 공급자에 연결할 수 없어 생성을 시작하지 않았습니다. 이미지 공급자 설정과 연결을 확인해 주세요.");
      return;
    }

    try {
      const hasUploadedAssets = selectedFiles.length > 0;

      if (hasUploadedAssets) {
        await handleDocumentProcessing(
          selectedFiles,
          auto,
          resolvedMaster,
          admitted,
          runConfig,
        );
      } else {
        await handleDirectPresentationGeneration(auto, resolvedMaster, runConfig);
      }
    } catch (error) {
      handleGenerationError(error);
    } finally {
      startInFlightRef.current = false;
    }
  };

  const handleGeneratePresentation = () => startPresentation(true);
  const handleReviewOutline = () => startPresentation(false);

  const recoverCreate = async () => {
    if (!aliveRef.current || startInFlightRef.current || !recoverCreateRef.current) return;
    startInFlightRef.current = true;
    setLoadingState({ isLoading: true, message: "같은 요청의 접수 상태를 확인하고 있습니다.", showProgress: false });
    try { await recoverCreateRef.current(); }
    catch (error) { if (aliveRef.current) handleGenerationError(error); }
    finally { startInFlightRef.current = false; }
  };

  const editGenerationRequest = () => {
    if (startInFlightRef.current || loadingState.awaitingReceipt) return;
    setGenerationAttemptActive(false);
  };

  return {
    config,
    previousGenerationAttempt,
    generationAttemptActive,
    recoverCreate,
    rasterRetryAvailable: rasterReviewState.rasterRetryAvailable,
    rasterAdvancedReview,
    setRasterAdvancedReview,
    retryRasterAnalysis: rasterReviewState.retryRasterAnalysis,
    editGenerationRequest,
    handleConfigChange,
    loadingState,
    canCancelDocumentProcessing,
    decomposeAbortControllerRef,
    handleGeneratePresentation,
    handleReviewOutline,
    masterAdmissionError,
    isMasterResolving,
    sourceContentSummary: sourceContentGate.summary,
    sourceContentCanvas: sourceContentGate.admission?.analysis.documentCanvas ?? null,
    isSourceContentAnalyzing: sourceContentGate.isAnalyzing,
    sourceContentLossPrompt: sourceContentGate.lossPrompt,
    resolveSourceContentLossPrompt: sourceContentGate.resolveLossPrompt,
    rasterReview: rasterReviewState.rasterOperation ? rasterReviewSummary(rasterReviewState.rasterOperation) : null,
    rasterAnalysisMode: rasterRecovery?.analysisMode ?? null,
    canAutoPreserveExistingRaster: Boolean(rasterReviewState.rasterOperation && rasterRecovery &&
      (rasterRecovery.referenceSnapshotId || rasterReviewState.rasterOperation.reference_snapshot_id) &&
      rasterRecovery.analysisMode !== "auto_preserve"),
    autoPreserveExistingRaster: rasterReviewState.autoPreserveExistingRaster,
    rasterPage: rasterReviewState.rasterPage,
    isRasterPageLoading: rasterReviewState.isRasterPageLoading,
    loadRasterPage: rasterReviewState.loadRasterPage,
    saveRasterReview: rasterReviewState.saveRasterReview,
    setRasterPageMode: rasterReviewState.setRasterPageMode,
    selectRecommendedRasterPages,
    preserveRasterDeck,
    confirmRasterInventory: rasterReviewState.confirmRasterInventory,
    approveRasterReview: rasterReviewState.approveRasterReview,
  };
};
