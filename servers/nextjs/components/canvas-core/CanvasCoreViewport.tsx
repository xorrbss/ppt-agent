"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type Konva from "konva";

export interface CanvasCoreViewportTransform {
  x: number;
  y: number;
  scale: number;
}

export interface CanvasCoreViewportOptions {
  slideWidth: number;
  slideHeight: number;
  initialWidth?: number;
  initialHeight?: number;
  minContainerWidth?: number;
  minContainerHeight?: number;
  minScale?: number;
  maxScale?: number;
  padding?: number;
  wheelStep?: number;
}

const DEFAULT_WIDTH = 960;
const DEFAULT_HEIGHT = 600;

export function fitCanvasCoreViewport(
  dimensions: { width: number; height: number },
  slide: { width: number; height: number },
  padding = 24,
): CanvasCoreViewportTransform {
  const usableWidth = Math.max(1, dimensions.width - padding * 2);
  const usableHeight = Math.max(1, dimensions.height - padding * 2);
  const scale = Math.min(
    1,
    usableWidth / slide.width,
    usableHeight / slide.height,
  );
  return {
    scale,
    x: (dimensions.width - slide.width * scale) / 2,
    y: (dimensions.height - slide.height * scale) / 2,
  };
}

export function preserveCanvasCoreViewportOnResize(
  viewport: CanvasCoreViewportTransform,
  previous: { width: number; height: number },
  next: { width: number; height: number },
  slide: { width: number; height: number },
  padding = 24,
): CanvasCoreViewportTransform {
  if (
    viewport.scale <= 0 ||
    previous.width <= 0 ||
    previous.height <= 0 ||
    next.width <= 0 ||
    next.height <= 0
  ) {
    return fitCanvasCoreViewport(next, slide, padding);
  }
  const logicalCenter = {
    x: (previous.width / 2 - viewport.x) / viewport.scale,
    y: (previous.height / 2 - viewport.y) / viewport.scale,
  };
  return constrainCanvasCoreViewport({
    scale: viewport.scale,
    x: next.width / 2 - logicalCenter.x * viewport.scale,
    y: next.height / 2 - logicalCenter.y * viewport.scale,
  }, next, slide, padding);
}

export function zoomCanvasCoreViewport(
  viewport: CanvasCoreViewportTransform,
  pointer: { x: number; y: number },
  requestedScale: number,
  minScale = 0.25,
  maxScale = 4,
): CanvasCoreViewportTransform {
  const scale = Math.min(maxScale, Math.max(minScale, requestedScale));
  const logical = {
    x: (pointer.x - viewport.x) / viewport.scale,
    y: (pointer.y - viewport.y) / viewport.scale,
  };
  return {
    scale,
    x: pointer.x - logical.x * scale,
    y: pointer.y - logical.y * scale,
  };
}

export function constrainCanvasCoreViewport(
  viewport: CanvasCoreViewportTransform,
  dimensions: { width: number; height: number },
  slide: { width: number; height: number },
  padding = 24,
): CanvasCoreViewportTransform {
  const scaledWidth = slide.width * viewport.scale;
  const scaledHeight = slide.height * viewport.scale;
  const usableWidth = Math.max(1, dimensions.width - padding * 2);
  const usableHeight = Math.max(1, dimensions.height - padding * 2);
  const constrainAxis = (
    position: number,
    viewportSize: number,
    scaledSize: number,
    usableSize: number,
  ) => {
    if (scaledSize <= usableSize) return (viewportSize - scaledSize) / 2;
    const minimum = viewportSize - padding - scaledSize;
    return Math.min(padding, Math.max(minimum, position));
  };
  return {
    ...viewport,
    x: constrainAxis(viewport.x, dimensions.width, scaledWidth, usableWidth),
    y: constrainAxis(viewport.y, dimensions.height, scaledHeight, usableHeight),
  };
}

/**
 * Shared viewport controller for Konva-backed editors. Element selection and
 * contract commits stay in the adapter/editor; this hook only frames the
 * canonical slide and keeps pan/zoom transient.
 */
