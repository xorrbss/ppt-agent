import React, { forwardRef } from "react";
import { GripVertical } from "lucide-react";
import type { Slide } from "../../types/slide";
import { V1ContentRender } from "../../components/V1ContentRender";

interface SlideThumbnailCardProps extends React.HTMLAttributes<HTMLDivElement> {
  slide: Slide;
  index: number;
  selected: boolean;
  previewScale?: number;
  dragHandleProps?: React.ComponentPropsWithRef<"button">;
}

const SCALE = 0.061;

export const SlideThumbnailCard = forwardRef<
  HTMLDivElement,
  SlideThumbnailCardProps
>(({
  slide,
  index,
  selected,
  previewScale = SCALE,
  dragHandleProps,
  className = "",
  style,
  onClick,
  onKeyDown,
  ...props
}, ref) => {
  const handleDragButtonClick = (event: React.MouseEvent<HTMLButtonElement>) => {
    event.stopPropagation();
    dragHandleProps?.onClick?.(event);
  };

  return (
    <div
      ref={ref}
      data-slide-thumbnail-index={index}
      style={{
        backgroundColor: "var(--card-color, #ffffff)",
        borderColor: selected ? "#5141e5" : "var(--stroke, #e5e7eb)",
        ...style,
      }}
      className={`border relative rounded-[12px] transition-all duration-200 ${
        selected ? "border-[#BDB4FE]" : "border-[#EDEEEF]"
      } ${className}`}
      {...props}
    >
      <div
        role="button"
        tabIndex={0}
        aria-label={`Slide ${index + 1}`}
        aria-current={selected ? "page" : undefined}
        data-testid={`slide-thumbnail-select-${index}`}
        onClick={onClick}
        onKeyDown={(event) => {
          onKeyDown?.(event);
          if (event.defaultPrevented || (event.key !== "Enter" && event.key !== " ")) {
            return;
          }
          event.preventDefault();
          onClick?.(event as unknown as React.MouseEvent<HTMLDivElement>);
        }}
        className="relative cursor-pointer overflow-hidden rounded-[11px] p-1.5 outline-none focus-visible:ring-2 focus-visible:ring-[#6D28D9] focus-visible:ring-inset"
      >
        <p className="pointer-events-none absolute -left-1 top-1/2 z-50 flex h-[18px] min-w-[18px] -translate-y-1/2 items-center justify-center rounded-full border border-[#EDEEEF] bg-white px-1 text-[10px] font-medium text-[#191919] shadow-sm">
          {index + 1}
        </p>

        <div
          aria-hidden="true"
          inert
          className="relative pointer-events-none"
          style={{ height: `${720 * previewScale}px`, overflow: "hidden" }}
        >
          <div
            className="absolute top-0 left-0 rounded-[10px] overflow-hidden pointer-events-none"
            style={{
              width: 1280,
              height: 720,
              transformOrigin: "top left",
              transform: `scale(${previewScale})`,
            }}
          >
            <V1ContentRender slide={slide} isEditMode={false} />
          </div>
        </div>
      </div>

      {dragHandleProps && (
        <button
          {...dragHandleProps}
          type="button"
          aria-label={dragHandleProps["aria-label"] ?? `Reorder slide ${index + 1}`}
          onClick={handleDragButtonClick}
          className={`absolute right-1 top-1 z-[60] flex h-8 w-8 touch-none items-center justify-center rounded-md bg-white/95 text-slate-600 shadow-sm outline-none hover:bg-white focus-visible:ring-2 focus-visible:ring-[#6D28D9] ${dragHandleProps.className ?? ""}`}
        >
          <GripVertical aria-hidden="true" className="h-4 w-4" />
        </button>
      )}
    </div>
  );
});

SlideThumbnailCard.displayName = "SlideThumbnailCard";
