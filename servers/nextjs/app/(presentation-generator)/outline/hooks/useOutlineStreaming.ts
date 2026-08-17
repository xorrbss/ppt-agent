import { useEffect, useRef, useState } from "react";
import { useDispatch, useSelector } from "react-redux";
import { notify } from "@/components/ui/sonner";
import { setOutlines } from "@/store/slices/presentationGeneration";
import { jsonrepair } from "jsonrepair";
import { RootState } from "@/store/store";
import { DashboardApi } from "@/app/(presentation-generator)/services/api/dashboard";
import { CodexAuthApi } from "@/app/(presentation-generator)/services/api/codex-auth";
import { useTranslations } from "next-intl";

const MAX_STREAM_RETRIES = 3;
const STREAM_RETRY_DELAY_MS = 1_000;
const OUTLINE_RECOVERY_INTERVAL_MS = 5_000;

export type OutlineEventSourceFactory = (url: string) => EventSource;

type OutlineSlide = { content: string };

/** SSE frames emitted by `/api/outline-stream/{id}`. */
type OutlineStreamEvent =
  | { type: "chunk"; chunk: string }
  | { type: "complete"; presentation: { outlines: { slides: OutlineSlide[] } } }
  | { type: "closing" }
  | { type: "error"; detail?: string };

const createBrowserEventSource: OutlineEventSourceFactory = (url) =>
  new EventSource(url);

