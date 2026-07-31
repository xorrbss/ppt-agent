import { useEffect, useRef, useState } from "react";
import { useDispatch, useSelector } from "react-redux";
import { notify } from "@/components/ui/sonner";
import { setOutlines } from "@/store/slices/presentationGeneration";
import { jsonrepair } from "jsonrepair";
import { RootState } from "@/store/store";
import { getApiUrl } from "@/utils/api";

const MAX_STREAM_RETRIES = 3;
const STREAM_RETRY_DELAY_MS = 1_000;

export type OutlineEventSourceFactory = (url: string) => EventSource;

const createBrowserEventSource: OutlineEventSourceFactory = (url) =>
  new EventSource(url);

export const useOutlineStreaming = (
  presentationId: string | null,
  createEventSource: OutlineEventSourceFactory = createBrowserEventSource
) => {
  const dispatch = useDispatch();
  const { outlines } = useSelector((state: RootState) => state.presentationGeneration);
  const [isStreaming, setIsStreaming] = useState(true);
  const [isLoading, setIsLoading] = useState(true);
  const [hasCompleted, setHasCompleted] = useState(false);
  const [activeSlideIndex, setActiveSlideIndex] = useState<number | null>(null);
  const [highestActiveIndex, setHighestActiveIndex] = useState<number>(-1);
  // A streamed chunk is written to Redux so the UI can render it progressively.
  // Remember only the outlines that existed before this hook mounted; otherwise
  // the first partial chunk would look like a completed, pre-existing outline.
  const outlinesPreexistingRef = useRef(outlines.length > 0);
  const prevSlidesRef = useRef<{ content: string }[]>([]);
  const activeIndexRef = useRef<number>(-1);
  const highestIndexRef = useRef<number>(-1);

  useEffect(() => {
    if (!presentationId || outlinesPreexistingRef.current) {
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

    const resetStreamingState = () => {
      setIsStreaming(false);
      setIsLoading(false);
      setHasCompleted(false);
      setActiveSlideIndex(null);
      setHighestActiveIndex(-1);
      activeIndexRef.current = -1;
      highestIndexRef.current = -1;
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
        getApiUrl(`/api/v1/ppt/outlines/stream/${presentationId}`)
      );

      eventSource.addEventListener("response", (event) => {
        let data: any;
        try {
          data = JSON.parse(event.data);
        } catch {
          if (!scheduleRetry("invalid SSE payload")) {
            resetStreamingState();
            notify.error("스트림 파싱 실패", "개요 스트림 응답을 파싱하지 못했습니다.");
          }
          return;
        }

        switch (data.type) {
          case "chunk":
            accumulatedChunks += data.chunk;
            try {
              const repairedJson = jsonrepair(accumulatedChunks);
              const partialData = JSON.parse(repairedJson);

              if (partialData.slides) {
                const nextSlides: { content: string }[] = partialData.slides || [];
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
              dispatch(setOutlines(outlinesData));
              setIsStreaming(false);
              setIsLoading(false);
              setHasCompleted(true);
              setActiveSlideIndex(null);
              setHighestActiveIndex(-1);
              prevSlidesRef.current = outlinesData;
              activeIndexRef.current = -1;
              highestIndexRef.current = -1;
              isClosed = true;
              closeEventSource();
              clearRetryTimer();
              retryCount = 0;
            } catch {
              if (!scheduleRetry("failed to parse complete payload")) {
                resetStreamingState();
                notify.error("파싱 실패", "프레젠테이션 데이터를 파싱하지 못했습니다.");
              }
            }
            accumulatedChunks = "";
            break;

          case "closing":
            setIsStreaming(false);
            setIsLoading(false);
            setHasCompleted(false);
            setActiveSlideIndex(null);
            setHighestActiveIndex(-1);
            activeIndexRef.current = -1;
            highestIndexRef.current = -1;
            isClosed = true;
            closeEventSource();
            clearRetryTimer();
            retryCount = 0;
            break;
          case "error":
            if (!scheduleRetry(data.detail || "server returned stream error")) {
              resetStreamingState();
              closeEventSource();
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
          resetStreamingState();
          closeEventSource();
          notify.error("연결 실패", "서버에 연결하지 못했습니다. 다시 시도해 주세요.");
        }
      };
    };

    setIsStreaming(true);
    setIsLoading(true);
    setHasCompleted(false);
    openStream();

    return () => {
      isClosed = true;
      closeEventSource();
      clearRetryTimer();
    };
  }, [presentationId, dispatch, createEventSource]);

  return {
    isStreaming,
    isLoading,
    hasCompleted,
    activeSlideIndex,
    highestActiveIndex,
  };
};
