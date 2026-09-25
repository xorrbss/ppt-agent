"use client";
import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
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
import {
  RasterSourceApi,
  rasterApprovalForUi,
  rasterCasBody,
  rasterPageDetail,
  rasterReviewPin,
  rasterReviewSummary,
  type RasterOperationWire,
  type RasterReviewPageDetail,
  type RasterReviewSaveRequest,
  type RasterAnalysisPin,
  type RasterPageModeRequest,
  type RasterPendingOperationWire,
} from "../../../services/api/source-content/raster-contract";
import { createDurableGenerationTask, saveDurableGenerationTask } from "../../../outline/hooks/durableGenerationTask";
import { resolveDeckTheme } from "../applyDeckTheme";
import { clearRasterOriginalPreserveResult, rememberRasterOriginalPreserveResult } from "../../../services/api/source-content/raster-result-hint";

const masterClient = new AuthoredVnextMasterClient();
const RASTER_REVIEW_RECOVERY_KEY = "presenton:raster-review-operation:v1";

type RasterReviewRecovery = {
  presentationId: string;
  operationId: string;
  referenceSnapshotId?: string;
  analysisMode?: "auto_preserve" | "reconstruct" | "auto_redesign";
  orientation: "landscape" | "portrait";
  authoredStyle: string;
  instructions: string | null;
};

const rememberRasterReview = (value: RasterReviewRecovery) => {
  try { window.sessionStorage.setItem(RASTER_REVIEW_RECOVERY_KEY, JSON.stringify(value)); } catch { /* Browser storage may be unavailable. */ }
};

const forgetRasterReview = () => {
  try { window.sessionStorage.removeItem(RASTER_REVIEW_RECOVERY_KEY); } catch { /* Browser storage may be unavailable. */ }
};

const isRasterPollTimeout = (error: unknown) =>
  error instanceof Error && error.message === "raster_analysis_timed_out";

// The server reports incremental, elapsed-time progress (analyze_redesign_pptx
// runs OCR plus a vision-LLM call per page and can take minutes on a large
// deck); prefer that live text over the generic queued/running fallback so a
// long wait keeps showing real progress instead of one static message.
const rasterWaitMessage = (
  pending: RasterPendingOperationWire,
  queuedText: string,
  runningText: string,
) => pending.progress?.message || (pending.status === "queued" ? queuedText : runningText);

// Non-blocking: advisory items never gate approval (raster_review_blockers
// excludes them) and generation proceeds regardless. Only surfaced when the
// count is small enough to skim as a toast, not a review workflow.
const RASTER_ADVISORY_NOTICE_MAX = 20;

const notifyRasterAdvisories = (operation: RasterOperationWire) => {
  const count = operation.advisory_count ?? 0;
  if (count <= 0 || count > RASTER_ADVISORY_NOTICE_MAX) return;
  notify.info(
    "검토를 권장하는 항목이 있습니다",
    `서버가 확신하지 못한 항목 ${count}개가 있습니다. 생성은 그대로 진행되며, 결과에서 해당 부분을 확인해 주세요.`,
  );
};

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
 * (validation, document processing, direct generation, error handling).
 */