export function useCanvasCoreViewport({
  slideWidth,
  slideHeight,
  initialWidth = DEFAULT_WIDTH,
  initialHeight = DEFAULT_HEIGHT,
  minContainerWidth = 320,
  minContainerHeight = 360,
  minScale = 0.25,
  maxScale = 4,
  padding = 24,
  wheelStep = 1.08,
}: CanvasCoreViewportOptions) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const observedDimensionsRef = useRef<{ width: number; height: number } | null>(null);
  const panRef = useRef<{
    pointer: { x: number; y: number };
    viewport: CanvasCoreViewportTransform;
  } | null>(null);
  const [panArmed, setPanArmed] = useState(false);
  const [dimensions, setDimensions] = useState({
    width: initialWidth,
    height: initialHeight,
  });
  const slide = { width: slideWidth, height: slideHeight };
  const [viewport, setViewport] = useState<CanvasCoreViewportTransform>(() =>
    fitCanvasCoreViewport(
      { width: initialWidth, height: initialHeight },
      slide,
      padding,
    ),
  );

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const observer = new ResizeObserver(([entry]) => {
      const width = Math.max(minContainerWidth, Math.floor(entry.contentRect.width));
      const height = Math.max(minContainerHeight, Math.floor(entry.contentRect.height));
      const next = { width, height };
      const previous = observedDimensionsRef.current;
      if (previous?.width === width && previous.height === height) return;
      observedDimensionsRef.current = next;
      setDimensions(next);
      setViewport((current) =>
        previous
          ? preserveCanvasCoreViewportOnResize(current, previous, next, slide, padding)
          : fitCanvasCoreViewport(next, slide, padding),
      );
    });
    observer.observe(container);
    return () => observer.disconnect();
  }, [minContainerHeight, minContainerWidth, padding, slideHeight, slideWidth]);

  const zoomBy = useCallback(
    (factor: number) =>
      setViewport((current) =>
        constrainCanvasCoreViewport(
          zoomCanvasCoreViewport(
            current,
            { x: dimensions.width / 2, y: dimensions.height / 2 },
            current.scale * factor,
            minScale,
            maxScale,
          ),
          dimensions,
          slide,
          padding,
        ),
      ),
    [dimensions, maxScale, minScale, padding, slideHeight, slideWidth],
  );
  const fit = useCallback(
    () => setViewport(fitCanvasCoreViewport(dimensions, slide, padding)),
    [dimensions, padding, slideHeight, slideWidth],
  );
  const beginPan = useCallback(
    (event: Konva.KonvaEventObject<MouseEvent>): boolean => {
      const pointer = event.target.getStage()?.getPointerPosition();
      if (!pointer || (!panArmed && event.evt.button !== 1)) return false;
      event.evt.preventDefault();
      panRef.current = { pointer, viewport };
      return true;
    },
    [panArmed, viewport],
  );
  const stageProps = {
    onWheel: (event: Konva.KonvaEventObject<WheelEvent>) => {
      event.evt.preventDefault();
      event.evt.stopPropagation();
      const pointer = event.target.getStage()?.getPointerPosition();
      if (!pointer) return;
      const factor = event.evt.deltaY > 0 ? 1 / wheelStep : wheelStep;
      // Keep rapid wheel events cumulative even when React batches updates.
      setViewport((current) =>
        constrainCanvasCoreViewport(
          zoomCanvasCoreViewport(
            current,
            pointer,
            current.scale * factor,
            minScale,
            maxScale,
          ),
          dimensions,
          slide,
          padding,
        ),
      );
    },
    onMouseMove: (event: Konva.KonvaEventObject<MouseEvent>) => {
      const pan = panRef.current;
      const pointer = event.target.getStage()?.getPointerPosition();
      if (!pan || !pointer) return;
      setViewport(constrainCanvasCoreViewport({
        ...pan.viewport,
        x: pan.viewport.x + pointer.x - pan.pointer.x,
        y: pan.viewport.y + pointer.y - pan.pointer.y,
      }, dimensions, slide, padding));
    },
    onMouseUp: () => {
      panRef.current = null;
    },
    onMouseLeave: () => {
      panRef.current = null;
    },
  };

  return {
    containerRef,
    dimensions,
    viewport,
    armPan: setPanArmed,
    beginPan,
    stageProps,
    zoomBy,
    fit,
  };
}

export function CanvasCoreZoomControls({
  scale,
  zoomBy,
  fit,
  zoomOutLabel,
  zoomInLabel,
  fitLabel,
  fitText,
}: {
  scale: number;
  zoomBy: (factor: number) => void;
  fit: () => void;
  zoomOutLabel: string;
  zoomInLabel: string;
  fitLabel: string;
  fitText: string;
}) {
  return (
    <div className="absolute bottom-3 right-3 z-20 flex items-center gap-1 rounded-lg bg-slate-950/90 p-1 text-xs text-slate-200">
      <button type="button" aria-label={zoomOutLabel} className="rounded px-2 py-1 hover:bg-slate-700" onClick={() => zoomBy(1 / 1.2)}>
        −
      </button>
      <span aria-live="polite" className="min-w-12 text-center">
        {Math.round(scale * 100)}%
      </span>
      <button type="button" aria-label={zoomInLabel} className="rounded px-2 py-1 hover:bg-slate-700" onClick={() => zoomBy(1.2)}>
        +
      </button>
      <button type="button" aria-label={fitLabel} className="rounded px-2 py-1 hover:bg-slate-700" onClick={fit}>
        {fitText}
      </button>
    </div>
  );
}
