import { useEffect, useState } from "react";
import { useDispatch, useSelector } from "react-redux";
import { toast } from "sonner";
import { setOutlines } from "@/store/slices/presentationGeneration";
import { jsonrepair } from "jsonrepair";
import { RootState } from "@/store/store";



export const useOutlineStreaming = (presentationId: string | null) => {
  const dispatch = useDispatch();
  const { outlines } = useSelector((state: RootState) => state.presentationGeneration);
  const [isStreaming, setIsStreaming] = useState(true);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    if (!presentationId || outlines.length > 0) return;

    let eventSource: EventSource;
    let accumulatedChunks = "";

    const initializeStream = async () => {
      setIsStreaming(true)
      setIsLoading(true)
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
                  setIsLoading(false)
                }
              } catch (error) {
                // JSON isn't complete yet, continue accumulating
              }
              break;

            case "complete":
              try {
                const outlinesData: string[] = data.presentation.outlines.slides;
                dispatch(setOutlines(outlinesData));
                  setIsStreaming(false)
                  setIsLoading(false)
                  eventSource.close();
              } catch (error) {
                console.error("Error parsing accumulated chunks:", error);
                toast.error("Failed to parse presentation data");
                eventSource.close();
              }
              accumulatedChunks = "";
              break;

            case "closing":
              setIsStreaming(false)
              setIsLoading(false)
              eventSource.close();
              break;
            case "error":
              setIsStreaming(false)
              setIsLoading(false)
              eventSource.close();
              toast.error('Error in outline streaming',
                {
                  description: data.detail || 'Failed to connect to the server. Please try again.',
                }
              );
              break;
          }
        });

        eventSource.onerror = () => {
          setIsStreaming(false)
          setIsLoading(false)
          eventSource.close();
          toast.error("Failed to connect to the server. Please try again.");
        };
      } catch (error) {
        setIsStreaming(false)
        setIsLoading(false)
        toast.error("Failed to initialize connection");
      } finally {
        setIsStreaming(false)
        setIsLoading(false)
      }
    };
    initializeStream();
    return () => {
      if (eventSource) {
        eventSource.close();
      }
    };
  }, [presentationId, dispatch]);

  return { isStreaming, isLoading };
}; 