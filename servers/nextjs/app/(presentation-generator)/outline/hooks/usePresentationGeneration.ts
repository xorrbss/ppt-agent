import { useSavedSvgTemplate } from "./useSavedSvgTemplate";
import { AUTH_SESSION_EXPIRED_EVENT } from "../../services/api/api-error-handler";
import { commonHeaderDesignForConfig } from "@/lib/common-header-design";
import { isAmbiguousGenerationTransportError, isRetryableGenerationStartError, hasRecoverableChatFirstWorkspace, isCompanyStandardAdmissionError } from "./generationStartErrors";
import { useTranslations as useA2bTranslations } from "next-intl";
import { useState, useCallback, useEffect, useRef } from "react";
import { useDispatch, useSelector } from "react-redux";
import { RootState } from "@/store/store";
import { useRouter } from "next/navigation";
import { notify } from "@/components/ui/sonner";
import { clearPresentationData } from "@/store/slices/presentationGeneration";
import { PresentationGenerationApi } from "../../services/api/presentation-generation";
import { LoadingState } from "../types/index";
import { LanguageType } from "../../upload/type";
import {
  ApiError,
  getApiErrorDisplayMessage,
  isPresentationGenerationSlideLimitError,
  isRetryablePresentationGenerationError,
} from "../../services/api/api-error-handler";
import { useTranslations } from "next-intl";
import {
  clearDurableGenerationTask,
  createDurableGenerationTask,
  DurableGenerationTask,
  findDurableGenerationTask,
  saveDurableGenerationTask,
  updateDurableGenerationTask,
} from "./durableGenerationTask";
import { generationMasterPin } from "@/lib/authored-vnext/master/selection-storage";
import { DEFAULT_AUTHORED_STYLE } from "../../services/api/authored";
import { SOURCE_CONTENT_POLICY } from "../../services/api/source-content/contract";
import { documentCanvasForOrientation } from "@/lib/document-canvas";

const DEFAULT_LOADING_STATE: LoadingState = {
  message: "",
  isLoading: false,
  showProgress: false,
  duration: 0,
};

// New presentations have one engine.  It routes through the durable authored
// endpoint instead of choosing among layout, custom, or Template V2 paths.
const AUTHORED_TIMEOUT_MS = 30 * 60 * 1000;

