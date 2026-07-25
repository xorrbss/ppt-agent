"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type Konva from "konva";

import {
  fitTemplateV2Viewport,
  preserveTemplateV2ViewportOnResize,
  zoomTemplateV2Viewport,
  type ViewportTransform,
} from "@/lib/template-v2-konva";

const ZOOM_STEP = 1.2;
const WHEEL_STEP = 1.08;

// Pan/zoom state for the studio stage. It is separate from element interaction:
// the canvas owns selection and geometry, this owns how the slide is framed.
export function useTemplateV2Viewport() {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const observedDimensionsRef = useRef<{
    width: number;
    height: number;
  } | null>(null);
  const panRef = useRef<{
    pointer: { x: number; y: number };
    viewport: ViewportTransform;
  } | null>(null);
  const [panArmed, setPanArmed] = useState(false);
  const [dimensions, setDimensions] = useState({ width: 960, height: 600 });
  const [viewport, setViewport] = useState<ViewportTransform>(() =>
    fitTemplateV2Viewport(960, 600)
  );

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const observer = new ResizeObserver(([entry]) => {
      const width = Math.max(320, Math.floor(entry.contentRect.width));
      const height = Math.max(360, Math.floor(entry.contentRect.height));
      const next = { width, height };
      const previous = observedDimensionsRef.current;
      if (previous?.width === width && previous.height === height) return;
      observedDimensionsRef.current = next;
      setDimensions(next);
      setViewport((current) =>
        previous
          ? preserveTemplateV2ViewportOnResize(current, previous, next)
          : fitTemplateV2Viewport(width, height)
      );
    });
    observer.observe(container);
    return () => observer.disconnect();
  }, []);

  const zoomBy = useCallback(
    (factor: number) =>
      setViewport((current) =>
        zoomTemplateV2Viewport(
          current,
          { x: dimensions.width / 2, y: dimensions.height / 2 },
          current.scale * factor
        )
      ),
    [dimensions.height, dimensions.width]
  );

  const fit = useCallback(
    () => setViewport(fitTemplateV2Viewport(dimensions.width, dimensions.height)),
    [dimensions.height, dimensions.width]
  );

  // Held Space or the middle button starts a pan; the caller keeps its own
  // mousedown behaviour when this returns false.
  const beginPan = useCallback(
    (event: Konva.KonvaEventObject<MouseEvent>): boolean => {
      const pointer = event.target.getStage()?.getPointerPosition();
      if (!pointer || (!panArmed && event.evt.button !== 1)) return false;
      event.evt.preventDefault();
      panRef.current = { pointer, viewport };
      return true;
    },
    [panArmed, viewport]
  );

  const stageProps = {
    onWheel: (event: Konva.KonvaEventObject<WheelEvent>) => {
      event.evt.preventDefault();
      const pointer = event.target.getStage()?.getPointerPosition();
      if (!pointer) return;
      const direction = event.evt.deltaY > 0 ? 1 / WHEEL_STEP : WHEEL_STEP;
      setViewport((current) =>
        zoomTemplateV2Viewport(current, pointer, current.scale * direction)
      );
    },
    onMouseMove: (event: Konva.KonvaEventObject<MouseEvent>) => {
      const pan = panRef.current;
      const pointer = event.target.getStage()?.getPointerPosition();
      if (!pan || !pointer) return;
      setViewport({
        ...pan.viewport,
        x: pan.viewport.x + pointer.x - pan.pointer.x,
        y: pan.viewport.y + pointer.y - pan.pointer.y,
      });
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

export function TemplateV2CanvasZoomControls({
  scale,
  zoomBy,
  fit,
}: {
  scale: number;
  zoomBy: (factor: number) => void;
  fit: () => void;
}) {
  return (
    <div className="absolute bottom-3 right-3 flex items-center gap-1 rounded-lg bg-slate-950/90 p-1 text-xs text-slate-200">
      <button
        type="button"
        aria-label="Zoom out"
        className="rounded px-2 py-1 hover:bg-slate-700"
        onClick={() => zoomBy(1 / ZOOM_STEP)}
      >
        −
      </button>
      <span aria-live="polite" className="min-w-12 text-center">
        {Math.round(scale * 100)}%
      </span>
      <button
        type="button"
        aria-label="Zoom in"
        className="rounded px-2 py-1 hover:bg-slate-700"
        onClick={() => zoomBy(ZOOM_STEP)}
      >
        +
      </button>
      <button
        type="button"
        aria-label="Fit slide to view"
        className="rounded px-2 py-1 hover:bg-slate-700"
        onClick={fit}
      >
        Fit
      </button>
    </div>
  );
}
