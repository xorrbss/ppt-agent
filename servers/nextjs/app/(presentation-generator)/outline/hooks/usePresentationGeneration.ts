import { useState, useCallback, useEffect, useRef } from "react";
import { flushSync } from "react-dom";
import { useDispatch, useSelector } from "react-redux";
import { RootState } from "@/store/store";
import { usePathname, useRouter } from "next/navigation";
import { notify } from "@/components/ui/sonner";
import { clearPresentationData } from "@/store/slices/presentationGeneration";
import { PresentationGenerationApi } from "../../services/api/presentation-generation";
import type {
  GenerationFailure,
  GenerationStatusResponse,
} from "../../services/api/presentation-generation";
import { DashboardApi } from "../../services/api/dashboard";
import { LoadingState, TABS } from "../types/index";
import { TemplateLayoutsWithSettings } from "@/app/presentation-templates/utils";
import { getCustomTemplateDetails } from "@/app/hooks/useCustomTemplates";
import { MixpanelEvent, trackEvent } from "@/utils/mixpanel";
import { AUTHORED_TEMPLATE_ID } from "../components/TemplateSelection";
import { LanguageType } from "../../upload/type";
import {
  parseTemplateV2SelectionId,
  TEMPLATE_V2_SELECTION_PREFIX,
} from "@/app/hooks/useStructuredTemplates";
import {
  apiErrorFromPayload,
  getApiErrorDisplayMessage,
  getRetryablePresentationGenerationMessage,
  isRetryablePresentationGenerationError,
} from "../../services/api/api-error-handler";
import {
  clearDurableGenerationTask,
  createDurableGenerationTask,
  DurableGenerationTask,
  findDurableGenerationTask,
  saveDurableGenerationTask,
  subscribeToDurableGenerationTasks,
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
const AUTHORED_POLL_MS = 4000;
const AUTHORED_TIMEOUT_MS = 30 * 60 * 1000;
// Tolerate a few consecutive transient status-poll failures (a 502/network blip during
// the minutes-long poll) before aborting — the backend task keeps running regardless.
const AUTHORED_MAX_POLL_ERRORS = 3;
const PREPARE_RECOVERY_ATTEMPTS = 4;
const PREPARE_RECOVERY_POLL_MS = 1000;

const hasPreparedStructure = (presentation: any): boolean =>
  Array.isArray(presentation?.structure?.slides) &&
  presentation.structure.slides.length > 0;

export const usePresentationGeneration = (
  presentationId: string | null,
  outlines: { content: string }[] | null,
  selectedTemplate: TemplateLayoutsWithSettings | string | null,
  setActiveTab: (tab: string) => void
) => {
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
  const language = useSelector(
    (s: RootState) => s.pptGenUpload.config?.language ?? null
  );
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
        "개요가 준비되지 않았습니다",
        "계속하기 전에 개요 생성이 완료될 때까지 기다려 주세요."
      );
      return false;
    }

    if (!selectedTemplate) {
      notify.warning(
        "레이아웃이 선택되지 않았습니다",
        "프레젠테이션을 생성하기 전에 레이아웃 그룹을 선택하세요."
      );
      return false;
    }

    return true;
  }, [outlines, selectedTemplate]);

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

  const getDurableLoadingState = useCallback(
    (task: DurableGenerationTask): LoadingState => ({
      message:
        task.mode === "authored"
          ? "AI가 슬라이드를 저작하는 중입니다. 완료되면 자동으로 이동합니다."
          : "구조화 템플릿으로 프레젠테이션을 생성하는 중입니다.",
      isLoading: task.status !== "background",
      showProgress: task.status !== "background",
      duration: task.progressDurationSeconds,
      progress: task.lastProgress,
      failure: task.lastFailure,
    }),
    []
  );

  const finishDurableGeneration = useCallback(
    async (
      durableTask: DurableGenerationTask,
      statusTask: GenerationStatusResponse,
      isActiveRun: () => boolean
    ) => {
      const newId = statusTask?.data?.presentation_id;
      if (!newId) {
        throw new Error("생성된 프레젠테이션을 찾을 수 없습니다.");
      }

      clearDurableGenerationTask(durableTask.idempotencyKey);
      startingDurableTaskRef.current = null;
      if (!isActiveRun()) return;
      // Completion is user-visible before best-effort cleanup. Waiting for the
      // outline-shell DELETE can otherwise strand a completed deck behind the
      // loading overlay when that request is slow or unavailable.
      flushSync(() => setLoadingState(DEFAULT_LOADING_STATE));
      dispatch(clearPresentationData());
      clearTheme();
      router.replace(
        statusTask?.data?.edit_path || `/presentation?id=${newId}`
      );

      if (
        durableTask.sourcePresentationId &&
        durableTask.sourcePresentationId !== newId
      ) {
        void DashboardApi.deletePresentation(
          durableTask.sourcePresentationId
        ).catch((error) => {
          console.warn("Failed to clean up outline shell presentation", error);
        });
      }
    },
    [clearTheme, dispatch, router]
  );

  const reportDurableGenerationFailure = useCallback(
    (error: any, mode: DurableGenerationTask["mode"]) => {
      console.warn(
        mode === "authored"
          ? "Authored presentation generation did not complete."
          : "Structured-template presentation generation did not complete.",
        error
      );
      if (isRetryablePresentationGenerationError(error)) {
        notify.error(
          "생성이 일시 중단되었습니다",
          getRetryablePresentationGenerationMessage(error),
          {
            duration: 12_000,
            action: {
              label: "다시 시도",
              onClick: () => retrySubmitRef.current?.(),
            },
          }
        );
      } else {
        notify.error(
          "생성 오류",
          getApiErrorDisplayMessage(
            error,
            "프레젠테이션 생성 중 오류가 발생했습니다."
          )
        );
      }
    },
    []
  );

  const runDurableGeneration = useCallback(
    async (
      initialTask: DurableGenerationTask,
      minimumLoadingMs = 0
    ) => {
      if (
        activeDurableTaskRef.current === initialTask.idempotencyKey &&
        initialTask.taskId
      ) {
        return;
      }

      const runId = ++generationRunRef.current;
      const isActiveRun = () => generationRunRef.current === runId;
      activeDurableTaskRef.current = initialTask.idempotencyKey;
      setLoadingState(getDurableLoadingState(initialTask));

      let durableTask = initialTask;
      let pollErrors = 0;
      let backgroundNoticeShown = initialTask.status === "background";
      const showLoadingUntil = Date.now() + minimumLoadingMs;

      try {
        if (!durableTask.taskId) {
          throw new Error("생성 작업 식별자가 없습니다.");
        }
        const taskId = durableTask.taskId;

        while (isActiveRun()) {
          let statusTask: GenerationStatusResponse;
          try {
            statusTask = await PresentationGenerationApi.getGenerationStatus(
              taskId
            );
            if (!isActiveRun()) return;
            pollErrors = 0;
          } catch (pollError) {
            pollErrors += 1;
            if (pollErrors >= AUTHORED_MAX_POLL_ERRORS) throw pollError;
            await new Promise((resolve) =>
              setTimeout(resolve, AUTHORED_POLL_MS)
            );
            continue;
          }

          if (statusTask?.status === "completed") {
            const remainingLoadingMs = showLoadingUntil - Date.now();
            if (remainingLoadingMs > 0) {
              // Avoid an imperceptible loading flash when a newly-created task
              // is completed from an idempotency replay/cache immediately.
              await new Promise((resolve) =>
                setTimeout(resolve, remainingLoadingMs)
              );
              if (!isActiveRun()) return;
            }
            await finishDurableGeneration(
              durableTask,
              statusTask,
              isActiveRun
            );
            return;
          }
          if (statusTask?.status === "error") {
            const failure = (statusTask.failure || statusTask.error) as
              | GenerationFailure
              | Record<string, unknown>
              | undefined;
            const generationError = apiErrorFromPayload(
              failure,
              statusTask?.message || "Presentation generation failed.",
              500,
              statusTask?.request_id
            );
            if (
              durableTask.mode === "authored" &&
              isRetryablePresentationGenerationError(generationError)
            ) {
              durableTask = updateDurableGenerationTask(durableTask, {
                status: "retryable-error",
                requestId: statusTask.request_id || durableTask.requestId,
                lastProgress: statusTask.progress || durableTask.lastProgress,
                lastFailure: failure as GenerationFailure | undefined,
              });
            } else {
              clearDurableGenerationTask(durableTask.idempotencyKey);
            }
            throw generationError;
          }

          const isPastForegroundDeadline = Date.now() >= durableTask.deadlineAt;
          durableTask = updateDurableGenerationTask(durableTask, {
            status: isPastForegroundDeadline
              ? "background"
              : statusTask?.status === "running"
                ? "running"
                : "pending",
            requestId: statusTask?.request_id || durableTask.requestId,
            lastProgress: statusTask?.progress || durableTask.lastProgress,
            lastFailure: undefined,
          });
          if (!isPastForegroundDeadline) {
            setLoadingState(getDurableLoadingState(durableTask));
          }

          // The 30-minute deadline limits the blocking overlay, not the server job.
          // Keep polling in the background so a late completion still opens the deck.
          if (isPastForegroundDeadline && !backgroundNoticeShown) {
            backgroundNoticeShown = true;
            setLoadingState(DEFAULT_LOADING_STATE);
            notify.info(
              "생성이 백그라운드에서 계속됩니다",
              "완료되면 생성된 프레젠테이션으로 자동 이동합니다."
            );
          }

          await new Promise((resolve) =>
            setTimeout(resolve, AUTHORED_POLL_MS)
          );
        }
      } catch (error: any) {
        if (!isActiveRun()) return;
        const isRetryable = isRetryablePresentationGenerationError(error);
        if (durableTask.mode === "authored" && isRetryable) {
          if (durableTask.status !== "retryable-error") {
            durableTask = updateDurableGenerationTask(durableTask, {
              status: "retryable-error",
            });
          }
        } else if (!isRetryable) {
          clearDurableGenerationTask(durableTask.idempotencyKey);
          startingDurableTaskRef.current = null;
        }
        reportDurableGenerationFailure(error, durableTask.mode);
      } finally {
        if (isActiveRun()) {
          activeDurableTaskRef.current = null;
          setLoadingState(DEFAULT_LOADING_STATE);
        }
      }
    },
    [
      finishDurableGeneration,
      getDurableLoadingState,
      reportDurableGenerationFailure,
    ]
  );

  useEffect(() => {
    const reconcile = () => {
      const durableTask = findDurableGenerationTask(presentationId);
      if (!durableTask?.taskId || durableTask.status === "retryable-error") {
        // Fast Refresh may preserve React state after invalidating its poller. If
        // there is no durable task to resume, never leave that stale overlay up.
        setLoadingState(DEFAULT_LOADING_STATE);
        return;
      }
      void runDurableGeneration(
        durableTask,
        submittingDurableTaskRef.current === durableTask.idempotencyKey
          ? 100
          : 0
      );
    };
    reconcile();
    return subscribeToDurableGenerationTasks(reconcile);
  }, [presentationId, runDurableGeneration]);

  const handleSubmit = useCallback(async () => {
    if (!selectedTemplate) {
      setActiveTab(TABS.LAYOUTS);
      return;
    }
    if (!validateInputs()) return;
    const runId = ++generationRunRef.current;
    const isActiveRun = () => generationRunRef.current === runId;

    // Persist the idempotency key before POSTing so a lost response can be retried
    // without creating a second authored deck.
    if (selectedTemplate === AUTHORED_TEMPLATE_ID) {
      const slides_markdown = (outlines || [])
        .map((o) => o.content)
        .filter((c) => c && c.trim().length > 0);
      if (slides_markdown.length === 0) {
        notify.warning("개요가 비어 있습니다", "먼저 개요를 생성하세요.");
        return;
      }
      const base = Math.max(120, slides_markdown.length * 25);
      const estSeconds = authoredVisionQa
        ? Math.min(AUTHORED_TIMEOUT_MS / 1000, base * 2)
        : Math.min(600, base);
      let durableTask =
        startingDurableTaskRef.current?.sourcePresentationId === presentationId
          ? startingDurableTaskRef.current
          : findDurableGenerationTask(presentationId);
      if (durableTask?.taskId && durableTask.status !== "retryable-error") {
        await runDurableGeneration(durableTask);
        return;
      }
      // A "starting" record can survive a lost POST response. Reusing it here
      // resubmits with the same idempotency key instead of creating a second job.
      if (!durableTask || durableTask.mode !== "authored") {
        durableTask = saveDurableGenerationTask(
          createDurableGenerationTask({
            mode: "authored",
            sourcePresentationId: presentationId,
            timeoutMs: AUTHORED_TIMEOUT_MS,
            progressDurationSeconds: estSeconds,
          })
        );
      }
      startingDurableTaskRef.current = durableTask;
      submittingDurableTaskRef.current = durableTask.idempotencyKey;
      setLoadingState(getDurableLoadingState(durableTask));
      try {
        const started = await PresentationGenerationApi.generateAuthoredAsync({
          content: slides_markdown[0].slice(0, 200),
          slides_markdown,
          // "Auto" is a sentinel, not a real language: send null so the backend applies
          // its language-aware fallback instead of authoring with a literal "Auto".
          language: language && language !== LanguageType.Auto ? language : null,
          vision_qa: authoredVisionQa,
          authored_style: authoredStyle,
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
        await runDurableGeneration(durableTask);
      } catch (error: any) {
        submittingDurableTaskRef.current = null;
        if (!isActiveRun()) return;
        if (!isRetryablePresentationGenerationError(error)) {
          clearDurableGenerationTask(durableTask.idempotencyKey);
          startingDurableTaskRef.current = null;
        }
        reportDurableGenerationFailure(error, "authored");
      } finally {
        if (isActiveRun()) setLoadingState(DEFAULT_LOADING_STATE);
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
        "템플릿 오류",
        "구조화 템플릿 선택 정보가 올바르지 않습니다. 템플릿을 다시 선택해 주세요."
      );
      return;
    }
    if (templateV2Selection) {
      const slides_markdown = (outlines || [])
        .map((outline) => outline.content)
        .filter((content) => content && content.trim().length > 0);
      if (slides_markdown.length === 0) {
        notify.warning("개요가 비어 있습니다", "먼저 개요를 생성해 주세요.");
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
        await runDurableGeneration(durableTask);
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
      setLoadingState(getDurableLoadingState(durableTask));

      try {
        const started =
          await PresentationGenerationApi.generateTemplateV2Async({
            content: slides_markdown[0].slice(0, 200),
            slides_markdown,
            language:
              language && language !== LanguageType.Auto ? language : null,
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
        await runDurableGeneration(durableTask);
      } catch (error: any) {
        submittingDurableTaskRef.current = null;
        if (!isActiveRun()) return;
        if (!isRetryablePresentationGenerationError(error)) {
          clearDurableGenerationTask(durableTask.idempotencyKey);
          startingDurableTaskRef.current = null;
        }
        reportDurableGenerationFailure(error, "template-v2");
      } finally {
        if (isActiveRun()) setLoadingState(DEFAULT_LOADING_STATE);
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

    setLoadingState({
      message: "Generating presentation data...",
      isLoading: true,
      showProgress: true,
      duration: 30,
    });

    try {
      let layout;

      // Check if it's a custom template (string = presentationId)
      if (typeof selectedTemplate === "string") {
        setLoadingState({
          message: "커스텀 템플릿 불러오는 중…",
          isLoading: true,
          showProgress: true,
          duration: 30,
        });

        // Fetch custom template details using the shared function
        const customTemplateDetail = await getCustomTemplateDetails(
          selectedTemplate
        );

        if (
          !customTemplateDetail ||
          customTemplateDetail.layouts.length === 0
        ) {
          notify.error("템플릿 오류", "커스텀 템플릿 레이아웃을 불러오지 못했습니다.");
          return;
        }

        setLoadingState({
          message: "발표자료 생성 중…",
          isLoading: true,
          showProgress: true,
          duration: 30,
        });

        layout = {
          name: customTemplateDetail.id,
          ordered: false,
          icon_weight: "bold",
          slides: customTemplateDetail.layouts.map((compiledLayout) => ({
            id: customTemplateDetail.id.startsWith("custom-")
              ? `${customTemplateDetail.id}:${compiledLayout.layoutId}`
              : `custom-${customTemplateDetail.id}:${compiledLayout.layoutId}`,
            name: compiledLayout.layoutName,
            description: compiledLayout.layoutDescription,
            templateID: customTemplateDetail.id,
            templateName: customTemplateDetail.name,
            json_schema: compiledLayout.schemaJSON,
          })),
        };
      } else {
        // Built-in template
        layout = {
          name: selectedTemplate.id,
          ordered: false,
          icon_weight: selectedTemplate.settings?.icon_weight || "bold",
          slides: selectedTemplate.layouts.map((layoutItem: any) => ({
            id: layoutItem.layoutId,
            name: layoutItem.layoutName,
            description: layoutItem.layoutDescription,
            templateID: selectedTemplate.id,
            templateName: selectedTemplate.name,
            json_schema: layoutItem.schemaJSON,
          })),
        };
      }

      const response = await PresentationGenerationApi.presentationPrepare({
        presentation_id: presentationId,
        outlines: outlines,
        layout: layout,
      });

      if (response) {
        dispatch(clearPresentationData());
        clearTheme();
        router.replace(
          `/presentation?id=${presentationId}&stream=true&type=standard`
        );
      }
    } catch (error: any) {
      // This failure is handled below with an in-app notification. Keep it out of
      // the Next.js development error overlay so the user can retry normally.
      console.warn("Presentation preparation did not complete.", error);

      // The browser can lose the long-running prepare response just before the
      // backend commits the finished structure. Confirm the persisted result for
      // a few seconds before reporting a failure so a completed deck is not
      // stranded behind an error toast.
      if (presentationId && isActiveRun()) {
        for (let attempt = 0; attempt < PREPARE_RECOVERY_ATTEMPTS; attempt += 1) {
          if (attempt > 0) {
            await new Promise((resolve) =>
              setTimeout(resolve, PREPARE_RECOVERY_POLL_MS)
            );
          }
          if (!isActiveRun()) return;

          try {
            const savedPresentation =
              await DashboardApi.getPresentation(presentationId);
            if (hasPreparedStructure(savedPresentation)) {
              dispatch(clearPresentationData());
              clearTheme();
              router.replace(
                `/presentation?id=${presentationId}&stream=true&type=standard`
              );
              return;
            }
          } catch {
            // The original prepare error remains the useful message. A transient
            // recovery-read failure should not replace it with a second error.
          }
        }
      }

      if (isRetryablePresentationGenerationError(error)) {
        notify.error(
          "생성이 일시 중단되었습니다",
          getRetryablePresentationGenerationMessage(error),
          {
            duration: 12_000,
            action: {
              label: "다시 시도",
              onClick: () => retrySubmitRef.current?.(),
            },
          }
        );
      } else {
        notify.error(
          "생성 오류",
          getApiErrorDisplayMessage(
            error,
            "발표자료를 생성하는 중 문제가 발생했습니다."
          )
        );
      }
    } finally {
      if (isActiveRun()) setLoadingState(DEFAULT_LOADING_STATE);
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
    authoredStyle,
    dispatch,
    router,
    clearTheme,
    getDurableLoadingState,
    reportDurableGenerationFailure,
    runDurableGeneration,
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