export const usePresentationGeneration = (
  presentationId: string | null,
  outlines: { content: string }[] | null,
  onMasterRequired?: () => void,
) => {
  const tA2b = useA2bTranslations("outlineExtras");
  const t = useTranslations("generation");
  const dispatch = useDispatch();
  const router = useRouter();
  const templateRestoreState = useSavedSvgTemplate(presentationId);
  const authoredVisionQa = useSelector(
    (s: RootState) => s.pptGenUpload.authoredVisionQa
  );
  const authoredStyle = useSelector(
    (s: RootState) => s.pptGenUpload.authoredStyle
  );
  const generationStrategy = useSelector(
    (s: RootState) => s.pptGenUpload.generationStrategy,
  );
  const companyStandard = useSelector(
    (s: RootState) => s.pptGenUpload.companyStandard
  );
  // The deck language chosen in the upload flow; threaded into the authored request
  // so the backend picks language-aware fonts (e.g. Noto Sans KR) instead of falling
  // back to its non-Korean default.
  const generationConfig = useSelector(
    (s: RootState) => s.pptGenUpload.config
  );
  const sourceContentMode = useSelector(
    (s: RootState) => s.pptGenUpload.sourceContentMode,
  );
  const sourceContentAdmission = useSelector(
    (s: RootState) => s.pptGenUpload.sourceContentAdmission,
  );
  const language = generationConfig?.language ?? null;
  const [loadingState, setLoadingState] = useState<LoadingState>(
    DEFAULT_LOADING_STATE
  );
  const [startRecovery, setStartRecovery] = useState<"receipt" | "retry" | null>(null);
  const generationRunRef = useRef(0);
  const ownerInvalidatedRef = useRef(false);
  const retrySubmitRef = useRef<(() => void) | null>(null);
  const activeDurableTaskRef = useRef<string | null>(null);
  // Keep the pre-response task identity in memory as well as localStorage. Two
  // retries can be clicked in adjacent turns before React/storage reconciliation
  // settles; both POSTs must still carry the exact same idempotency key.
  const startingDurableTaskRef = useRef<DurableGenerationTask | null>(null);
  const submittingDurableTaskRef = useRef<string | null>(null);

  useEffect(() => {
    const invalidate = () => { ownerInvalidatedRef.current = true; generationRunRef.current += 1; startingDurableTaskRef.current = null; submittingDurableTaskRef.current = null; };
    window.addEventListener(AUTH_SESSION_EXPIRED_EVENT, invalidate); window.addEventListener("presenton:auth-logout", invalidate);
    return () => { window.removeEventListener(AUTH_SESSION_EXPIRED_EVENT, invalidate); window.removeEventListener("presenton:auth-logout", invalidate); };
  }, []);
  useEffect(
    () => () => {
      generationRunRef.current += 1;
      activeDurableTaskRef.current = null;
    },
    []
  );

  const validateInputs = useCallback(() => {
    if (!outlines || outlines.length === 0) {
      notify.warning(
        t("outlineNotReadyTitle"),
        t("outlineNotReadyDescription")
      );
      return false;
    }

    return true;
  }, [outlines, t]);

  const clearTheme = useCallback(() => {
    const element = document.getElementById("presentation-page");
    if (!element) return;
    element.style.removeProperty("--primary-color");
    element.style.removeProperty("--background-color");
    element.style.removeProperty("--card-color");
    element.style.removeProperty("--stroke");
    element.style.removeProperty("--primary-text");
    element.style.removeProperty("--background-text");
    element.style.removeProperty("--graph-0");
    element.style.removeProperty("--graph-1");
    element.style.removeProperty("--graph-2");
    element.style.removeProperty("--graph-3");
    element.style.removeProperty("--graph-4");
    element.style.removeProperty("--graph-5");
    element.style.removeProperty("--graph-6");
    element.style.removeProperty("--graph-7");
    element.style.removeProperty("--graph-8");
    element.style.removeProperty("--graph-9");
  }, []);

  const openWorkspace = useCallback(
    (idempotencyKey?: string) => {
      if (idempotencyKey) clearDurableGenerationTask(idempotencyKey);
      startingDurableTaskRef.current = null;
      submittingDurableTaskRef.current = null;
      activeDurableTaskRef.current = null;
      setLoadingState(DEFAULT_LOADING_STATE);
      dispatch(clearPresentationData());
      clearTheme();
      router.replace(`/presentation?id=${presentationId}`);
    },
    [clearTheme, dispatch, presentationId, router]
  );

  const openRecoverableWorkspace = useCallback(
    (error: unknown, idempotencyKey?: string): boolean => {
      if (!hasRecoverableChatFirstWorkspace(error)) {
        return false;
      }
      // The bootstrap endpoint has already durably attached the conversation,
      // first user message, settings, assets, and operation to this staging
      // presentation. Admission errors do not currently echo those IDs, so the
      // presentation ID is the stable recovery key for opening the timeline.
      openWorkspace(idempotencyKey);
      return true;
    },
    [openWorkspace]
  );

  const reportDurableGenerationFailure = useCallback(
    (error: unknown, task: DurableGenerationTask) => {
      console.warn(
        task.mode === "authored"
          ? "Authored presentation generation did not complete."
          : "Structured-template presentation generation did not complete.",
        error
      );
      if (isPresentationGenerationSlideLimitError(error)) {
        notify.error(
          t("slideLimitTitle"),
          t("slideLimitDescription")
        );
        return;
      }
      if (isRetryableGenerationStartError(error)) {
        // Only authored jobs resume from server-side checkpoints on a same-key
        // re-POST, so only they can truthfully promise saved progress.
        const progress =
          task.mode === "authored" ? task.lastProgress : undefined;
        const description =
          progress &&
          Number.isFinite(progress.completed_slides) &&
          Number.isFinite(progress.total_slides) &&
          progress.total_slides > 0
            ? t("resumeProgress", {
                completed: progress.completed_slides,
                total: progress.total_slides,
              })
            : t("interruptedDescription");
        notify.error(
          t("interruptedTitle"),
          description,
          {
            duration: 12_000,
            action: {
              label: t("retry"),
              onClick: () => retrySubmitRef.current?.(),
            },
          }
        );
      } else {
        notify.error(
          t("errorTitle"),
          getApiErrorDisplayMessage(error, t("errorDescription")),
        );
      }
    },
    [t]
  );

  // The workspace timeline reports generation progress, so a reload never
  // resumes a client-side poller; only stale overlay state needs clearing.
  useEffect(() => {
    setLoadingState(DEFAULT_LOADING_STATE);
  }, [presentationId]);

  const handleSubmit = useCallback(async () => {
    if (submittingDurableTaskRef.current || ownerInvalidatedRef.current) return;
    if (templateRestoreState !== "ready") {
      notify.warning("템플릿 설정 확인이 필요합니다", templateRestoreState === "loading" ? "저장된 템플릿 설정을 확인하고 있습니다." : "저장된 템플릿 설정을 불러오지 못했습니다. 페이지를 새로고침해 주세요."); return;
    }
    if (!presentationId) {
      notify.error(
        t("presentationUnavailableTitle"),
        t("presentationUnavailableDescription")
      );
      return;
    }
    let durableTask =
      startingDurableTaskRef.current?.sourcePresentationId === presentationId
        ? startingDurableTaskRef.current
        : findDurableGenerationTask(presentationId);
    const isSourceContentGeneration =
      durableTask?.authoredRequest?.sourceMode === "source_content_restyle" ||
      durableTask?.authoredRequest?.sourceMode === "raster_reconstruction" ||
      (sourceContentMode && Boolean(sourceContentAdmission));
    if (!isSourceContentGeneration && !validateInputs()) return;
    const runId = ++generationRunRef.current;
    const isActiveRun = () => generationRunRef.current === runId;
    const generationPreferences = {
      language: language && language !== LanguageType.Auto ? language : null,
      ...(generationConfig
        ? {
            tone: generationConfig.tone,
            verbosity: generationConfig.verbosity,
            instructions: generationConfig.instructions || null,
            include_table_of_contents:
              generationConfig.includeTableOfContents,
            include_title_slide: generationConfig.includeTitleSlide,
            web_search: generationConfig.webSearch,
          }
        : {}),
    };
    // The complete generation settings are persisted in the first workspace
    // message, so the admission request carries all of them.
    const sourceContentInstructions = [generationConfig?.prompt, generationConfig?.instructions]
      .map((value) => value?.trim())
      .filter((value): value is string => Boolean(value))
      .join("\n");
    const asyncGenerationPreferences = isSourceContentGeneration
      ? {
          language: null,
          instructions: sourceContentInstructions || null,
          include_table_of_contents: false,
          include_title_slide: false,
          web_search: false,
        }
      : generationPreferences;

    // Persist the idempotency key before POSTing so a lost response can be retried
    // without creating a second authored deck.
    const slides_markdown = (outlines || [])
      .map((o) => o.content)
      .filter((c) => c && c.trim().length > 0);
    if (!isSourceContentGeneration && slides_markdown.length === 0) {
      notify.warning(t("outlineEmptyTitle"), t("outlineEmptyDescription"));
      return;
    }
    const base = Math.max(120, slides_markdown.length * 25);
    const estSeconds = authoredVisionQa
      ? Math.min(AUTHORED_TIMEOUT_MS / 1000, base * 2)
      : Math.min(600, base);
    // "Auto" is a sentinel, not a real language: send null so the backend applies
    // its language-aware fallback instead of authoring with a literal "Auto".
    const reduxAuthoredRequest = {
      strategy: generationStrategy,
      language: isSourceContentGeneration
        ? null
        : language && language !== LanguageType.Auto
          ? language
          : null,
      ...(isSourceContentGeneration
        ? { instructions: sourceContentInstructions || null }
        : {}),
      // The immutable source-content path must never enter vision re-authoring,
      // including outline reload/resume paths reconstructed from Redux.
      visionQa: isSourceContentGeneration || generationStrategy === "svg" ? false : authoredVisionQa,
      ...(generationStrategy === "svg" ? {
        commonHeaderMode: generationConfig?.commonHeaderMode ?? "uniform",
        commonHeaderDesign: commonHeaderDesignForConfig(generationConfig ?? {}),
        commonHeaderCoverException: (generationConfig?.commonHeaderMode ?? "uniform") === "uniform" && (generationConfig?.commonHeaderCoverException ?? false),
      } : {}),
      ...(generationStrategy === "svg" && generationConfig?.svgDesignReferenceSnapshotId ? { svgDesignReferenceSnapshotId: generationConfig.svgDesignReferenceSnapshotId } : {}),
      ...(generationStrategy === "svg" && generationConfig?.svgTemplatePin ? { svgTemplatePin: generationConfig.svgTemplatePin, svgTemplateMode: generationConfig.svgTemplateMode, svgTemplateFrameMap: generationConfig.svgTemplateFrameMap } : {}),
      authoredStyle:
        generationStrategy === "authored" && generationConfig?.pageSize !== "A4" && generationConfig?.aspectRatio === "9:16"
          ? DEFAULT_AUTHORED_STYLE.id
          : authoredStyle,
      ...(isSourceContentGeneration && generationStrategy === "svg" && generationConfig?.pageSize === "A4" && generationConfig.orientation
        ? { documentCanvas: documentCanvasForOrientation(generationConfig.orientation) }
        : (generationStrategy !== "svg" || generationConfig?.sourceCanvas) && sourceContentAdmission?.documentCanvas
          ? { documentCanvas: sourceContentAdmission.documentCanvas }
        : {}),
      ...(generationConfig?.sourceCanvas ? { sourceCanvas: true }
        : generationConfig?.svgCanvas ? { svgCanvas: generationConfig.svgCanvas }
        : generationConfig?.pageSize === "A4" && generationConfig.orientation
          ? { pageSize: "A4" as const, orientation: generationConfig.orientation }
          : { aspectRatio: generationConfig?.aspectRatio ?? "16:9" }),
      ...(generationConfig?.codexGenerationPreset
        ? { codexGenerationPreset: generationConfig.codexGenerationPreset }
        : {}),
      ...(companyStandard && !isSourceContentGeneration && generationStrategy === "authored"
        ? { masterPin: generationMasterPin(companyStandard) }
        : {}),
      ...(sourceContentMode && sourceContentAdmission
        ? {
            sourceMode: "source_content_restyle" as const,
            sourceContentManifestPin: sourceContentAdmission.manifestPin,
            contentPolicy: SOURCE_CONTENT_POLICY,
          }
        : {}),
    };
    if (durableTask?.taskId && durableTask.status !== "retryable-error") {
      openWorkspace(durableTask.idempotencyKey);
      return;
    }
    // A "starting" record can survive a lost POST response. Reusing it here
    // resubmits with the same idempotency key instead of creating a second job.
    if (!durableTask || (durableTask.mode !== "authored" && durableTask.mode !== "svg")) {
      durableTask = saveDurableGenerationTask({
        ...createDurableGenerationTask({
          mode: generationStrategy,
          sourcePresentationId: presentationId,
          timeoutMs: AUTHORED_TIMEOUT_MS,
          progressDurationSeconds: estSeconds,
        }),
        authoredRequest: reduxAuthoredRequest,
      });
    }
    // A resume must resend the captured settings verbatim: a reload wipes the
    // Redux copies, and the server 409s a same-key re-POST whose payload drifts.
    const authoredRequest = durableTask.authoredRequest ?? reduxAuthoredRequest;
    const requestSvgCanvas = "svgCanvas" in authoredRequest ? authoredRequest.svgCanvas : undefined;
    const requestSourceCanvas = "sourceCanvas" in authoredRequest && authoredRequest.sourceCanvas;
    const requestPageSize = "pageSize" in authoredRequest ? authoredRequest.pageSize : undefined;
    const requestOrientation = "orientation" in authoredRequest ? authoredRequest.orientation : undefined;
    const requestAspectRatio = "aspectRatio" in authoredRequest ? authoredRequest.aspectRatio : undefined;
    startingDurableTaskRef.current = durableTask;
    submittingDurableTaskRef.current = durableTask.idempotencyKey;
    setStartRecovery(null);
    setLoadingState({ isLoading: true, message: "슬라이드 제작 요청을 접수하고 있습니다.", showProgress: false, duration: 0 });
    try {
      const started = await PresentationGenerationApi.generateAuthoredAsync({
        presentation_id: presentationId,
        // Source-content generation obtains every display payload from its
        // immutable manifest.  Never leak an outline surrogate into this path.
        content: isSourceContentGeneration ? "" : slides_markdown[0].slice(0, 200),
        slides_markdown: isSourceContentGeneration ? undefined : slides_markdown,
        ...asyncGenerationPreferences,
        ...(isSourceContentGeneration
          ? { instructions: authoredRequest.instructions ?? null }
          : {}),
        language: authoredRequest.language,
        vision_qa: authoredRequest.visionQa,
        strategy: authoredRequest.strategy ?? "svg",
        common_header_mode: "commonHeaderMode" in authoredRequest ? authoredRequest.commonHeaderMode : undefined,
        common_header_design: "commonHeaderDesign" in authoredRequest ? authoredRequest.commonHeaderDesign : undefined,
        common_header_cover_exception: "commonHeaderCoverException" in authoredRequest ? authoredRequest.commonHeaderCoverException : undefined,
        ...("svgTemplatePin" in authoredRequest && authoredRequest.svgTemplatePin ? {} : { authored_style: authoredRequest.authoredStyle }),
        svg_design_reference_snapshot_id: "svgDesignReferenceSnapshotId" in authoredRequest ? authoredRequest.svgDesignReferenceSnapshotId : undefined,
        svgTemplatePin: "svgTemplatePin" in authoredRequest ? authoredRequest.svgTemplatePin : undefined,
        svgTemplateMode: "svgTemplateMode" in authoredRequest ? authoredRequest.svgTemplateMode : undefined,
        svgTemplateFrameMap: "svgTemplateFrameMap" in authoredRequest ? authoredRequest.svgTemplateFrameMap : undefined,
        ...(requestSourceCanvas ? {}
          : requestSvgCanvas ? { svg_canvas: requestSvgCanvas }
          : requestPageSize === "A4" && requestOrientation
            ? { page_size: "A4" as const, orientation: requestOrientation }
            : requestAspectRatio ? { aspect_ratio: requestAspectRatio }
            : authoredRequest.sourceMode === "source_content_restyle" && authoredRequest.strategy === "svg" ? {}
            : { aspect_ratio: "16:9" }),
        codex_generation_preset: authoredRequest.codexGenerationPreset,
        masterPin: authoredRequest.masterPin,
        sourceMode: authoredRequest.sourceMode,
        rasterApprovalPin: "rasterApprovalPin" in authoredRequest ? authoredRequest.rasterApprovalPin : undefined,
        referenceSnapshotIds: authoredRequest.sourceMode === "raster_reconstruction"
          ? authoredRequest.referenceSnapshotIds : undefined,
        sourceContentManifestPin: authoredRequest.sourceContentManifestPin,
        contentPolicy: authoredRequest.contentPolicy,
        idempotency_key: durableTask.idempotencyKey,
      });
      const taskId = started?.id;
      if (!taskId) throw new Error("생성 작업을 시작하지 못했습니다.");
      durableTask = updateDurableGenerationTask(durableTask, {
        taskId,
        status: started?.status === "running" ? "running" : "pending",
        requestId: started?.request_id,
        lastFailure: undefined,
      });
      submittingDurableTaskRef.current = null;
      startingDurableTaskRef.current = null;
      if (!isActiveRun()) return;
      openWorkspace(durableTask.idempotencyKey);
    } catch (error: unknown) {
      submittingDurableTaskRef.current = null;
      if (!isActiveRun()) return;
      setLoadingState({ ...DEFAULT_LOADING_STATE, message: error instanceof Error ? error.message : "제작 요청을 접수하지 못했습니다. 다시 확인해 주세요." });
      setStartRecovery(isAmbiguousGenerationTransportError(error) ? "receipt" : isRetryableGenerationStartError(error) ? "retry" : null);
      if (isCompanyStandardAdmissionError(error)) {
        clearDurableGenerationTask(durableTask.idempotencyKey);
        startingDurableTaskRef.current = null;
        notify.warning(
          tA2b("pleaseCheckThePptMasterAgain"),
          tA2b("theMasterChangedOrIsNotPublished")
        );
        onMasterRequired?.();
        return;
      }
      if (openRecoverableWorkspace(error, durableTask.idempotencyKey)) {
        return;
      }
      if (!isRetryableGenerationStartError(error)) {
        clearDurableGenerationTask(durableTask.idempotencyKey);
        startingDurableTaskRef.current = null;
      }
      reportDurableGenerationFailure(error, durableTask);
    }
  }, [
    validateInputs,
    templateRestoreState,
    presentationId,
    outlines,
    authoredVisionQa,
    language,
    generationConfig,
    sourceContentMode,
    sourceContentAdmission,
    authoredStyle,
    generationStrategy,
    companyStandard,
    onMasterRequired,
    openWorkspace,
    openRecoverableWorkspace,
    reportDurableGenerationFailure,
    t,
  ]);

  useEffect(() => {
    retrySubmitRef.current = () => {
      void handleSubmit();
    };
    return () => {
      retrySubmitRef.current = null;
    };
  }, [handleSubmit]);

  // Invalidate the active run so every in-flight loop bails at its next
  // isActiveRun() check, and ask the backend to stop the durable job. The
  // server-side stop is best-effort: an attempt already in its final commit
  // can still publish the deck.
  const cancelGeneration = useCallback(() => {
    generationRunRef.current += 1;
    activeDurableTaskRef.current = null;
    startingDurableTaskRef.current = null;
    submittingDurableTaskRef.current = null;
    const durableTask = findDurableGenerationTask(presentationId);
    if (durableTask) {
      if (durableTask.taskId) {
        void PresentationGenerationApi.cancelGeneration(
          durableTask.taskId
        ).catch((error) => {
          console.warn("Failed to cancel generation on the server", error);
        });
      }
      clearDurableGenerationTask(durableTask.idempotencyKey);
    }
    setLoadingState(DEFAULT_LOADING_STATE);
    notify.info(
      "생성을 취소했습니다",
      "서버에 작업 중지를 요청했습니다. 이미 마무리 단계인 작업은 완료될 수 있습니다."
    );
  }, [presentationId]);

  return { loadingState, handleSubmit, cancelGeneration, startRecovery };
};
