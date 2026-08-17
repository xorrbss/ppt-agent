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

const DEFAULT_LOADING_STATE: LoadingState = {
  message: "",
  isLoading: false,
  showProgress: false,
  duration: 0,
};

// New presentations have one engine.  It routes through the durable authored
// endpoint instead of choosing among layout, custom, or Template V2 paths.
const AUTHORED_TIMEOUT_MS = 30 * 60 * 1000;

const isAmbiguousGenerationTransportError = (error: unknown): boolean => {
  if (error instanceof TypeError) return true;
  if (
    typeof DOMException !== "undefined" &&
    error instanceof DOMException &&
    (error.name === "AbortError" || error.name === "NetworkError")
  ) {
    return true;
  }
  if (error instanceof ApiError) return error.status === 0;
  return Boolean(
    error &&
      typeof error === "object" &&
      (error as { status?: unknown }).status === 0
  );
};

const isRetryableGenerationStartError = (error: unknown): boolean =>
  isAmbiguousGenerationTransportError(error) ||
  isRetryablePresentationGenerationError(error);

const hasRecoverableChatFirstWorkspace = (error: unknown): boolean =>
  error instanceof ApiError && error.workspaceProjected;

const isCompanyStandardAdmissionError = (error: unknown): boolean => {
  if (!(error instanceof ApiError)) return false;
  const value = `${error.code ?? ""} ${error.message}`.toLowerCase();
  return [
    "authored_vnext_stale_master",
    "authored_vnext_published_master_required",
    "authored_vnext_generation_not_found",
    "authored_vnext_master_disabled",
  ].some((code) => value.includes(code));
};

export const usePresentationGeneration = (
  presentationId: string | null,
  outlines: { content: string }[] | null,
  onMasterRequired?: () => void,
) => {
  const t = useTranslations("generation");
  const dispatch = useDispatch();
  const router = useRouter();
  const authoredVisionQa = useSelector(
    (s: RootState) => s.pptGenUpload.authoredVisionQa
  );
  const authoredStyle = useSelector(
    (s: RootState) => s.pptGenUpload.authoredStyle
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
  const language = generationConfig?.language ?? null;
  const [loadingState, setLoadingState] = useState<LoadingState>(
    DEFAULT_LOADING_STATE
  );
  const generationRunRef = useRef(0);
  const retrySubmitRef = useRef<(() => void) | null>(null);
  const activeDurableTaskRef = useRef<string | null>(null);
  // Keep the pre-response task identity in memory as well as localStorage. Two
  // retries can be clicked in adjacent turns before React/storage reconciliation
  // settles; both POSTs must still carry the exact same idempotency key.
  const startingDurableTaskRef = useRef<DurableGenerationTask | null>(null);
  const submittingDurableTaskRef = useRef<string | null>(null);

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
          t("errorDescription")
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
    if (!validateInputs()) return;
    if (!presentationId) {
      notify.error(
        t("presentationUnavailableTitle"),
        t("presentationUnavailableDescription")
      );
      return;
    }
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
    const asyncGenerationPreferences = generationPreferences;

    // Persist the idempotency key before POSTing so a lost response can be retried
    // without creating a second authored deck.
    const slides_markdown = (outlines || [])
      .map((o) => o.content)
      .filter((c) => c && c.trim().length > 0);
    if (slides_markdown.length === 0) {
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
      language: language && language !== LanguageType.Auto ? language : null,
      visionQa: authoredVisionQa,
      authoredStyle,
      ...(companyStandard
        ? { masterPin: generationMasterPin(companyStandard) }
        : {}),
    };
    let durableTask =
      startingDurableTaskRef.current?.sourcePresentationId === presentationId
        ? startingDurableTaskRef.current
        : findDurableGenerationTask(presentationId);
    if (durableTask?.taskId && durableTask.status !== "retryable-error") {
      openWorkspace(durableTask.idempotencyKey);
      return;
    }
    // A "starting" record can survive a lost POST response. Reusing it here
    // resubmits with the same idempotency key instead of creating a second job.
    if (!durableTask || durableTask.mode !== "authored") {
      durableTask = saveDurableGenerationTask({
        ...createDurableGenerationTask({
          mode: "authored",
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
    startingDurableTaskRef.current = durableTask;
    submittingDurableTaskRef.current = durableTask.idempotencyKey;
    try {
      const started = await PresentationGenerationApi.generateAuthoredAsync({
        presentation_id: presentationId,
        content: slides_markdown[0].slice(0, 200),
        slides_markdown,
        ...asyncGenerationPreferences,
        language: authoredRequest.language,
        vision_qa: authoredRequest.visionQa,
        authored_style: authoredRequest.authoredStyle,
        masterPin: authoredRequest.masterPin,
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
      if (isCompanyStandardAdmissionError(error)) {
        clearDurableGenerationTask(durableTask.idempotencyKey);
        startingDurableTaskRef.current = null;
        notify.warning(
          "PPT 마스터를 다시 확인해 주세요",
          "마스터가 변경되었거나 게시 상태가 아닙니다. 최신 마스터를 적용한 뒤 다시 생성하세요."
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
    presentationId,
    outlines,
    authoredVisionQa,
    language,
    generationConfig,
    authoredStyle,
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

  return { loadingState, handleSubmit, cancelGeneration };
};
