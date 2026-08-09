import { useState, useCallback, useEffect, useRef } from "react";
import { useDispatch, useSelector } from "react-redux";
import { RootState } from "@/store/store";
import { usePathname, useRouter } from "next/navigation";
import { notify } from "@/components/ui/sonner";
import { clearPresentationData } from "@/store/slices/presentationGeneration";
import { PresentationGenerationApi } from "../../services/api/presentation-generation";
import { LoadingState, TABS } from "../types/index";
import { TemplateLayoutsWithSettings } from "@/app/presentation-templates/utils";
import { MixpanelEvent, trackEvent } from "@/utils/mixpanel";
import { AUTHORED_TEMPLATE_ID } from "../components/TemplateSelection";
import { LanguageType } from "../../upload/type";
import {
  parseTemplateV2SelectionId,
  TEMPLATE_V2_SELECTION_PREFIX,
} from "@/app/hooks/useStructuredTemplates";
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

const DEFAULT_LOADING_STATE: LoadingState = {
  message: "",
  isLoading: false,
  showProgress: false,
  duration: 0,
};

// The authored (high-quality) mode is not a real layout template, so it routes
// through the async generate endpoint + polling rather than the layout
// prepare/stream path. Sentinel id shared with the selection card
// (AUTHORED_TEMPLATE_ID) so the card and this branch can never disagree.
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

export const normalizeCustomTemplateId = (templateId: string): string =>
  templateId.startsWith("custom-") ? templateId : `custom-${templateId}`;

