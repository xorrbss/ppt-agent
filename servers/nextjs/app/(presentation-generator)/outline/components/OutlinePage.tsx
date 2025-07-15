"use client";
import React, { useEffect, useState } from "react";
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { OutlineItem } from "./OutlineItem";
import { Button } from "@/components/ui/button";
import { RootState } from "@/store/store";
import { useSelector, useDispatch } from "react-redux";
import { useRouter } from "next/navigation";
import { PresentationGenerationApi } from "../../services/api/presentation-generation";
import { toast } from "@/hooks/use-toast";
import {
  setPresentationData,
  setOutlines,
  SlideOutline,
} from "@/store/slices/presentationGeneration";
import { OverlayLoader } from "@/components/ui/overlay-loader";
import Wrapper from "@/components/Wrapper";
import { jsonrepair } from "jsonrepair";

const OutlinePage = () => {
  const dispatch = useDispatch();
  const router = useRouter();

  const sensors = useSensors(
    useSensor(PointerSensor),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    })
  );

  const { presentation_id, outlines } = useSelector(
    (state: RootState) => state.presentationGeneration
  );

  const { currentTheme, currentColors } = useSelector(
    (state: RootState) => state.theme
  );

  const [loadingState, setLoadingState] = useState({
    message: "",
    isLoading: false,
    showProgress: false,
    duration: 0,
  });

  const [isStreaming, setStreaming] = useState<boolean>(false);
  const [isLoading, setLoading] = useState<boolean>(true);

  useEffect(() => {
    let evtSource: EventSource;
    let accumulatedChunks = "";

    const fetchSlides = async () => {
      setStreaming(true);
      setLoading(true);

      try {
        evtSource = new EventSource(
          `/api/v1/ppt/outlines/stream?presentation_id=${presentation_id}`
        );

        evtSource.onopen = () => {
          console.log('connection open');
        };

        evtSource.addEventListener("response", (event) => {
          const data = JSON.parse(event.data);

          if (data.type === "chunk") {
            accumulatedChunks += data.chunk;

            try {
              const repairedJson = jsonrepair(accumulatedChunks);
              const partialData = JSON.parse(repairedJson);

              if (partialData.slides) {
                dispatch(setOutlines(partialData.slides));
                setLoading(false);
              }
            } catch (error) {
              // It's okay if this fails, it just means the JSON isn't complete yet
            }
          } else if (data.type === "complete") {
            try {
              setLoading(false);
              setStreaming(false);
              const outlinesData: SlideOutline[] = JSON.parse(data.presentation).outlines;
              dispatch(setOutlines(outlinesData));
              evtSource.close();
            } catch (error) {
              evtSource.close();
              console.error("Error parsing accumulated chunks:", error);
              toast({
                title: "Error",
                description: "Failed to parse presentation data",
                variant: "destructive",
              });
            }
            accumulatedChunks = "";
          } else if (data.type === "closing") {
            setLoading(false);
            setStreaming(false);
            evtSource.close();
          }
        });

        evtSource.onerror = (error) => {

          setLoading(false);
          setStreaming(false);
          evtSource.close();

          toast({
            title: "Connection Error",
            description: "Failed to connect to the server. Please try again.",
            variant: "destructive",
          });
        };
      } catch (error) {

        setLoading(false);
        setStreaming(false);

        toast({
          title: "Error",
          description: "Failed to initialize connection",
          variant: "destructive",
        });
      }
    };

    if (presentation_id) {
      fetchSlides();
    }

    // Cleanup function
    return () => {
      if (evtSource) {
        evtSource.close();
      }
    };
  }, [presentation_id, dispatch]);

  const handleDragEnd = (event: any) => {
    const { active, over } = event;

    if (!active || !over || !outlines) return;

    if (active.id !== over.id) {
      // Find the indices of the dragged and target items
      const oldIndex = outlines.findIndex((item) => item.title === active.id);
      const newIndex = outlines.findIndex((item) => item.title === over.id);

      // Reorder the array
      const reorderedArray = arrayMove(outlines, oldIndex, newIndex);

      // Update local state
      setOutlines(reorderedArray);
      // Update the store with new order
      dispatch(setOutlines(reorderedArray));
    }
  };

  const handleSubmit = async () => {
    if (!outlines || outlines.length === 0) {
      toast({
        title: "No Outlines",
        description: "Please wait for outlines to load before generating presentation",
        variant: "destructive",
      });
      return;
    }
    // Generate data
    setLoadingState({
      message: "Generating presentation data...",
      isLoading: true,
      showProgress: true,
      duration: 30,
    });

    try {
      const response = await PresentationGenerationApi.presentationPrepare({
        presentation_id: presentation_id,
        outlines: outlines,
      });

      if (response) {
        dispatch(setPresentationData(response));

        toast({
          title: "Success",
          description: "Presentation generated successfully!",
          variant: "default",
        });

        router.push(`/presentation?id=${presentation_id}&stream=true`);
      }
    } catch (error) {
      console.error("error in data generation", error);
      toast({
        title: "Generation Error",
        description: "Failed to generate presentation. Please try again.",
        variant: "destructive",
      });
    } finally {
      setLoadingState({
        isLoading: false,
        message: "",
        showProgress: false,
        duration: 0,
      });
    }
  };

  const handleAddSlide = () => {
    if (!outlines) return;

    const newSlide: SlideOutline = {
      title: "New Slide",
      body: "",
      // Add any other required properties based on your SlideOutline type
    };

    const updatedOutlines = [...outlines, newSlide];
    setOutlines(updatedOutlines);
    dispatch(setOutlines(updatedOutlines));
  };


  if (!presentation_id) {
    return (
      <Wrapper>
        <div className="max-w-[1000px] mx-auto px-4 sm:px-6 pb-6">
          <div className="mt-4 sm:mt-8 font-instrument_sans text-center">
            <h4 className="text-lg sm:text-xl font-medium mb-4">
              No Presentation ID Found
            </h4>
            <p className="text-gray-600">Please start a new presentation.</p>
          </div>
        </div>
      </Wrapper>
    );
  }

  return (
    <Wrapper>
      <OverlayLoader
        show={loadingState.isLoading}
        text={loadingState.message}
        showProgress={loadingState.showProgress}
        duration={loadingState.duration}
      />

      <div className="max-w-[1000px] mx-auto px-4 sm:px-6 pb-6">
        <div className="mt-4 sm:mt-8 font-instrument_sans relative">
          <div className="flex items-center justify-between mb-4">
            <h4 className="text-lg sm:text-xl font-instrument_sans font-medium">
              Outline
            </h4>
            {isStreaming && (
              <div className="flex items-center text-sm text-blue-600">
                <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-blue-600 mr-2"></div>
                Generating outlines...
              </div>
            )}
          </div>

          {/* Skeleton loading state */}
          {isLoading && (
            <div className="space-y-4">
              {[...Array(5)].map((_, index) => (
                <div key={index} className="animate-pulse">
                  <div className="flex items-start space-x-3 p-3 sm:p-4 border rounded-lg">
                    <div className="w-6 h-6 bg-gray-200 rounded-full flex-shrink-0"></div>
                    <div className="flex-1 space-y-2">
                      <div className="h-5 bg-gray-200 rounded w-3/4"></div>
                      <div className="space-y-1">
                        <div className="h-4 bg-gray-100 rounded w-full"></div>
                        <div className="h-4 bg-gray-100 rounded w-5/6"></div>
                        <div className="h-4 bg-gray-100 rounded w-4/6"></div>
                      </div>
                    </div>
                    <div className="w-5 h-5 bg-gray-200 rounded flex-shrink-0"></div>
                  </div>
                </div>
              ))}
              <div className="w-full mt-4 h-10 bg-gray-200 rounded-[32px] animate-pulse"></div>
            </div>
          )}

          {/* Outlines content */}
          {outlines && outlines.length > 0 && (
            <div className="border p-2 sm:p-4 md:p-6 rounded-lg">
              <DndContext
                sensors={sensors}
                collisionDetection={closestCenter}
                onDragEnd={handleDragEnd}
              >
                <SortableContext
                  items={outlines?.map((item, index) => ({ id: item.title || `slide-${index}` })) || []}
                  strategy={verticalListSortingStrategy}
                >
                  {outlines?.map((item, index) => (
                    <OutlineItem
                      key={item.title || `slide-${index}`}
                      index={index + 1}
                      slideOutline={item}
                      isStreaming={isStreaming}
                    />
                  ))}
                </SortableContext>
              </DndContext>

              <Button
                variant="outline"
                onClick={handleAddSlide}
                disabled={isLoading || isStreaming}
                className="w-full mt-4 text-[#9034EA] border-[#9034EA] rounded-[32px] hover:bg-[#9034EA]/10"
              >
                + Add Slide
              </Button>
            </div>
          )}

          {/* Empty state */}
          {!isLoading && outlines && outlines.length === 0 && (
            <div className="text-center py-8">
              <p className="text-gray-600 mb-4">No outlines available</p>
              <Button
                variant="outline"
                onClick={handleAddSlide}
                className="text-[#9034EA] border-[#9034EA] rounded-[32px]"
              >
                + Add First Slide
              </Button>
            </div>
          )}
        </div>

        {/* Generate button */}
        {!isStreaming && <Button
          disabled={loadingState.isLoading || isLoading || isStreaming || !outlines || outlines.length === 0}
          onClick={handleSubmit}
          className="bg-[#5146E5] w-full rounded-[32px] text-base sm:text-lg py-4 sm:py-6 transition-all duration-300 font-roboto font-semibold hover:bg-[#5146E5]/80 text-white mt-4 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <svg
            className="mr-2"
            width="24"
            height="24"
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 25 25"
            fill="none"
          >
            <g clipPath="url(#clip0_1960_939)">
              <path
                d="M21.217 9.57008L21.463 9.00408C21.8955 8.0028 22.6876 7.2 23.683 6.75408L24.442 6.41508C24.5341 6.37272 24.6121 6.30485 24.6668 6.21951C24.7214 6.13417 24.7505 6.03494 24.7505 5.93358C24.7505 5.83222 24.7214 5.73299 24.6668 5.64765C24.6121 5.56231 24.5341 5.49444 24.442 5.45208L23.725 5.13308C22.7046 4.67446 21.8989 3.84196 21.474 2.80708L21.221 2.19608C21.1838 2.10144 21.119 2.02018 21.035 1.96291C20.951 1.90563 20.8517 1.875 20.75 1.875C20.6483 1.875 20.549 1.90563 20.465 1.96291C20.381 2.02018 20.3162 2.10144 20.279 2.19608L20.026 2.80608C19.6015 3.84116 18.7962 4.67401 17.776 5.13308L17.058 5.45308C16.9662 5.49556 16.8885 5.56342 16.834 5.64865C16.7795 5.73389 16.7506 5.83293 16.7506 5.93408C16.7506 6.03523 16.7795 6.13428 16.834 6.21951C16.8885 6.30474 16.9662 6.3726 17.058 6.41508L17.818 6.75308C18.8132 7.19945 19.6049 8.00261 20.037 9.00408L20.283 9.57008C20.463 9.98408 21.036 9.98408 21.217 9.57008ZM6.55 16.8761H8.704L9.304 15.3761H12.196L12.796 16.8761H14.95L11.75 8.87608H9.75L6.55 16.8761ZM10.75 11.7611L11.396 13.3761H10.104L10.75 11.7611ZM15.75 16.8761V8.87608H17.75V16.8761H15.75ZM3.75 3.87608C3.48478 3.87608 3.23043 3.98144 3.04289 4.16897C2.85536 4.35651 2.75 4.61086 2.75 4.87608V20.8761C2.75 21.1413 2.85536 21.3957 3.04289 21.5832C3.23043 21.7707 3.48478 21.8761 3.75 21.8761H21.75C22.0152 21.8761 22.2696 21.7707 22.4571 21.5832C22.6446 21.3957 22.75 21.1413 22.75 20.8761V11.8761H20.75V19.8761H4.75V5.87608H14.75V3.87608H3.75Z"
                fill="white"
              />
            </g>
            <defs>
              <clipPath id="clip0_1960_939">
                <rect
                  width="24"
                  height="24"
                  fill="white"
                  transform="translate(0.75 0.876953)"
                />
              </clipPath>
            </defs>
          </svg>
          {loadingState.isLoading
            ? loadingState.message
            : isLoading || isStreaming
              ? "Loading..."
              : "Generate Presentation"}
        </Button>}
      </div>
    </Wrapper>
  );
};

export default OutlinePage;