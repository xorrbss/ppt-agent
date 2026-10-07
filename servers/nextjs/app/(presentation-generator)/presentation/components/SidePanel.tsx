"use client";
import React, { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { Plus } from "lucide-react";
import { useDispatch, useSelector, useStore } from "react-redux";
import { v4 as uuidv4 } from "uuid";
import { RootState } from "@/store/store";
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import {
  addNewSlide,
  setPresentationData,
} from "@/store/slices/presentationGeneration";
import { SortableSlide } from "./SortableSlide";
import type { Slide } from "../../types/slide";
import { Separator } from "@/components/ui/separator";
import { notify } from "@/components/ui/sonner";
import { PresentationGenerationApi } from "../../services/api/presentation-generation";
import { ApiError } from "../../services/api/api-error-handler";
import { usePathname } from "next/navigation";
import { trackEvent, MixpanelEvent } from "@/utils/mixpanel";
import { SlideThumbnailCard } from "./SlideThumbnailCard";
import {
  BLANK_SLIDE_LAYOUT_GROUP,
  BLANK_SLIDE_LAYOUT_ID,
  createBlankPresentationSlide,
  isTemplateFreePresentation,
} from "../../_shared/blank-slide";
import { MAX_NUMBER_OF_SLIDES } from "@/utils/presentationLimits";
import { getThumbnailScrollTarget } from "../utils/slideScrollSync";
import { flaggedSlideKey } from "../utils/flaggedSlides";

interface SidePanelProps {
  selectedSlide: number;
  onSlideClick: (index: number) => void;
  presentationId: string;
  loading: boolean;
  mutationsEnabled?: boolean;
  displayMode?: "rail" | "drawer";
}

const SidePanel = ({
  selectedSlide,
  onSlideClick,
  presentationId,
  loading,
  mutationsEnabled = true,
  displayMode = "rail",
}: SidePanelProps) => {
  const t = useTranslations("slideNavigation");
  const pathname = usePathname();
  const thumbnailScrollRef = useRef<HTMLDivElement | null>(null);
  const store = useStore<RootState>();
  const orderSaveBusy = useRef(false);
  const orderOperation = useRef<{
    presentationId: string; operationId: string; slide_ids: string[]; expected_revision: number;
  } | null>(null);
  const [orderSaveState, setOrderSaveState] = useState<"idle" | "saving" | "unconfirmed">("idle");

  const { presentationData, isStreaming, flaggedSlides } = useSelector(
    (state: RootState) => state.presentationGeneration,
  );

  const dispatch = useDispatch();
  const flagFor = (slide: Slide, position: number) =>
    flaggedSlides[flaggedSlideKey(slide, position)] ?? null;

  const lastSlideIndex = presentationData?.slides?.length
    ? presentationData.slides.length - 1
    : 0;
  const isTemplateFree = isTemplateFreePresentation(presentationData);
  const canManageSlides = mutationsEnabled && orderSaveState === "idle";
  const previewScale = displayMode === "drawer" ? 0.18 : undefined;

  useEffect(() => {
    const container = thumbnailScrollRef.current;
    if (!container) return;
    const thumbnail = container.querySelector<HTMLElement>(
      `[data-slide-thumbnail-index="${selectedSlide}"]`,
    );
    if (!thumbnail) return;

    const containerRect = container.getBoundingClientRect();
    const thumbnailRect = thumbnail.getBoundingClientRect();
    const targetTop =
      thumbnailRect.top - containerRect.top + container.scrollTop;
    const target = getThumbnailScrollTarget({
      scrollTop: container.scrollTop,
      clientHeight: container.clientHeight,
      targetTop,
      targetBottom: targetTop + thumbnailRect.height,
    });
    if (target === null) return;
    container.scrollTo({ top: target, behavior: "smooth" });
  }, [presentationData?.slides?.length, selectedSlide]);


  const handleAddSlideClick = () => {
    if (!canManageSlides || orderSaveBusy.current || !presentationData?.slides?.length || isStreaming) {
      return;
    }

    if (presentationData.slides.length >= MAX_NUMBER_OF_SLIDES) {
      notify.warning(
        t("limitTitle"),
        t("limitDescription", { limit: MAX_NUMBER_OF_SLIDES }),
      );
      return;
    }

    if (isTemplateFree) {
      const slideId = uuidv4();
      const newIndex = lastSlideIndex + 1;
      const blankSlide = createBlankPresentationSlide({
        id: slideId,
        index: newIndex,
        presentationId,
        templateId: BLANK_SLIDE_LAYOUT_GROUP,
      });

      dispatch(
        addNewSlide({
          slideData: blankSlide,
          index: lastSlideIndex,
        }),
      );
      trackEvent(MixpanelEvent.Presentation_Slide_Added, {
        pathname,
        presentation_id: presentationId,
        inserted_after_index: lastSlideIndex,
        template_id: BLANK_SLIDE_LAYOUT_GROUP,
        layout_id: BLANK_SLIDE_LAYOUT_ID,
        source: "blank_side_panel",
      });
      onSlideClick(newIndex);
      return;
    }

  };

  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: {
        distance: 8, // Start drag after moving 8px
      },
    }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    }),
  );

  const completeOrderSave = (result: { revision: number; presentation_id: string }) => {
    // Read at completion: text edits and other UI updates made while waiting
    // must not be replaced with the snapshot from before the reorder.
    const latest = store.getState().presentationGeneration.presentationData;
    if (latest?.id === result.presentation_id) {
      dispatch(setPresentationData({
        ...latest, revision: Math.max(latest.revision ?? 0, result.revision),
      }));
    }
    orderOperation.current = null;
    orderSaveBusy.current = false;
    setOrderSaveState("idle");
  };

  const handleOrderSaveError = (error: unknown) => {
    orderSaveBusy.current = false;
    if (orderOperation.current && !(error instanceof ApiError && [409, 422].includes(error.status))) {
      setOrderSaveState("unconfirmed");
      return;
    }
    orderOperation.current = null;
    setOrderSaveState("idle");
    if (error instanceof ApiError && error.status === 409) {
      notify.error(t("reorderSaveFailed"), t("reorderConflictReload"));
      window.location.reload();
      return;
    }
    notify.error(t("reorderSaveFailed"), t("reorderNetworkRetry"));
  };

  const confirmPendingOrder = () => {
    const operation = orderOperation.current;
    if (!operation || orderSaveBusy.current) return;
    orderSaveBusy.current = true;
    setOrderSaveState("saving");
    void PresentationGenerationApi.waitForSlideOrderOperation(
      operation.presentationId, operation, operation.operationId,
    ).then(completeOrderSave).catch(handleOrderSaveError);
  };

  const persistSlideOrder = (updatedSlides: Slide[], expectedRevision?: number) => {
    if (typeof expectedRevision !== "number" || orderSaveBusy.current || orderOperation.current) return;
    const slideIds: string[] = [];
    for (const slide of updatedSlides) {
      if (slide.id === null) return;
      slideIds.push(slide.id);
    }
    orderSaveBusy.current = true;
    setOrderSaveState("saving");
    void PresentationGenerationApi.reorderSlides(presentationId, {
      slide_ids: slideIds,
      expected_revision: expectedRevision,
    }, { onPending: (operationId) => {
      orderOperation.current = { presentationId, operationId, slide_ids: slideIds, expected_revision: expectedRevision };
    } }).then(completeOrderSave).catch(handleOrderSaveError);
  };

  const handleDragEnd = (event: DragEndEvent) => {
    if (!canManageSlides || orderSaveBusy.current || orderOperation.current) return;
    const { active, over } = event;

    if (!active || !over || !presentationData?.slides) return;

    if (active.id !== over.id) {
      const selectedSlideId = presentationData.slides[selectedSlide]?.id;
      // Find the indices of the dragged and target items
      const oldIndex = presentationData?.slides.findIndex(
        (item: Slide) => item.id === active.id,
      );
      const newIndex = presentationData?.slides.findIndex(
        (item: Slide) => item.id === over.id,
      );

      // Reorder the array
      const reorderedArray = arrayMove<Slide>(
        presentationData?.slides,
        oldIndex,
        newIndex,
      );

      // Update indices of all slides
      const updatedArray = reorderedArray.map((slide: Slide, index: number) => ({
        ...slide,
        index: index,
      }));

      // Update the store with new order and indices
      dispatch(
        setPresentationData({ ...presentationData, slides: updatedArray }),
      );
      if (selectedSlideId) {
        const nextSelectedIndex = updatedArray.findIndex(
          (slide) => slide.id === selectedSlideId,
        );
        if (nextSelectedIndex >= 0 && nextSelectedIndex !== selectedSlide) {
          requestAnimationFrame(() => onSlideClick(nextSelectedIndex));
        }
      }
      trackEvent(MixpanelEvent.Presentation_Slides_Reordered, {
        pathname,
        presentation_id: presentationId,
        from_index: oldIndex,
        to_index: newIndex,
        slide_count: updatedArray.length,
      });
      persistSlideOrder(updatedArray, presentationData.revision);
    }
  };

  // Loading shimmer component
  if (
    !presentationData ||
    loading ||
    !presentationData?.slides ||
    presentationData?.slides.length === 0
  ) {
    return null;
  }

  return (
    <div
      data-thumbnail-display={displayMode}
      className={displayMode === "drawer" ? "h-full w-full px-2" : "h-full w-[120px] px-4"}
    >
      <div
        className={`
          relative  h-full z-50 xl:z-auto 
          transition-all duration-300 ease-in-out
        `}
      >
        <div className="w-full h-full hide-scrollbar overflow-hidden slide-theme flex flex-col">
          {orderSaveState !== "idle" && (
            <div role="status" className="text-[11px] py-2 text-center">
              {orderSaveState === "saving" ? t("reorderSaving") : t("reorderUnconfirmed")}
              {orderSaveState === "unconfirmed" && (
                <button type="button" onClick={confirmPendingOrder} className="block mx-auto underline">
                  {t("reorderCheckAgain")}
                </button>
              )}
            </div>
          )}
          <DndContext
            sensors={sensors}
            collisionDetection={closestCenter}
            onDragEnd={handleDragEnd}
          >
            <div
              ref={thumbnailScrollRef}
              data-testid="slide-thumbnail-scroll"
              role="navigation"
              aria-label={t("thumbnails")}
              className="overflow-y-auto w-full hide-scrollbar min-h-0 flex-1 space-y-3.5"
            >
              {isStreaming || !canManageSlides ? (
                presentationData &&
                presentationData?.slides.map((slide: Slide, index: number) => (
                  <SlideThumbnailCard
                    key={slide.id || String(index)}
                    slide={slide}
                    index={index}
                    selected={selectedSlide === index}
                    previewScale={previewScale}
                    flag={flagFor(slide, index)}
                    onClick={() => onSlideClick(slide.index ?? index)}
                  />
                ))
              ) : (
                <SortableContext
                  items={
                    presentationData?.slides.map(
                      (slide: Slide) => slide.id || `${slide.index}`,
                    ) || []
                  }
                  strategy={verticalListSortingStrategy}
                >
                  {presentationData &&
                    presentationData?.slides.map(
                      (slide: Slide, index: number) => (
                        <SortableSlide
                          key={slide.id || String(index)}
                          slide={slide}
                          index={index}
                          selectedSlide={selectedSlide}
                          onSlideClick={onSlideClick}
                          previewScale={previewScale}
                          flag={flagFor(slide, index)}
                        />
                      ),
                    )}
                </SortableContext>
              )}
            </div>
          </DndContext>
          {mutationsEnabled && isTemplateFree && (
            <Separator orientation="horizontal" className=" " />
          )}

          {mutationsEnabled && (
            <button
              type="button"
              data-testid="side-panel-add-slide"
              onClick={handleAddSlideClick}
              disabled={!canManageSlides}
              className="py-4 gap-2 flex flex-col duration-300 items-center justify-center rounded-lg cursor-pointer mx-auto"
            >
              <Plus className="w-3.5 h-3.5" />
              <span className="text-[11px] font-normal text-[#000000]">
                {t("addSlide")}
              </span>
            </button>
          )}
        </div>
      </div>
    </div>
  );
};

export default SidePanel;