export const usePresentationGeneration = (
  presentationId: string | null,
  outlines: { content: string }[] | null,
  selectedTemplate: TemplateLayoutsWithSettings | string | null,
  setActiveTab: (tab: string) => void
) => {
  const t = useTranslations("generation");
  const dispatch = useDispatch();
  const router = useRouter();
  const pathname = usePathname();
  const authoredVisionQa = useSelector(
    (s: RootState) => s.pptGenUpload.authoredVisionQa
  );
  const authoredStyle = useSelector(
    (s: RootState) => s.pptGenUpload.authoredStyle
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
  const standardIdempotencyKeyRef = useRef<string | null>(null);

  useEffect(
    () => () => {
      generationRunRef.current += 1;
      activeDurableTaskRef.current = null;
      standardIdempotencyKeyRef.current = null;
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

    if (!selectedTemplate) {
      notify.warning(
        t("layoutNotSelectedTitle"),
        t("layoutNotSelectedDescription")
      );
      return false;
    }

    return true;
  }, [outlines, selectedTemplate, t]);

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
      standardIdempotencyKeyRef.current = null;
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
    if (!selectedTemplate) {
      setActiveTab(TABS.LAYOUTS);
      return;
    }
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
    if (selectedTemplate === AUTHORED_TEMPLATE_ID) {
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
        if (openRecoverableWorkspace(error, durableTask.idempotencyKey)) {
          return;
        }
        if (!isRetryableGenerationStartError(error)) {
          clearDurableGenerationTask(durableTask.idempotencyKey);
          startingDurableTaskRef.current = null;
        }
        reportDurableGenerationFailure(error, durableTask);
      }
      return;
    }

    const templateV2Selection =
      typeof selectedTemplate === "string"
        ? parseTemplateV2SelectionId(selectedTemplate)
        : null;
    if (
      typeof selectedTemplate === "string" &&
      selectedTemplate.startsWith(TEMPLATE_V2_SELECTION_PREFIX) &&
      !templateV2Selection
    ) {
      notify.error(
        t("templateErrorTitle"),
        t("templateErrorDescription")
      );
      return;
    }
    if (templateV2Selection) {
      const slides_markdown = (outlines || [])
        .map((outline) => outline.content)
        .filter((content) => content && content.trim().length > 0);
      if (slides_markdown.length === 0) {
        notify.warning(t("outlineEmptyTitle"), t("outlineEmptyDescription"));
        return;
      }

      const progressDurationSeconds = Math.max(
        30,
        slides_markdown.length * 12
      );
      let durableTask =
        startingDurableTaskRef.current?.sourcePresentationId === presentationId
          ? startingDurableTaskRef.current
          : findDurableGenerationTask(presentationId);
      if (durableTask?.taskId) {
        openWorkspace(durableTask.idempotencyKey);
        return;
      }
      // See the authored branch: a retry of a response-lost POST must keep the
      // original key so the backend can return the existing task.
      if (!durableTask || durableTask.mode !== "template-v2") {
        durableTask = saveDurableGenerationTask(
          createDurableGenerationTask({
            mode: "template-v2",
            sourcePresentationId: presentationId,
            timeoutMs: AUTHORED_TIMEOUT_MS,
            progressDurationSeconds,
          })
        );
      }
      startingDurableTaskRef.current = durableTask;
      submittingDurableTaskRef.current = durableTask.idempotencyKey;

      try {
        const started =
          await PresentationGenerationApi.generateTemplateV2Async({
            presentation_id: presentationId,
            content: slides_markdown[0].slice(0, 200),
            slides_markdown,
            ...asyncGenerationPreferences,
            template_v2_id: templateV2Selection.templateId,
            template_v2_revision: templateV2Selection.revision,
            idempotency_key: durableTask.idempotencyKey,
          });
        const taskId = started?.id;
        if (!taskId) {
          throw new Error("구조화 템플릿 생성 작업을 시작하지 못했습니다.");
        }
        durableTask = updateDurableGenerationTask(durableTask, {
          taskId,
          status: started?.status === "running" ? "running" : "pending",
          requestId: started?.request_id,
        });
        submittingDurableTaskRef.current = null;
        startingDurableTaskRef.current = null;
        if (!isActiveRun()) return;
        openWorkspace(durableTask.idempotencyKey);
      } catch (error: unknown) {
        submittingDurableTaskRef.current = null;
        if (!isActiveRun()) return;
        if (openRecoverableWorkspace(error, durableTask.idempotencyKey)) {
          return;
        }
        if (!isRetryableGenerationStartError(error)) {
          clearDurableGenerationTask(durableTask.idempotencyKey);
          startingDurableTaskRef.current = null;
        }
        reportDurableGenerationFailure(error, durableTask);
      }
      return;
    }

    const selectedTemplateId =
      typeof selectedTemplate === "string"
        ? selectedTemplate
        : selectedTemplate?.id || null;
    const selectedTemplateType =
      typeof selectedTemplate === "string" ? "custom" : "built_in";
    const selectedTemplateName =
      typeof selectedTemplate === "string"
        ? null
        : selectedTemplate?.name || null;
    const selectedTemplateLayoutCount =
      typeof selectedTemplate === "string"
        ? null
        : selectedTemplate?.layouts?.length || 0;

    trackEvent(MixpanelEvent.Outline_Presentation_Generation_Started, {
      pathname,
      presentation_id: presentationId,
      outline_count: outlines?.length || 0,
      template_id: selectedTemplateId,
      template_type: selectedTemplateType,
      template_name: selectedTemplateName,
      template_layout_count: selectedTemplateLayoutCount,
    });

    const slides_markdown = (outlines || [])
      .map((outline) => outline.content)
      .filter((content) => content && content.trim().length > 0);
    if (slides_markdown.length === 0) {
      notify.warning(
        t("outlineEmptyTitle"),
        t("outlineEmptyDescription")
      );
      return;
    }
    const template =
      typeof selectedTemplate === "string"
        ? normalizeCustomTemplateId(selectedTemplate)
        : selectedTemplate.id;
    if (!standardIdempotencyKeyRef.current) {
      const randomId =
        typeof crypto !== "undefined" && "randomUUID" in crypto
          ? crypto.randomUUID()
          : `${Date.now().toString(36)}-${Math.random()
              .toString(36)
              .slice(2)}`;
      standardIdempotencyKeyRef.current = `ppt-generation-${randomId}`;
    }
    const idempotencyKey = standardIdempotencyKeyRef.current;

    try {
      const started = await PresentationGenerationApi.generateStandardAsync({
        presentation_id: presentationId,
        content: slides_markdown[0].slice(0, 200),
        slides_markdown,
        ...generationPreferences,
        template,
        idempotency_key: idempotencyKey,
      });
      if (!started?.id) {
        throw new Error("Presentation generation was not accepted.");
      }
      if (!isActiveRun()) return;
      openWorkspace();
    } catch (error: unknown) {
      if (!isActiveRun()) return;
      console.warn("Presentation generation was not accepted.", error);
      if (openRecoverableWorkspace(error)) return;
      if (isRetryableGenerationStartError(error)) {
        notify.error(
          t("interruptedTitle"),
          t("interruptedDescription"),
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
    }
  }, [
    selectedTemplate,
    validateInputs,
    pathname,
    presentationId,
    outlines,
    setActiveTab,
    authoredVisionQa,
    language,
    generationConfig,
    authoredStyle,
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
    standardIdempotencyKeyRef.current = null;
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
