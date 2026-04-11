import { useEffect, useRef } from "react";
import { useDispatch } from "react-redux";
import {
  clearPresentationData,
  setPresentationData,
  setStreaming,
} from "@/store/slices/presentationGeneration";
import { jsonrepair } from "jsonrepair";
import { toast } from "sonner";
import { MixpanelEvent, trackEvent } from "@/utils/mixpanel";
import { getFastAPIUrl, resolveBackendAssetUrl } from "@/utils/api";

const normalizePresentationAssets = <T,>(input: T): T => {
  if (Array.isArray(input)) {
    return input.map((item) => normalizePresentationAssets(item)) as T;
  }

  if (input && typeof input === "object") {
    const normalized: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(input as Record<string, unknown>)) {
      if (typeof value === "string") {
        normalized[key] = resolveBackendAssetUrl(value);
      } else {
        normalized[key] = normalizePresentationAssets(value);
      }
    }
    return normalized as T;
  }

  return input;
};

export const usePresentationStreaming = (
  presentationId: string,
  stream: string | null,
  setLoading: (loading: boolean) => void,
  setError: (error: boolean) => void,
  fetchUserSlides: () => void
) => {
  const dispatch = useDispatch();
  const previousSlidesLength = useRef(0);

  useEffect(() => {
    let eventSource: EventSource;
    let accumulatedChunks = "";

    const initializeStream = async () => {
      dispatch(setStreaming(true));
      dispatch(clearPresentationData());

      trackEvent(MixpanelEvent.Presentation_Stream_API_Call);

      eventSource = new EventSource(
        `${getFastAPIUrl()}/api/v1/ppt/presentation/stream/${presentationId}`
      );

      eventSource.addEventListener("response", (event) => {
        const data = JSON.parse(event.data);

        switch (data.type) {
          case "chunk":
            accumulatedChunks += data.chunk;
            try {
              const repairedJson = jsonrepair(accumulatedChunks);
              const partialData = JSON.parse(repairedJson);
              const normalizedPartialData = normalizePresentationAssets(partialData);

              if (normalizedPartialData.slides) {
                if (
                  normalizedPartialData.slides.length !== previousSlidesLength.current &&
                  normalizedPartialData.slides.length > 0
                ) {
                  dispatch(
                    setPresentationData({
                      ...normalizedPartialData,
                      slides: normalizedPartialData.slides,
                    })
                  );
                  previousSlidesLength.current = normalizedPartialData.slides.length;
                  setLoading(false);
                }
              }
            } catch (error) {
              // JSON isn't complete yet, continue accumulating
            }
            break;

          case "complete":
            try {
              dispatch(setPresentationData(normalizePresentationAssets(data.presentation)));
              dispatch(setStreaming(false));
              setLoading(false);
              eventSource.close();

              // Remove stream parameter from URL
              const newUrl = new URL(window.location.href);
              newUrl.searchParams.delete("stream");
              window.history.replaceState({}, "", newUrl.toString());
            } catch (error) {
              eventSource.close();
              console.error("Error parsing accumulated chunks:", error);
            }
            accumulatedChunks = "";
            break;

          case "closing":
            dispatch(setPresentationData(normalizePresentationAssets(data.presentation)));
            setLoading(false);
            dispatch(setStreaming(false));
            eventSource.close();

            // Remove stream parameter from URL
            const newUrl = new URL(window.location.href);
            newUrl.searchParams.delete("stream");
            window.history.replaceState({}, "", newUrl.toString());
            break;
          case "error":
            eventSource.close();
            toast.error("Error in outline streaming", {
              description:
                data.detail ||
                "Failed to connect to the server. Please try again.",
            });
            setLoading(false);
            dispatch(setStreaming(false));
            setError(true);
            break;
        }
      });

      eventSource.onerror = (error) => {
        console.error("EventSource failed:", error);
        setLoading(false);
        dispatch(setStreaming(false));
        setError(true);
        eventSource.close();
      };
    };

    if (stream) {
      initializeStream();
    } else {
      fetchUserSlides();
    }

    return () => {
      if (eventSource) {
        eventSource.close();
      }
    };
  }, [presentationId, stream, dispatch, setLoading, setError, fetchUserSlides]);
};
