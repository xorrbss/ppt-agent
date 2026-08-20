import { useEffect, useRef } from "react";
import { useTranslations } from "next-intl";
import { useDispatch } from "react-redux";
import {
  clearPresentationData,
  setPresentationData,
  setStreaming,
  updateSlide,
  type PresentationData,
} from "@/store/slices/presentationGeneration";
import { jsonrepair } from "jsonrepair";
import { notify } from "@/components/ui/sonner";
import { MixpanelEvent, trackEvent } from "@/utils/mixpanel";
import { getApiUrl, normalizeBackendAssetUrls } from "@/utils/api";
import { store } from "@/store/store";
import type { Slide } from "@/app/(presentation-generator)/types/slide";

const MAX_STREAM_RETRIES = 3;
const STREAM_RETRY_DELAY_MS = 1_000;

/** Chunk JSON replays each slide as first streamed; don't clobber URLs filled by `slide_assets`. */
const PLACEHOLDER_ASSET_MARKERS = [
  "/static/images/placeholder",
  "/static/icons/placeholder",
  "placeholder.jpg",
  "placeholder.svg",
];

function isPlaceholderAssetUrl(url: unknown): boolean {
  if (typeof url !== "string" || !url.trim()) return false;
  const u = url.toLowerCase();
  return PLACEHOLDER_ASSET_MARKERS.some((m) => u.includes(m));
}

/** Slides arrive as plain JSON; only `content` is inspected while merging. */
type StreamedSlide = Record<string, unknown> & { content?: unknown };

/** SSE frames emitted by `/api/v1/ppt/presentation/stream/{id}`. */
type PresentationStreamEvent =
  | { type: "chunk"; chunk: string }
  | { type: "fonts"; fonts: Record<string, string> | null }
  | {
      type: "slide_html";
      index: unknown;
      html: unknown;
      slide?: Slide | null;
      slide_id?: string | null;
    }
  | { type: "slide_assets"; slide_index: unknown; slide: Slide | null }
  | { type: "complete"; presentation: PresentationData }
  | { type: "closing"; presentation: PresentationData }
  | { type: "error"; detail?: string };

function mergeContentPreservingResolvedAssets(
  prev: unknown,
  incoming: unknown
): unknown {
  if (incoming === undefined || incoming === null) return prev;
  if (prev === undefined || prev === null) return incoming;

  if (Array.isArray(incoming)) {
    if (!Array.isArray(prev)) return incoming;
    return incoming.map((item, i) =>
      mergeContentPreservingResolvedAssets(prev[i], item)
    );
  }

  if (typeof incoming !== "object") return incoming;
  if (typeof prev !== "object") return incoming;

  const incomingRecord = incoming as Record<string, unknown>;
  const prevRecord = prev as Record<string, unknown>;
  const result: Record<string, unknown> = { ...incomingRecord };

  for (const key of Object.keys(incomingRecord)) {
    const pv = prevRecord[key];
    const iv = incomingRecord[key];

    if (iv !== null && typeof iv === "object") {
      if (Array.isArray(iv) && Array.isArray(pv)) {
        result[key] = iv.map((item, idx) =>
          mergeContentPreservingResolvedAssets(pv[idx], item)
        );
      } else if (
        !Array.isArray(iv) &&
        pv !== null &&
        typeof pv === "object" &&
        !Array.isArray(pv)
      ) {
        result[key] = mergeContentPreservingResolvedAssets(pv, iv);
      }
      continue;
    }

    if (
      key === "__image_url__" &&
      typeof iv === "string" &&
      typeof pv === "string"
    ) {
      if (isPlaceholderAssetUrl(iv) && !isPlaceholderAssetUrl(pv)) {
        result[key] = pv;
      }
    }
    if (
      key === "__icon_url__" &&
      typeof iv === "string" &&
      typeof pv === "string"
    ) {
      if (isPlaceholderAssetUrl(iv) && !isPlaceholderAssetUrl(pv)) {
        result[key] = pv;
      }
    }
  }

  return result;
}