export const useOutlineStreaming = (
  presentationId: string | null,
  createEventSource: OutlineEventSourceFactory = createBrowserEventSource
) => {
  const t = useTranslations("generation");
  const dispatch = useDispatch();
  const { outlines } = useSelector((state: RootState) => state.presentationGeneration);
  const llmProvider = useSelector(
    (state: RootState) => state.userConfig?.llm_config?.LLM,
  );
  const [isStreaming, setIsStreaming] = useState(true);
  const [isLoading, setIsLoading] = useState(true);
  const [hasCompleted, setHasCompleted] = useState(false);
  const [activeSlideIndex, setActiveSlideIndex] = useState<number | null>(null);
  const [highestActiveIndex, setHighestActiveIndex] = useState<number>(-1);
  const [retryNonce, setRetryNonce] = useState(0);
  const prevSlidesRef = useRef<{ content: string }[]>([]);
  const activeIndexRef = useRef<number>(-1);
  const highestIndexRef = useRef<number>(-1);
  const outlinesRef = useRef(outlines);

  useEffect(() => {
    outlinesRef.current = outlines;
  }, [outlines]);

  useEffect(() => {
    if (!presentationId || outlinesRef.current.length > 0) {
      // Nothing to stream: either there is no presentation yet, or the outlines
      // are already in the store (e.g. back-navigation into an already-completed
      // auto flow). isStreaming/isLoading initialize to true; clear them here so
      // the UI doesn't sit on a permanent "개요를 생성하는 중…" loader forever.
      setIsStreaming(false);
      setIsLoading(false);
      setHasCompleted(false);
      return;
    }

    let eventSource: EventSource | null = null;
    let accumulatedChunks = "";
    let retryCount = 0;
    let isClosed = false;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let recoveryTimer: ReturnType<typeof setInterval> | null = null;
    let recoveryInFlight = false;
    let codexRefreshAttempted = false;

    const closeEventSource = () => {
      if (eventSource) {
        eventSource.close();
        eventSource = null;
      }
    };

    const clearRetryTimer = () => {
      if (retryTimer) {
        clearTimeout(retryTimer);
        retryTimer = null;
      }
    };

    const clearRecoveryTimer = () => {
      if (recoveryTimer) {
        clearInterval(recoveryTimer);
        recoveryTimer = null;
      }
    };

    const resetStreamingState = () => {
      setIsStreaming(false);
      setIsLoading(false);
      setHasCompleted(false);
      setActiveSlideIndex(null);
      setHighestActiveIndex(-1);
      activeIndexRef.current = -1;
      highestIndexRef.current = -1;
    };

    const stopStreaming = () => {
      resetStreamingState();
      isClosed = true;
      closeEventSource();
      clearRetryTimer();
      clearRecoveryTimer();
    };

    const refreshCodexSession = async (force = false): Promise<boolean> => {
      if (llmProvider?.toLowerCase() !== "codex" || codexRefreshAttempted) {
        return false;
      }
      const refreshed = force
        ? (await CodexAuthApi.refresh(), true)
        : await CodexAuthApi.refreshIfExpired();
      codexRefreshAttempted = refreshed;
      return refreshed;
    };

    const failExpiredCodexSession = () => {
      stopStreaming();
      notify.error(
        t("codexSessionExpiredTitle"),
        t("codexSessionExpiredDescription"),
      );
    };

    const finishStreaming = (outlineSlides: { content: string }[]) => {
      dispatch(setOutlines(outlineSlides));
      setIsStreaming(false);
      setIsLoading(false);
      setHasCompleted(true);
      setActiveSlideIndex(null);
      setHighestActiveIndex(-1);
      prevSlidesRef.current = outlineSlides;
      activeIndexRef.current = -1;
      highestIndexRef.current = -1;
      isClosed = true;
      closeEventSource();
      clearRetryTimer();
      clearRecoveryTimer();
      retryCount = 0;
    };

    const recoverSavedOutlines = async (): Promise<boolean> => {
      if (isClosed || recoveryInFlight) return false;

      recoveryInFlight = true;
      try {
        const presentation = await DashboardApi.getPresentation(presentationId);
        const savedSlides = presentation?.outlines?.slides;
        if (Array.isArray(savedSlides) && savedSlides.length > 0) {
          finishStreaming(savedSlides);
          return true;
        }
      } catch {
        // The stream remains the primary path. A transient recovery read failure
        // should not interrupt generation or show a second error to the user.
      } finally {
        recoveryInFlight = false;
      }

      return false;
    };

    const scheduleRetry = (reason: string): boolean => {
      if (retryCount >= MAX_STREAM_RETRIES || isClosed) {
        return false;
      }

      retryCount += 1;
      const retryDelay = STREAM_RETRY_DELAY_MS * retryCount;
      console.warn(
        `Outline stream retry ${retryCount}/${MAX_STREAM_RETRIES}: ${reason}`
      );

      closeEventSource();
      clearRetryTimer();
      accumulatedChunks = "";
      prevSlidesRef.current = [];
      activeIndexRef.current = -1;
      highestIndexRef.current = -1;

      retryTimer = setTimeout(() => {
        if (!isClosed) {
          openStream();
        }
      }, retryDelay);

      return true;
    };

    const openStream = () => {
      closeEventSource();
      eventSource = createEventSource(
        `/api/outline-stream/${encodeURIComponent(presentationId)}`
      );

      eventSource.addEventListener("response", (event) => {
        let data: OutlineStreamEvent;
        try {
          data = JSON.parse(event.data) as OutlineStreamEvent;
        } catch {
          if (!scheduleRetry("invalid SSE payload")) {
            stopStreaming();
            notify.error("스트림 파싱 실패", "개요 스트림 응답을 파싱하지 못했습니다.");
          }
          return;
        }

        switch (data.type) {
          case "chunk":
            accumulatedChunks += data.chunk;
            try {
              const repairedJson = jsonrepair(accumulatedChunks);
              const partialData = JSON.parse(repairedJson) as {
                slides?: OutlineSlide[];
              };

              if (partialData.slides) {
                const nextSlides: OutlineSlide[] = partialData.slides || [];
                try {
                  const prev = prevSlidesRef.current || [];
                  let changedIndex: number | null = null;
                  const maxLen = Math.max(prev.length, nextSlides.length);
                  for (let i = 0; i < maxLen; i++) {
                    const prevContent = prev[i]?.content;
                    const nextContent = nextSlides[i]?.content;
                    if (nextContent !== prevContent) {
                      changedIndex = i;
                    }
                  }
                  const prevActive = activeIndexRef.current;
                  let nextActive = changedIndex ?? prevActive;
                  if (nextActive < prevActive) {
                    nextActive = prevActive;
                  }
                  activeIndexRef.current = nextActive;
                  setActiveSlideIndex(nextActive);

                  if (nextActive > highestIndexRef.current) {
                    highestIndexRef.current = nextActive;
                    setHighestActiveIndex(nextActive);
                  }
                } catch {}

                prevSlidesRef.current = nextSlides;
                dispatch(setOutlines(nextSlides));
                setIsLoading(false);
              }
            } catch {
              // JSON isn't complete yet, continue accumulating
            }
            break;

          case "complete":
            try {
              const outlinesData: { content: string }[] =
                data.presentation.outlines.slides;
              finishStreaming(outlinesData);
            } catch {
              if (!scheduleRetry("failed to parse complete payload")) {
                stopStreaming();
                notify.error("파싱 실패", "프레젠테이션 데이터를 파싱하지 못했습니다.");
              }
            }
            accumulatedChunks = "";
            break;

          case "closing":
            stopStreaming();
            retryCount = 0;
            break;
          case "error":
            if (
              !codexRefreshAttempted &&
              /(?:authentication|access) token (?:is |has )?expired/i.test(
                data.detail || "",
              )
            ) {
              closeEventSource();
              void refreshCodexSession(true)
                .then((refreshed) => {
                  if (!isClosed && refreshed) {
                    retryCount = 0;
                    openStream();
                  }
                })
                .catch(failExpiredCodexSession);
              break;
            }
            if (!scheduleRetry(data.detail || "server returned stream error")) {
              stopStreaming();
              notify.error(
                "개요 스트리밍 실패",
                data.detail ||
                  "서버에 연결하지 못했습니다. 다시 시도해 주세요."
              );
            }
            break;
        }
      });

      eventSource.onerror = () => {
        if (!scheduleRetry("connection lost")) {
          stopStreaming();
          notify.error("연결 실패", "서버에 연결하지 못했습니다. 다시 시도해 주세요.");
        }
      };
    };

    setIsStreaming(true);
    setIsLoading(true);
    setHasCompleted(false);
    recoveryTimer = setInterval(() => {
      void recoverSavedOutlines();
    }, OUTLINE_RECOVERY_INTERVAL_MS);
    void recoverSavedOutlines().then(async (recovered) => {
      if (recovered || isClosed) return;
      try {
        await refreshCodexSession();
      } catch {
        failExpiredCodexSession();
        return;
      }
      if (!isClosed) openStream();
    });

    return () => {
      isClosed = true;
      closeEventSource();
      clearRetryTimer();
      clearRecoveryTimer();
    };
  }, [presentationId, dispatch, createEventSource, llmProvider, retryNonce, t]);

  return {
    isStreaming,
    isLoading,
    hasCompleted,
    activeSlideIndex,
    highestActiveIndex,
    retry: () => setRetryNonce((current) => current + 1),
  };
};
