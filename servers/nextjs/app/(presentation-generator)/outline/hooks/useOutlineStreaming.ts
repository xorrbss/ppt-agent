import { useEffect, useState } from "react";
import { useDispatch } from "react-redux";
import { toast } from "@/hooks/use-toast";
import { setOutlines, SlideOutline } from "@/store/slices/presentationGeneration";
import { jsonrepair } from "jsonrepair";
import { StreamState } from "../types/index";

const DEFAULT_STREAM_STATE: StreamState = {
  isStreaming: false,
  isLoading: true,
};

export const useOutlineStreaming = (presentationId: string | null) => {
  const dispatch = useDispatch();
  const [streamState, setStreamState] = useState<StreamState>(DEFAULT_STREAM_STATE);

  useEffect(() => {
    if (!presentationId) return;

    let eventSource: EventSource;
    let accumulatedChunks = "";

    const initializeStream = async () => {
      setStreamState({ isStreaming: true, isLoading: true });

      try {
        eventSource = new EventSource(
          `/api/v1/ppt/outlines/stream?presentation_id=${presentationId}`
        );

        eventSource.addEventListener("response", (event) => {
          const data = JSON.parse(event.data);

          switch (data.type) {
            case "chunk":
              accumulatedChunks += data.chunk;
              try {
                const repairedJson = jsonrepair(accumulatedChunks);
                const partialData = JSON.parse(repairedJson);
                if (partialData.slides) {
                  dispatch(setOutlines(partialData.slides));
                  setStreamState(prev => ({ ...prev, isLoading: false }));
                }
              } catch (error) {
                // JSON isn't complete yet, continue accumulating
              }
              break;

            case "complete":
              try {
                const outlinesData: SlideOutline[] = JSON.parse(data.presentation).outlines;
                dispatch(setOutlines(outlinesData));
                setStreamState({ isStreaming: false, isLoading: false });
                eventSource.close();
              } catch (error) {
                console.error("Error parsing accumulated chunks:", error);
                toast({
                  title: "Error",
                  description: "Failed to parse presentation data",
                  variant: "destructive",
                });
                eventSource.close();
              }
              accumulatedChunks = "";
              break;

            case "closing":
              setStreamState({ isStreaming: false, isLoading: false });
              eventSource.close();
              break;
          }
        });

        eventSource.onerror = () => {
          setStreamState({ isStreaming: false, isLoading: false });
          eventSource.close();
          toast({
            title: "Connection Error",
            description: "Failed to connect to the server. Please try again.",
            variant: "destructive",
          });
        };
      } catch (error) {
        setStreamState({ isStreaming: false, isLoading: false });
        toast({
          title: "Error",
          description: "Failed to initialize connection",
          variant: "destructive",
        });
      }
    };

    initializeStream();

    return () => {
      if (eventSource) {
        eventSource.close();
      }
    };
  }, [presentationId, dispatch]);

  return streamState;
}; 