export const useUploadPageState = ({
  files,
  filesRef,
  selectedThemeId,
  masterFeatureEnabled = isAuthoredVnextMasterEnabled(),
  onMasterRequired,
}: UseUploadPageStateArgs) => {
  // Allocate the server-visible deck id before the create command so the
  // workspace operation and its retries hash the exact same stable id.
  const [pendingPresentationId, setPendingPresentationId] = useState<string>(
    () => crypto.randomUUID(),
  );
  const router = useRouter();
  const workspace = useGenerationWorkspace();
  const [generationAttemptActive, setGenerationAttemptActive] = useState(false);
  const [rasterOperation, setRasterOperation] = useState<RasterOperationWire | null>(null);
  const [rasterRecovery, setRasterRecovery] = useState<RasterReviewRecovery | null>(null);
  const [rasterRetryAvailable, setRasterRetryAvailable] = useState(false);
  const [rasterAdvancedReview, setRasterAdvancedReview] = useState(false);
  const [rasterPage, setRasterPage] = useState<RasterReviewPageDetail | null>(null);
  const [isRasterPageLoading, setIsRasterPageLoading] = useState(false);
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

  const handoffRasterGeneration = useCallback((operation: RasterOperationWire, recovery: RasterReviewRecovery) => {
    const pin = operation.approval_pin;
    const referenceSnapshotId = recovery.referenceSnapshotId ?? operation.reference_snapshot_id;
    if (!pin || pin.operation_id !== operation.operation_id ||
        pin.analysis_id !== operation.analysis.analysis_id ||
        pin.analysis_revision !== operation.analysis.revision ||
        pin.analysis_digest !== operation.analysis.digest ||
        pin.source_sha256 !== operation.analysis.source.source_sha256 || !referenceSnapshotId) {
      throw new Error("raster_approval_pin_invalid");
    }
    if (recovery.analysisMode === "auto_preserve") {
      const pages = operation.analysis.pages;
      const expectedPageNumbers = pages.map((page) => page.page_number).sort((a, b) => a - b);
      const approvedPageNumbers = [...pin.full_page_preserve_pages].sort((a, b) => a - b);
      const automaticPageNumbers = [...(pin.automatic_preserve_pages ?? [])].sort((a, b) => a - b);
      const expectedRegionIds = pages.map((page) => page.full_page_region?.region_id ?? "").sort();
      const approvedRegionIds = [...pin.approved_region_ids].sort();
      if (pages.length !== operation.analysis.source.page_count ||
          pages.some((page) => page.render_mode !== "full_page_preserve" || !page.full_page_region) ||
          approvedPageNumbers.length !== pages.length || automaticPageNumbers.length !== pages.length ||
          approvedRegionIds.length !== pages.length ||
          expectedPageNumbers.some((number, index) => number !== index + 1 ||
            number !== approvedPageNumbers[index] || number !== automaticPageNumbers[index]) ||
          expectedRegionIds.some((id, index) => id !== approvedRegionIds[index])) {
        throw new Error("raster_auto_preserve_incomplete");
      }
    } else if (recovery.analysisMode === "auto_redesign") {
      // Only reachable once every page cleared analysis with zero
      // low-confidence regions; the server never issues this pin otherwise.
      const pages = operation.analysis.pages;
      const expectedPageNumbers = pages.map((page) => page.page_number).sort((a, b) => a - b);
      const automaticPageNumbers = [...(pin.automatic_redesign_pages ?? [])].sort((a, b) => a - b);
      if (pages.length !== operation.analysis.source.page_count ||
          pages.some((page) => page.render_mode !== "redesign") ||
          automaticPageNumbers.length !== pages.length ||
          expectedPageNumbers.some((number, index) => number !== index + 1 || number !== automaticPageNumbers[index])) {
        throw new Error("raster_auto_redesign_incomplete");
      }
    }
    saveDurableGenerationTask({
      ...createDurableGenerationTask({
        mode: "svg",
        sourcePresentationId: recovery.presentationId,
        timeoutMs: 30 * 60 * 1000,
        progressDurationSeconds: Math.min(600, operation.analysis.source.page_count * 25),
      }),
      authoredRequest: {
        strategy: "svg",
        language: null,
        instructions: recovery.instructions,
        visionQa: false,
        authoredStyle: recovery.authoredStyle,
        pageSize: "A4",
        orientation: recovery.orientation,
        sourceMode: "raster_reconstruction",
        rasterApprovalPin: pin,
        referenceSnapshotIds: [referenceSnapshotId],
      },
    });
    if (recovery.analysisMode === "auto_preserve") {
      rememberRasterOriginalPreserveResult(recovery.presentationId);
    } else {
      clearRasterOriginalPreserveResult(recovery.presentationId);
    }
    forgetRasterReview();
    setRasterRecovery(null);
    router.push(`/outline?auto=1&id=${encodeURIComponent(recovery.presentationId)}`);
  }, [router]);

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

  useEffect(() => {
    let saved: RasterReviewRecovery | null = null;
    try {
      const raw = window.sessionStorage.getItem(RASTER_REVIEW_RECOVERY_KEY);
      if (raw) saved = JSON.parse(raw) as RasterReviewRecovery;
    } catch { forgetRasterReview(); }
    if (!saved?.operationId || !saved.presentationId || saved.authoredStyle !== "screenshot-blue") return;
    const recovery = saved;
    const controller = new AbortController();
    setRasterAdvancedReview(recovery.analysisMode === "reconstruct");
    setPendingPresentationId(recovery.presentationId);
    setRasterRecovery(recovery);
    setConfig((previous) => ({ ...previous, pageSize: "A4", orientation: recovery.orientation }));
    dispatch(setPptGenUploadState({ authoredStyle: recovery.authoredStyle, generationStrategy: "svg", sourceContentMode: true }));
    setGenerationAttemptActive(true);
    setLoadingState({ isLoading: true, message: "이전 원본 분석을 다시 불러오는 중…", showProgress: true });
    void (async () => {
      try {
        const pending = await RasterSourceApi.get(recovery.operationId);
        const operation = await RasterSourceApi.waitForReady(pending, undefined, controller.signal);
        if (controller.signal.aborted) return;
        const resumedRecovery = {
          ...recovery,
          operationId: operation.operation_id,
          referenceSnapshotId: recovery.referenceSnapshotId ?? operation.reference_snapshot_id ?? undefined,
        };
        if (recovery.analysisMode === "auto_preserve" ||
            (recovery.analysisMode === "auto_redesign" && operation.approval_pin)) {
          notifyRasterAdvisories(operation);
          handoffRasterGeneration(operation, resumedRecovery);
          return;
        }
        const detail = await RasterSourceApi.page(operation.operation_id, 1);
        if (controller.signal.aborted) return;
        rememberRasterReview(resumedRecovery);
        setRasterRecovery(resumedRecovery);
        setRasterOperation(operation);
        setRasterPage(rasterPageDetail(operation, detail));
        setLoadingState({ isLoading: false, message: "원본 화면과 인식 내용을 검토해 주세요.", showProgress: false });
      } catch (error) {
        if (controller.signal.aborted) return;
        if (isRasterPollTimeout(error)) {
          setRasterRetryAvailable(true);
          setLoadingState({ isLoading: false, message: "원본 화면 분석이 오래 걸리고 있습니다. 분석 상태를 다시 확인해 주세요.", showProgress: false });
          return;
        }
        forgetRasterReview();
        setRasterRecovery(null);
        setGenerationAttemptActive(false);
        setLoadingState({ isLoading: false, message: "", showProgress: false });
        notify.error("이전 원본 분석을 불러오지 못했습니다", "소유 권한이나 분석 상태를 확인해 주세요.");
      }
    })();
    return () => controller.abort();
    // Recovery is intentionally keyed to this browser tab's saved operation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const startInFlightRef = useRef(false);
  const [canCancelDocumentProcessing, setCanCancelDocumentProcessing] =
    useState(false);

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
    setRasterOperation(null);
    setRasterPage(null);
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
        "세로형 생성 제한",
        "세로형 Authored 생성은 회사 표준 마스터와 함께 사용할 수 없습니다. 마스터를 해제하거나 가로 16:9를 선택해 주세요.",
      );
      return false;
    }
    if (sourceContentMode) {
      const sourceContentError = validateSourceContentFiles(selectedFiles);
      if (sourceContentError) {
        trackUploadValidationFailure(sourceContentError);
        notify.warning(
          "원본 콘텐츠 유지 파일 제한",
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
      "AI 저작 스타일을 다시 선택해 주세요",
      "선택한 스타일을 현재 카탈로그에서 확인할 수 없습니다.",
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
      notify.error("첨부파일을 확인해 주세요", "수정한 요청에 사용할 파일을 다시 첨부해 주세요.");
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
          "SVG 원본 보존 생성 제한",
          "이 서버에서는 원본 콘텐츠 유지 SVG 생성을 아직 사용할 수 없습니다.",
        );
        setLoadingState({ isLoading: false, message: "", showProgress: false });
        startInFlightRef.current = false;
        return;
      }
      runConfig = {
        ...config,
        pageSize: undefined,
        orientation: undefined,
      };
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
      if (authoredStyle !== "screenshot-blue") {
        notify.warning("템플릿을 확인해 주세요", "이미지형 A4 SVG 재구성은 현재 screenshot-blue 템플릿에서 지원합니다. 템플릿을 선택한 뒤 다시 시작해 주세요.");
        abandonRun("이미지형 A4 SVG 재구성에 필요한 screenshot-blue 템플릿을 선택해 주세요.");
        return;
      }
      try {
        setRasterOperation(null);
        setRasterPage(null);
        // "auto_redesign" is the product default: a real, editable A4 rebuild.
        // The advanced-options toggle is the only way back to the fully
        // manual, region-by-region "reconstruct" review.
        const analysisMode = rasterAdvancedReview ? "reconstruct" : "auto_redesign";
        setLoadingState({
          isLoading: true,
          message: analysisMode === "auto_redesign"
            ? "원본 화면을 편집 가능한 A4 슬라이드로 재구성할 준비를 하는 중…"
            : "원본 화면의 영역과 인식 내용을 분석하는 중…",
          showProgress: true,
        });
        const theme = await resolveDeckTheme(selectedThemeId);
        const created = await PresentationGenerationApi.createPresentation({
          presentation_id: pendingPresentationId,
          previous_presentation_id: previousGenerationAttempt.presentationId ?? undefined,
          idempotency_key: `presentation-create:${pendingPresentationId}`,
          content: "",
          n_slides: null,
          reference_snapshot_ids: [admitted.documentAdmission.referenceSnapshotId],
          language: null,
          instructions: [config.prompt, config.instructions].map((part) => part?.trim()).filter(Boolean).join("\n") || null,
          include_table_of_contents: false,
          include_title_slide: false,
          web_search: false,
          page_size: "A4",
          orientation: config.orientation ?? "landscape",
          theme,
          strategy: "svg",
        });
        workspace?.bindPresentation(created.id);
        dispatch(setPresentationId(created.id));
        dispatch(clearOutlines());
        const pending = await RasterSourceApi.start({
          reference_snapshot_id: admitted.documentAdmission.referenceSnapshotId,
          presentation_id: created.id,
          orientation: config.orientation ?? "landscape",
          analysis_mode: analysisMode,
          style_id: "screenshot-blue",
        });
        const recovery = {
          presentationId: created.id,
          operationId: pending.operation_id,
          referenceSnapshotId: admitted.documentAdmission.referenceSnapshotId,
          analysisMode,
          orientation: config.orientation ?? "landscape",
          authoredStyle,
          instructions: [config.prompt, config.instructions].map((part) => part?.trim()).filter(Boolean).join("\n") || null,
        } satisfies RasterReviewRecovery;
        rememberRasterReview(recovery);
        setRasterRecovery(recovery);
        const operation = await RasterSourceApi.waitForReady(pending, (progress) => {
          if (!aliveRef.current) return;
          setLoadingState({
            isLoading: true,
            message: analysisMode === "auto_preserve"
              ? rasterWaitMessage(progress, "원본 화면 보존을 기다리는 중…", "원본 화면을 A4로 보존하는 중…")
              : analysisMode === "auto_redesign"
              ? rasterWaitMessage(progress, "원본 화면 재구성을 기다리는 중…", "원본 화면을 편집 가능한 텍스트와 표로 재구성하는 중…")
              : rasterWaitMessage(progress, "원본 화면 분석을 기다리는 중…", "원본 화면의 영역과 인식 내용을 분석하는 중…"),
            showProgress: true,
          });
        });
        if (!aliveRef.current) return;
        rememberRasterReview({ ...recovery, operationId: operation.operation_id });
        setRasterRecovery({ ...recovery, operationId: operation.operation_id });
        if (analysisMode === "auto_preserve" ||
            (analysisMode === "auto_redesign" && operation.approval_pin)) {
          // Zero low-confidence regions: proceed straight to generation with
          // no extra confirmation, exactly as auto_preserve already does.
          notifyRasterAdvisories(operation);
          handoffRasterGeneration(operation, { ...recovery, operationId: operation.operation_id });
          return;
        }
        const detail = await RasterSourceApi.page(operation.operation_id, 1);
        setRasterOperation(operation);
        setRasterPage(rasterPageDetail(operation, detail));
        setRasterRetryAvailable(false);
        setLoadingState({ isLoading: false, message: "원본 화면과 인식 내용을 검토해 주세요.", showProgress: false });
      } catch (error) {
        if (isRasterPollTimeout(error)) {
          setRasterRetryAvailable(true);
          setLoadingState({ isLoading: false, message: "원본 화면 분석이 오래 걸리고 있습니다. 분석 상태를 다시 확인해 주세요.", showProgress: false });
        } else {
          forgetRasterReview();
          setRasterRecovery(null);
          handleGenerationError(error);
        }
      } finally {
        startInFlightRef.current = false;
      }
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
        notify.warning("SVG 원본 보존 생성 제한", "이 서버에서는 원본 콘텐츠 유지 SVG 생성을 아직 사용할 수 없습니다.");
        abandonRun("원본 SVG 생성을 시작하지 않았습니다. 자료와 생성 방식을 확인해 주세요.");
        return;
      }
      runConfig = { ...config, pageSize: undefined, orientation: undefined };
    }
    if (admitted && generationStrategy === "svg" && admitted.analysis.summary.charts > 0) {
      const reason = "원본에 차트가 있어 SVG 재구성을 시작하지 않았습니다. 차트를 이미지로 바꾼 PPTX를 사용해 주세요.";
      notify.warning(
        "차트가 포함된 페이지를 재배치할 수 없습니다",
        reason,
      );
      abandonRun(reason);
      return;
    }
    if (admitted && generationStrategy === "svg" && !admitted.analysis.documentCanvas) {
      const reason = "첨부 PPTX의 원본 크기가 지원 범위와 맞지 않아 SVG 재구성을 시작하지 않았습니다. 현재 원본 콘텐츠 유지 SVG는 A4 가로 원본만 지원합니다. A4 가로 PPTX를 사용하거나 생성 방식을 변경해 주세요.";
      notify.warning(
        "원본 캔버스를 지원하지 않습니다",
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
        notify.warning("용지 설정 확인 실패", "서버의 용지 지원 정보를 확인하지 못했습니다. 잠시 후 다시 시도해 주세요.");
        abandonRun("서버의 용지 지원 정보를 확인하지 못해 생성을 시작하지 않았습니다. 잠시 후 다시 시도해 주세요.");
        return;
      }
      if (capability.a4.enabled && runConfig.pageSize !== "A4") {
        runConfig = { ...runConfig, pageSize: "A4", orientation: runConfig.orientation ?? "landscape" };
        setConfig(runConfig);
      }
      if (runConfig.pageSize === "A4" && (!capability.a4.enabled || capability.a4.selection?.enabled !== true)) {
        notify.warning("A4 생성 제한", "선택한 생성 방식과 회사 표준 조합은 아직 A4 생성이 준비되지 않았습니다. 선택한 방향은 유지됩니다.");
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

  const loadRasterPage = useCallback(async (pageIndex: number) => {
    if (!rasterOperation) return;
    setIsRasterPageLoading(true);
    try {
      const detail = await RasterSourceApi.page(rasterOperation.operation_id, pageIndex + 1);
      setRasterPage(rasterPageDetail(rasterOperation, detail));
    } catch {
      notify.error("원본 페이지를 불러오지 못했습니다", "다시 시도해 주세요.");
    } finally {
      setIsRasterPageLoading(false);
    }
  }, [rasterOperation]);

  const saveRasterReview = useCallback(async (request: RasterReviewSaveRequest) => {
    if (!rasterOperation || request.operationId !== rasterOperation.operation_id ||
        request.revision !== rasterOperation.analysis.revision ||
        request.operationVersion !== rasterOperation.operation_version) {
      throw new Error("raster_review_stale");
    }
    const updated = await RasterSourceApi.correct(request.operationId, {
      ...rasterCasBody(request),
      region_edits: request.edits.map((edit) => ({
        region_id: edit.regionId,
        ...(edit.kind ? { role: edit.kind } : {}),
        ...(edit.confirmedText !== undefined ? { text: edit.confirmedText } : {}),
        ...(edit.sourcePixelBox ? { source_pixel_bbox: [
          edit.sourcePixelBox.x,
          edit.sourcePixelBox.y,
          edit.sourcePixelBox.width,
          edit.sourcePixelBox.height,
        ] as [number, number, number, number] } : {}),
        reviewed: edit.reviewed,
      })),
      page_edits: [],
      review_ocr: false,
      resolve_unresolved: false,
    });
    const detail = await RasterSourceApi.page(updated.operation_id, request.pageIndex + 1);
    setRasterOperation(updated);
    setRasterPage(rasterPageDetail(updated, detail));
  }, [rasterOperation]);

  const setRasterPageMode = useCallback(async (request: RasterPageModeRequest) => {
    if (!rasterOperation || request.operationId !== rasterOperation.operation_id ||
        request.revision !== rasterOperation.analysis.revision ||
        request.operationVersion !== rasterOperation.operation_version) {
      throw new Error("raster_review_stale");
    }
    const updated = await RasterSourceApi.correct(request.operationId, {
      ...rasterCasBody(request),
      region_edits: [],
      page_edits: [{
        page_number: request.pageIndex + 1,
        render_mode: request.renderMode,
        reviewed: request.reviewed,
        acknowledge_image_only: request.acknowledgeImageOnly,
        acknowledge_legibility: request.acknowledgeLegibility,
      }],
      review_ocr: false,
      resolve_unresolved: false,
    });
    const detail = await RasterSourceApi.page(updated.operation_id, request.pageIndex + 1);
    setRasterOperation(updated);
    setRasterPage(rasterPageDetail(updated, detail));
  }, [rasterOperation]);

  const selectRecommendedRasterPages = useCallback(async (
    pin: RasterAnalysisPin,
    pageIndexes: number[],
    onProgress: (selected: number, total: number) => void,
  ) => {
    if (!rasterOperation || pin.operationId !== rasterOperation.operation_id ||
        pin.revision !== rasterOperation.analysis.revision ||
        pin.operationVersion !== rasterOperation.operation_version) {
      throw new Error("raster_review_stale");
    }
    const eligible = new Set(rasterOperation.analysis.pages
      .filter((page) => page.render_mode === "reconstruct" && page.recommended_render_mode === "full_page_preserve")
      .map((page) => page.page_number - 1));
    const targets = [...new Set(pageIndexes)].filter((index) => eligible.has(index)).sort((a, b) => a - b);
    let current = rasterOperation;
    let selected = 0;
    let failure: unknown = null;
    try {
      for (let index = 0; index < targets.length; index += 5) {
        const batch = targets.slice(index, index + 5);
        current = await RasterSourceApi.correct(current.operation_id, {
          ...rasterCasBody(rasterReviewPin(current)),
          region_edits: [],
          page_edits: batch.map((pageIndex) => ({
            page_number: pageIndex + 1,
            render_mode: "full_page_preserve" as const,
            reviewed: false,
            acknowledge_image_only: false,
            acknowledge_legibility: false,
          })),
          review_ocr: false,
          resolve_unresolved: false,
        });
        selected += batch.length;
        onProgress(selected, targets.length);
      }
    } catch (error) {
      failure = error;
    }
    try {
      const latest = await RasterSourceApi.get(current.operation_id);
      if (latest.analysis) current = latest;
      const detail = await RasterSourceApi.page(current.operation_id, (rasterPage?.pageIndex ?? 0) + 1);
      setRasterOperation(current);
      setRasterPage(rasterPageDetail(current, detail));
    } catch (error) {
      if (!failure) failure = error;
    }
    if (failure) throw failure;
  }, [rasterOperation, rasterPage?.pageIndex]);

  const preserveRasterDeck = useCallback(async (
    pin: RasterAnalysisPin,
    stage: "select" | "confirm",
    onProgress: (completed: number, total: number) => void,
  ) => {
    if (!rasterOperation || pin.operationId !== rasterOperation.operation_id ||
        pin.revision !== rasterOperation.analysis.revision ||
        pin.operationVersion !== rasterOperation.operation_version) {
      throw new Error("raster_review_stale");
    }
    const pages = rasterOperation.analysis.pages;
    const total = rasterOperation.analysis.source.page_count;
    if (pages.length !== total || new Set(pages.map((page) => page.page_number)).size !== total ||
        pages.some((page) => page.page_number < 1 || page.page_number > total) ||
        (stage === "confirm" && pages.some((page) =>
          page.render_mode !== "full_page_preserve" || !page.full_page_region))) {
      throw new Error("raster_deck_pages_incomplete");
    }
    const isConfirmed = (page: typeof pages[number]) =>
      page.render_mode === "full_page_preserve" && page.full_page_region?.review_required === false &&
      page.full_page_preserve_acknowledgements.includes("image_only") &&
      page.full_page_preserve_acknowledgements.includes("legibility");
    const targets = pages.filter((page) => stage === "select"
      ? page.render_mode !== "full_page_preserve"
      : !isConfirmed(page)).map((page) => page.page_number);
    let current = rasterOperation;
    let failure: unknown = null;
    onProgress(total - targets.length, total);
    try {
      for (let index = 0; index < targets.length; index += 5) {
        const batch = targets.slice(index, index + 5);
        current = await RasterSourceApi.correct(current.operation_id, {
          ...rasterCasBody(rasterReviewPin(current)),
          region_edits: [],
          page_edits: batch.map((pageNumber) => ({
            page_number: pageNumber,
            render_mode: "full_page_preserve" as const,
            reviewed: stage === "confirm",
            acknowledge_image_only: stage === "confirm",
            acknowledge_legibility: stage === "confirm",
          })),
          review_ocr: false,
          resolve_unresolved: false,
        });
        const verified = current.analysis.pages.filter((page) => stage === "select"
          ? page.render_mode === "full_page_preserve" && Boolean(page.full_page_region)
          : isConfirmed(page)).length;
        if (batch.some((pageNumber) => {
          const page = current.analysis.pages.find((item) => item.page_number === pageNumber);
          return !page || (stage === "select"
            ? page.render_mode !== "full_page_preserve" || !page.full_page_region
            : !isConfirmed(page));
        })) throw new Error("raster_deck_batch_incomplete");
        onProgress(verified, total);
      }
      if (current.analysis.pages.length !== total || current.analysis.pages.some((page) => stage === "select"
        ? page.render_mode !== "full_page_preserve" || !page.full_page_region
        : !isConfirmed(page)) || (stage === "confirm" && !current.can_approve)) {
        throw new Error("raster_deck_review_incomplete");
      }
    } catch (error) {
      failure = error;
    }
    try {
      const latest = await RasterSourceApi.get(current.operation_id);
      if (latest.analysis) current = latest;
      const detail = await RasterSourceApi.page(current.operation_id, (rasterPage?.pageIndex ?? 0) + 1);
      setRasterOperation(current);
      setRasterPage(rasterPageDetail(current, detail));
    } catch (error) {
      if (!failure) failure = error;
    }
    if (failure) throw failure;
  }, [rasterOperation, rasterPage?.pageIndex]);

  const confirmRasterInventory = useCallback(async (pin: RasterAnalysisPin) => {
    if (!rasterOperation || pin.operationId !== rasterOperation.operation_id ||
        pin.revision !== rasterOperation.analysis.revision ||
        pin.operationVersion !== rasterOperation.operation_version) {
      throw new Error("raster_review_stale");
    }
    const updated = await RasterSourceApi.correct(pin.operationId, {
      ...rasterCasBody(pin),
      region_edits: [],
      page_edits: [],
      review_ocr: true,
      resolve_unresolved: true,
    });
    const detail = await RasterSourceApi.page(updated.operation_id, (rasterPage?.pageIndex ?? 0) + 1);
    setRasterOperation(updated);
    setRasterPage(rasterPageDetail(updated, detail));
  }, [rasterOperation, rasterPage?.pageIndex]);

  const approveRasterReview = useCallback(async (pin: RasterAnalysisPin) => {
    if (!rasterOperation || pin.operationId !== rasterOperation.operation_id ||
        pin.revision !== rasterOperation.analysis.revision ||
        pin.operationVersion !== rasterOperation.operation_version) {
      throw new Error("raster_review_stale");
    }
    const approved = await RasterSourceApi.approve(pin.operationId, rasterCasBody(pin));
    if (!approved.approval_pin || approved.analysis.digest !== rasterOperation.analysis.digest) {
      throw new Error("raster_approval_pin_invalid");
    }
    setRasterOperation(approved);
    handoffRasterGeneration(approved, rasterRecovery ?? {
      presentationId: pendingPresentationId,
      operationId: approved.operation_id,
      analysisMode: "reconstruct",
      orientation: approved.analysis.target_canvas.orientation,
      authoredStyle,
      instructions: [config.prompt, config.instructions].map((part) => part?.trim()).filter(Boolean).join("\n") || null,
    });
    return rasterApprovalForUi(approved);
  }, [rasterOperation, rasterRecovery, pendingPresentationId, config.prompt, config.instructions, authoredStyle, handoffRasterGeneration]);

  const autoPreserveExistingRaster = async () => {
    const operation = rasterOperation;
    const recovery = rasterRecovery;
    const snapshotId = recovery?.referenceSnapshotId ?? operation?.reference_snapshot_id;
    if (!operation || !recovery || !snapshotId || recovery.analysisMode === "auto_preserve" ||
        startInFlightRef.current) return;
    startInFlightRef.current = true;
    setLoadingState({ isLoading: true, message: "원본 화면 그대로 A4 생성을 준비하는 중…", showProgress: true });
    try {
      const pending = await RasterSourceApi.start({
        reference_snapshot_id: snapshotId,
        presentation_id: recovery.presentationId,
        orientation: recovery.orientation,
        analysis_mode: "auto_preserve",
        style_id: "screenshot-blue",
      });
      const autoRecovery = { ...recovery, referenceSnapshotId: snapshotId,
        analysisMode: "auto_preserve" as const, operationId: pending.operation_id };
      rememberRasterReview(autoRecovery);
      setRasterRecovery(autoRecovery);
      setRasterOperation(null);
      setRasterRetryAvailable(false);
      const approved = await RasterSourceApi.waitForReady(pending, (progress) => {
        if (aliveRef.current) setLoadingState({ isLoading: true,
          message: rasterWaitMessage(progress, "원본 화면 보존을 기다리는 중…", "원본 화면을 A4로 보존하는 중…"),
          showProgress: true });
      });
      if (!aliveRef.current) return;
      handoffRasterGeneration(approved, { ...autoRecovery, operationId: approved.operation_id });
    } catch (error) {
      if (isRasterPollTimeout(error)) {
        setRasterRetryAvailable(true);
        setLoadingState({ isLoading: false, message: "원본 화면 보존이 오래 걸리고 있습니다. 상태를 다시 확인해 주세요.", showProgress: false });
      } else {
        rememberRasterReview(recovery);
        setRasterRecovery(recovery);
        setRasterOperation(operation);
        setLoadingState({ isLoading: false, message: "원본 보존으로 전환하지 못했습니다. 기존 검토 상태를 유지합니다.", showProgress: false });
        notify.error("원본 화면 보존을 시작하지 못했습니다", "기존 검토 내용은 그대로 유지됩니다.");
      }
    } finally {
      startInFlightRef.current = false;
    }
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

  const retryRasterAnalysis = async () => {
    if (!rasterRecovery || startInFlightRef.current) return;
    startInFlightRef.current = true;
    setRasterRetryAvailable(false);
    setLoadingState({ isLoading: true, message: "원본 화면 분석 상태를 확인하는 중…", showProgress: true });
    try {
      const pending = await RasterSourceApi.get(rasterRecovery.operationId);
      const operation = await RasterSourceApi.waitForReady(pending, (progress) => {
        if (!aliveRef.current) return;
        setLoadingState({
          isLoading: true,
          message: rasterWaitMessage(progress, "원본 화면 분석을 기다리는 중…", "원본 화면의 영역과 인식 내용을 분석하는 중…"),
          showProgress: true,
        });
      });
      if (!aliveRef.current) return;
      if (rasterRecovery.analysisMode === "auto_preserve" ||
          (rasterRecovery.analysisMode === "auto_redesign" && operation.approval_pin)) {
        notifyRasterAdvisories(operation);
        handoffRasterGeneration(operation, { ...rasterRecovery, operationId: operation.operation_id });
        return;
      }
      const detail = await RasterSourceApi.page(operation.operation_id, 1);
      if (!aliveRef.current) return;
      setRasterOperation(operation);
      setRasterPage(rasterPageDetail(operation, detail));
      setLoadingState({ isLoading: false, message: "원본 화면과 인식 내용을 검토해 주세요.", showProgress: false });
    } catch (error) {
      if (!aliveRef.current) return;
      if (isRasterPollTimeout(error)) {
        setRasterRetryAvailable(true);
        setLoadingState({ isLoading: false, message: "원본 화면 분석이 오래 걸리고 있습니다. 분석 상태를 다시 확인해 주세요.", showProgress: false });
      } else {
        forgetRasterReview();
        setRasterRecovery(null);
        handleGenerationError(error);
      }
    } finally {
      startInFlightRef.current = false;
    }
  };

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
    masterAdmissionError,
    isMasterResolving,
    sourceContentSummary: sourceContentGate.summary,
    sourceContentCanvas: sourceContentGate.admission?.analysis.documentCanvas ?? null,
    isSourceContentAnalyzing: sourceContentGate.isAnalyzing,
    sourceContentLossPrompt: sourceContentGate.lossPrompt,
    resolveSourceContentLossPrompt: sourceContentGate.resolveLossPrompt,
    rasterReview: rasterOperation ? rasterReviewSummary(rasterOperation) : null,
    rasterAnalysisMode: rasterRecovery?.analysisMode ?? null,
    canAutoPreserveExistingRaster: Boolean(rasterOperation && rasterRecovery &&
      (rasterRecovery.referenceSnapshotId || rasterOperation.reference_snapshot_id) &&
      rasterRecovery.analysisMode !== "auto_preserve"),
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
  };
};
