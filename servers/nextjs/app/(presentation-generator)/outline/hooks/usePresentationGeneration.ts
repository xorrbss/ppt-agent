import { useState, useCallback } from "react";
import { useDispatch, useSelector } from "react-redux";
import { RootState } from "@/store/store";
import { usePathname, useRouter } from "next/navigation";
import { notify } from "@/components/ui/sonner";
import { clearPresentationData } from "@/store/slices/presentationGeneration";
import { PresentationGenerationApi } from "../../services/api/presentation-generation";
import { DashboardApi } from "../../services/api/dashboard";
import { LoadingState, TABS } from "../types/index";
import { TemplateLayoutsWithSettings } from "@/app/presentation-templates/utils";
import { getCustomTemplateDetails } from "@/app/hooks/useCustomTemplates";
import { MixpanelEvent, trackEvent } from "@/utils/mixpanel";
import { AUTHORED_TEMPLATE_ID } from "../components/TemplateSelection";
import { LanguageType } from "../../upload/type";

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
const AUTHORED_TIMEOUT_MS = 20 * 60 * 1000;
// Tolerate a few consecutive transient status-poll failures (a 502/network blip during
// the minutes-long poll) before aborting — the backend task keeps running regardless.
const AUTHORED_MAX_POLL_ERRORS = 3;

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

  const clearTheme = () => {
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
  };

  const handleSubmit = useCallback(async () => {
    if (!selectedTemplate) {
      setActiveTab(TABS.LAYOUTS);
      return;
    }
    if (!validateInputs()) return;

    // Authored mode: bypass the layout prepare/stream path. Generate via the async
    // endpoint (minutes-long), passing the reviewed outline as slides_markdown so the
    // user's edits are honoured, then poll to completion and open the viewer.
    if (selectedTemplate === AUTHORED_TEMPLATE_ID) {
      const slides_markdown = (outlines || [])
        .map((o) => o.content)
        .filter((c) => c && c.trim().length > 0);
      if (slides_markdown.length === 0) {
        notify.warning("개요가 비어 있습니다", "먼저 개요를 생성하세요.");
        return;
      }
      // Progress estimate that PACES the bar. Authored slides are generated concurrently
      // (≈ one round) so wall-time grows slowly with count; vision-QA adds a second
      // authoring round (~2x). Clamped to the poll deadline so we never pace longer than
      // we will wait. This does NOT prevent the bar finishing early — ProgressBar parks
      // at 95% until generation actually completes, so an under-estimate just stalls there.
      const base = Math.max(120, slides_markdown.length * 25);
      const estSeconds = authoredVisionQa
        ? Math.min(AUTHORED_TIMEOUT_MS / 1000, base * 2)
        : Math.min(600, base);
      // Derive the headline from the same estimate so it can't contradict the bar's pacing
      // (no fixed "1~3분" that lies for larger decks).
      const estMinutes = Math.max(1, Math.round(estSeconds / 60));
      setLoadingState({
        message: authoredVisionQa
          ? `AI가 슬라이드를 저작·검수하는 중입니다… (약 ${estMinutes}분 소요)`
          : `AI가 슬라이드를 저작하는 중입니다… (약 ${estMinutes}분 소요)`,
        isLoading: true,
        showProgress: true,
        duration: estSeconds,
      });
      try {
        const started = await PresentationGenerationApi.generateAuthoredAsync({
          content: slides_markdown[0].slice(0, 200),
          slides_markdown,
          // "Auto" is a sentinel, not a real language: send null so the backend applies
          // its language-aware fallback instead of authoring with a literal "Auto".
          language: language && language !== LanguageType.Auto ? language : null,
          vision_qa: authoredVisionQa,
          authored_style: authoredStyle,
        });
        const taskId = started?.id;
        if (!taskId) throw new Error("생성 작업을 시작하지 못했습니다.");

        const deadlineMs = Date.now() + AUTHORED_TIMEOUT_MS;
        let pollErrors = 0;
        while (true) {
          if (Date.now() > deadlineMs) {
            throw new Error("생성 시간이 초과되었습니다. 잠시 후 다시 시도하세요.");
          }
          await new Promise((r) => setTimeout(r, AUTHORED_POLL_MS));
          let task;
          try {
            task = await PresentationGenerationApi.getGenerationStatus(taskId);
            pollErrors = 0;
          } catch (pollErr) {
            // A single transient blip must not abort a generation still running on the
            // backend. Tolerate a few consecutive failures, then surface the error.
            pollErrors += 1;
            if (pollErrors >= AUTHORED_MAX_POLL_ERRORS) throw pollErr;
            console.warn(
              `authored status poll failed (${pollErrors}/${AUTHORED_MAX_POLL_ERRORS}); retrying`,
              pollErr
            );
            continue;
          }
          if (task?.status === "completed") {
            const newId = task?.data?.presentation_id;
            if (!newId) throw new Error("생성 결과를 찾을 수 없습니다.");
            // The authored deck is a fresh presentation; delete the upload-created
            // outline shell so it doesn't linger as an orphan (best-effort).
            if (presentationId && presentationId !== newId) {
              try {
                await DashboardApi.deletePresentation(presentationId);
              } catch (e) {
                console.warn("Failed to clean up outline shell presentation", e);
              }
            }
            dispatch(clearPresentationData());
            clearTheme();
            router.replace(`/presentation?id=${newId}`);
            return;
          }
          if (task?.status === "error") {
            throw new Error(task?.message || "프레젠테이션 생성에 실패했습니다.");
          }
        }
      } catch (error: any) {
        console.error("Error in authored generation.", error);
        notify.error(
          "생성 오류",
          error.message || "AI 저작 생성 중 오류가 발생했습니다."
        );
      } finally {
        setLoadingState(DEFAULT_LOADING_STATE);
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
      console.error("Error In Presentation Generation(prepare).", error);
      notify.error(
        "생성 오류",
        error.message || "프레젠테이션 생성 중 오류가 발생했습니다."
      );
    } finally {
      setLoadingState(DEFAULT_LOADING_STATE);
    }
  }, [selectedTemplate, validateInputs, pathname, presentationId, outlines, setActiveTab, authoredVisionQa, language, authoredStyle, dispatch, router]);

  return { loadingState, handleSubmit };
};