function mergeSlidesPreservingResolvedAssets(
  prevSlides: StreamedSlide[] | undefined,
  incomingSlides: StreamedSlide[]
): StreamedSlide[] {
  if (!prevSlides?.length) return incomingSlides;
  return incomingSlides.map((incoming, idx) => {
    const prev = prevSlides[idx];
    if (!prev) return incoming;
    return {
      ...incoming,
      content: mergeContentPreservingResolvedAssets(
        prev.content,
        incoming.content
      ),
    };
  });
}

function mergeSingleSlidePreservingResolvedAssets(
  prevSlides: StreamedSlide[] | undefined,
  incomingSlide: StreamedSlide
): StreamedSlide[] {
  const nextSlides = prevSlides ? [...prevSlides] : [];
  const incomingIndex = Number(incomingSlide.index);
  const fallbackIndex = Number.isFinite(incomingIndex)
    ? incomingIndex
    : nextSlides.length;
  const matchIndex = nextSlides.findIndex(
    (slide) =>
      (incomingSlide.id && slide.id === incomingSlide.id) ||
      Number(slide.index) === fallbackIndex
  );
  const targetIndex = matchIndex >= 0 ? matchIndex : fallbackIndex;
  const previous = nextSlides[targetIndex];
  nextSlides[targetIndex] = previous
    ? {
        ...incomingSlide,
        content: mergeContentPreservingResolvedAssets(
          previous.content,
          incomingSlide.content
        ),
      }
    : incomingSlide;
  return nextSlides.filter(Boolean);
}

