"use client";
import React, { useState, useEffect, useMemo } from "react";
import { LayoutList, ListTree, PanelRightOpen, X } from "lucide-react";
import ToolTip from "@/components/ToolTip";
import { Button } from "@/components/ui/button";
import { useDispatch, useSelector } from "react-redux";
import { RootState } from "@/store/store";
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
import { setPresentationData } from "@/store/slices/presentationGeneration";
import { SortableSlide } from "./SortableSlide";
import { SortableListItem } from "./SortableListItem";
import useLayoutCache from "../../hooks/useLayoutCache";

interface SidePanelProps {
  selectedSlide: number;
  onSlideClick: (index: number) => void;
  isMobilePanelOpen: boolean;
  setIsMobilePanelOpen: (value: boolean) => void;
  loading: boolean;
}

const SidePanel = ({
  selectedSlide,
  onSlideClick,
  isMobilePanelOpen,
  setIsMobilePanelOpen,
  loading,
}: SidePanelProps) => {
  const [isOpen, setIsOpen] = useState(true);
  const [active, setActive] = useState<"list" | "grid">("grid");

  const { presentationData, isStreaming } = useSelector(
    (state: RootState) => state.presentationGeneration
  );
  const { currentTheme, currentColors } = useSelector(
    (state: RootState) => state.theme
  );
  const dispatch = useDispatch();
  const { getLayout } = useLayoutCache();

  // Memoized slide renderer using layout cache
  const renderSlideContent = useMemo(() => {
    return (slide: any) => {
      const Layout = getLayout(slide.layout);
      if (!Layout) {
        return <div>Layout not found</div>;
      }
      return <Layout data={slide.content} />;
    };
  }, [getLayout]);

  useEffect(() => {
    if (window.innerWidth < 768) {
      setIsOpen(isMobilePanelOpen);
    }
  }, [isMobilePanelOpen]);

  const sensors = useSensors(
    useSensor(PointerSensor),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    })
  );

  const handleClose = () => {
    setIsOpen(false);
    if (window.innerWidth < 768) {
      setIsMobilePanelOpen(false);
    }
  };

  const handleDragEnd = (event: any) => {
    const { active, over } = event;

    if (!active || !over || !presentationData?.slides) return;

    if (active.id !== over.id) {
      // Find the indices of the dragged and target items
      const oldIndex = presentationData?.slides.findIndex(
        (item) => item.id === active.id
      );
      const newIndex = presentationData?.slides.findIndex(
        (item) => item.id === over.id
      );

      // Reorder the array
      const reorderedArray = arrayMove(
        presentationData?.slides,
        oldIndex,
        newIndex
      );

      // Update indices of all slides
      const updatedArray = reorderedArray.map((slide, index) => ({
        ...slide,
        index: index,
      }));

      // Update the store with new order and indices
      dispatch(
        setPresentationData({ ...presentationData, slides: updatedArray })
      );
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
    <>
      {/* Mobile Toggle Button */}
      <div className="md:hidden fixed top-20 left-4 z-40">
        <Button
          variant="outline"
          size="sm"
          onClick={() => setIsMobilePanelOpen(true)}
          className="bg-white shadow-md"
        >
          <PanelRightOpen className="w-4 h-4" />
        </Button>
      </div>

      {/* Backdrop for mobile */}
      {isMobilePanelOpen && (
        <div
          className="md:hidden fixed inset-0 bg-black/50 z-30"
          onClick={() => setIsMobilePanelOpen(false)}
        />
      )}

      <div
        className={`${isOpen ? "w-72" : "w-0"} ${isMobilePanelOpen ? "translate-x-0" : "-translate-x-full"
          } md:translate-x-0 fixed md:relative left-0 top-0 h-full bg-white border-r border-gray-200 transition-all duration-300 ease-in-out z-40 md:z-auto flex-shrink-0`}
        style={{
          background: currentColors.background,
        }}
      >
        <div className="h-full flex flex-col">
          {/* Header */}
          <div className="p-4 border-b border-color flex items-center justify-between">
            <h3 className="font-semibold slide-title">Slides</h3>
            <div className="flex items-center gap-2">
              <ToolTip content="List view">
                <Button
                  variant={active === "list" ? "default" : "ghost"}
                  size="sm"
                  onClick={() => setActive("list")}
                  className="p-2"
                >
                  <ListTree className="w-4 h-4" />
                </Button>
              </ToolTip>
              <ToolTip content="Grid view">
                <Button
                  variant={active === "grid" ? "default" : "ghost"}
                  size="sm"
                  onClick={() => setActive("grid")}
                  className="p-2"
                >
                  <LayoutList className="w-4 h-4" />
                </Button>
              </ToolTip>
              <Button
                variant="ghost"
                size="sm"
                onClick={handleClose}
                className="md:hidden p-2"
              >
                <X className="w-4 h-4" />
              </Button>
            </div>
          </div>

          <DndContext
            sensors={sensors}
            collisionDetection={closestCenter}
            onDragEnd={handleDragEnd}
          >
            {/* List Preview */}
            {active === "list" && (
              <div className="p-4 overflow-y-auto hide-scrollbar h-[calc(100%-100px)]">
                {isStreaming ? (
                  presentationData &&
                  presentationData?.slides.map((slide, index) => (
                    <div
                      key={`${index}-${slide.type}-${slide.id}`}
                      className={`p-3 cursor-pointer rounded-lg slide-box`}
                    >
                      <span className="font-medium slide-title">
                        Slide {index + 1}
                      </span>
                      <p className="text-sm slide-description">
                        {slide.content.title}
                      </p>
                    </div>
                  ))
                ) : (
                  <SortableContext
                    items={
                      presentationData?.slides.map((slide) => slide.id!) || []
                    }
                    strategy={verticalListSortingStrategy}
                  >
                    <div className="space-y-2" id={`slide-${selectedSlide}`}>
                      {presentationData &&
                        presentationData?.slides.map((slide, index) => (
                          <SortableListItem
                            key={`${slide.id}-${index}`}
                            slide={slide}
                            index={index}
                            selectedSlide={selectedSlide}
                            onSlideClick={onSlideClick}
                          />
                        ))}
                    </div>
                  </SortableContext>
                )}
              </div>
            )}

            {/* Grid Preview */}
            {active === "grid" && (
              <div className="p-4 overflow-y-auto hide-scrollbar h-[calc(100%-100px)] space-y-4">
                {isStreaming ? (
                  presentationData &&
                  presentationData?.slides.map((slide, index) => (
                    <div
                      key={`${slide.id}-${index}`}
                      onClick={() => onSlideClick(index)}
                      className={` cursor-pointer ring-2 p-1  rounded-md transition-all duration-200 ${selectedSlide === index ? ' ring-[#5141e5]' : 'ring-gray-200'
                        }`}
                    >
                      <div className=" bg-white  relative overflow-hidden aspect-video">
                        <div className="absolute bg-gray-100/5 z-40 top-0 left-0 w-full h-full" />
                        <div className="transform scale-[0.2] flex justify-center items-center origin-top-left  w-[500%] h-[500%]">
                          {renderSlideContent(slide)}
                        </div>
                      </div>
                    </div>
                  ))
                ) : (
                  <SortableContext
                    items={
                      presentationData?.slides.map((slide) => slide.id!) || []
                    }
                    strategy={verticalListSortingStrategy}
                  >
                    {presentationData &&
                      presentationData?.slides.map((slide, index) => (
                        <SortableSlide
                          key={`${slide.id}-${index}`}
                          slide={slide}
                          index={index}
                          selectedSlide={selectedSlide}
                          onSlideClick={onSlideClick}
                          renderSlideContent={renderSlideContent}
                        />
                      ))}
                  </SortableContext>
                )}
              </div>
            )}
          </DndContext>
        </div>
      </div>
    </>
  );
};

export default SidePanel;