export const usePresentationStreaming = (
  presentationId: string,
  stream: string | null,
  setLoading: (loading: boolean) => void,
  setError: (error: boolean) => void,
  fetchUserSlides: () => void
) => {
  const dispatch = useDispatch();
  const t = useTranslations("community");
  const previousSlidesLength = useRef(0);

  useEffect(() => {
    if (!stream) {
      fetchUserSlides();
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

    const finalizeFailure = (description: string) => {
      closeEventSource();
      clearRetryTimer();
      setLoading(false);
      dispatch(setStreaming(false));
      setError(true);
      notify.error("프레젠테이션 스트리밍 실패", description);
    };

    const scheduleRetry = (reason: string): boolean => {
      if (retryCount >= MAX_STREAM_RETRIES || isClosed) {
        return false;
      }

      retryCount += 1;
      const retryDelay = STREAM_RETRY_DELAY_MS * retryCount;
      console.warn(
        `Presentation stream retry ${retryCount}/${MAX_STREAM_RETRIES}: ${reason}`
      );

      closeEventSource();
      clearRetryTimer();
      accumulatedChunks = "";
      previousSlidesLength.current = 0;

      retryTimer = setTimeout(() => {
        if (!isClosed) {
          openStream();
        }
      }, retryDelay);

      return true;
    };

    const openStream = () => {
      closeEventSource();
      // Forward the page's regenerate flag so the "재생성" action re-runs
      // generation; a plain refresh/prefetch (no flag) replays existing slides.
      const forceRegenerate =
        typeof window !== "undefined" &&
        new URLSearchParams(window.location.search).get("regenerate") === "true";
      const streamPath = `/api/v1/ppt/presentation/stream/${presentationId}${
        forceRegenerate ? "?regenerate=true" : ""
      }`;
      eventSource = new EventSource(getApiUrl(streamPath));

      eventSource.addEventListener("response", (event) => {
        let data: PresentationStreamEvent;
        try {
          data = JSON.parse(event.data) as PresentationStreamEvent;
        } catch {
          if (!scheduleRetry("invalid SSE payload")) {
            finalizeFailure("스트림 응답을 해석하지 못했습니다.");
          }
          return;
        }

        switch (data.type) {
          case "fonts": {
            if (data.fonts && typeof data.fonts === "object") {
              const prev = store.getState().presentationGeneration.presentationData;
              dispatch(
                setPresentationData({
                  ...(prev ?? {}),
                  fonts: data.fonts,
                  slides: prev?.slides ?? [],
                } as PresentationData)
              );
            }
            break;
          }

          case "slide_html": {
            const slideIndex = Number(data.index);
            if (
              !Number.isFinite(slideIndex) ||
              !data.slide ||
              typeof data.slide !== "object"
            ) break;

            const incomingSlide = data.slide;
            const normalizedSlide = normalizeBackendAssetUrls(
              incomingSlide
            ) as unknown as StreamedSlide;
            const prev = store.getState().presentationGeneration.presentationData;
            const mergedSlides = mergeSingleSlidePreservingResolvedAssets(
              prev?.slides,
              normalizedSlide
            );
            dispatch(
              setPresentationData({
                ...(prev ?? {}),
                slides: mergedSlides,
              } as PresentationData)
            );
            previousSlidesLength.current = mergedSlides.length;
            setLoading(false);
            break;
          }

          case "chunk":
            accumulatedChunks += data.chunk;
            try {
              const repairedJson = jsonrepair(accumulatedChunks);
              const partialData = JSON.parse(
                repairedJson
              ) as Partial<PresentationData>;
              const normalizedPartialData = normalizeBackendAssetUrls(partialData);

              if (
                normalizedPartialData.slides &&
                normalizedPartialData.slides.length > 0
              ) {
                const prev =
                  store.getState().presentationGeneration.presentationData;
                const mergedSlides = mergeSlidesPreservingResolvedAssets(
                  prev?.slides,
                  normalizedPartialData.slides
                );
                dispatch(
                  setPresentationData({
                    ...(prev ?? {}),
                    ...normalizedPartialData,
                    slides: mergedSlides,
                  } as PresentationData)
                );
                previousSlidesLength.current =
                  normalizedPartialData.slides.length;
                setLoading(false);
              }
            } catch {
              // JSON isn't complete yet, continue accumulating
            }
            break;

          case "slide_assets": {
            const idx = data.slide_index;
            if (
              typeof idx === "number" &&
              idx >= 0 &&
              data.slide &&
              typeof data.slide === "object"
            ) {
              dispatch(
                updateSlide({
                  index: idx,
                  slide: normalizeBackendAssetUrls(data.slide),
                })
              );
            }
            break;
          }

          case "complete":
            try {
              dispatch(setPresentationData(normalizeBackendAssetUrls(data.presentation)));
              dispatch(setStreaming(false));
              setLoading(false);
              isClosed = true;
              closeEventSource();
              clearRetryTimer();
              retryCount = 0;

              // Remove stream parameter from URL
              const newUrl = new URL(window.location.href);
              newUrl.searchParams.delete("stream");
              newUrl.searchParams.delete("regenerate");
              window.history.replaceState({}, "", newUrl.toString());
            } catch {
              if (!scheduleRetry("failed to parse complete payload")) {
                finalizeFailure("최종 발표자료 데이터를 해석하지 못했습니다.");
              }
            }
            accumulatedChunks = "";
            break;

          case "closing":
            dispatch(setPresentationData(normalizeBackendAssetUrls(data.presentation)));
            setLoading(false);
            dispatch(setStreaming(false));
            isClosed = true;
            closeEventSource();
            clearRetryTimer();
            retryCount = 0;

            // Remove stream parameter from URL
            const newUrl = new URL(window.location.href);
            newUrl.searchParams.delete("stream");
              newUrl.searchParams.delete("regenerate");
            window.history.replaceState({}, "", newUrl.toString());
            break;
          case "error":
            if (
              !scheduleRetry(
                data.detail || "server returned stream error response"
              )
            ) {
              finalizeFailure(
                data.detail ||
                  "서버에 연결하지 못했습니다. 다시 시도해 주세요."
              );
            }
            break;
        }
      });

      eventSource.onerror = (error) => {
        console.error("EventSource failed:", error);
        if (!scheduleRetry("connection lost")) {
          finalizeFailure("서버에 연결하지 못했습니다. 다시 시도해 주세요.");
        }
      };
    };

    dispatch(setStreaming(true));
    dispatch(clearPresentationData());
    trackEvent(MixpanelEvent.Presentation_Stream_API_Call);
    openStream();

    return () => {
      isClosed = true;
      closeEventSource();
      clearRetryTimer();
    };
  }, [presentationId, stream, dispatch, setLoading, setError, fetchUserSlides, t]);
};
