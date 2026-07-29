"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

import { resolveTemplateV2ToolbarRoute } from "@/lib/template-v2-toolbar";
import ChartToolbar from "./ChartToolbar";
import IconToolbar from "./IconToolbar";
import ImageToolbar from "./ImageToolbar";
import InfographicToolbar from "./InfographicToolbar";
import MultiSelectionToolbar from "./MultiSelectionToolbar";
import SelectionActions from "./SelectionActions";
import TableToolbar from "./TableToolbar";
import TextToolbar from "./TextToolbar";
import VectorToolbar from "./VectorToolbar";
import type { TemplateV2SelectionToolbarProps } from "./types";

export function TemplateV2SelectionToolbar({
  selectionCount,
  selectionPath = null,
  element,
  position,
  actions,
  fontFamilies,
  onPatch,
  onCropModeChange,
  onCropDraftChange,
  onReplaceImage,
  onChangeIcon,
}: TemplateV2SelectionToolbarProps) {
  const [mounted, setMounted] = useState(false);
  const route = resolveTemplateV2ToolbarRoute({
    selectionCount,
    element,
  });

  useEffect(() => {
    setMounted(true);
    return () => setMounted(false);
  }, []);

  if (
    !mounted ||
    !position?.visible ||
    route.kind === "none" ||
    route.kind === "unsupported"
  ) {
    return null;
  }

  const content =
    route.kind === "multi" ? (
      <MultiSelectionToolbar count={route.count} actions={actions} />
    ) : (
      <>
        {route.kind === "image" && (
          <ImageToolbar
            element={route.element}
            selectionPath={selectionPath}
            onPatch={onPatch}
            onCropModeChange={onCropModeChange}
            onCropDraftChange={onCropDraftChange}
            onReplaceImage={onReplaceImage}
          />
        )}
        {route.kind === "icon" && (
          <IconToolbar
            element={route.element}
            onPatch={onPatch}
            onChangeIcon={onChangeIcon}
          />
        )}
        {(route.kind === "text" || route.kind === "text-list") && (
          <TextToolbar
            element={route.element}
            fontFamilies={fontFamilies}
            onPatch={onPatch}
          />
        )}
        {route.kind === "vector" && (
          <VectorToolbar element={route.element} onPatch={onPatch} />
        )}
        {route.kind === "chart" && (
          <ChartToolbar element={route.element} onPatch={onPatch} />
        )}
        {route.kind === "table" && (
          <TableToolbar element={route.element} onPatch={onPatch} />
        )}
        {route.kind === "infographic" && (
          <InfographicToolbar element={route.element} onPatch={onPatch} />
        )}
        <SelectionActions actions={actions} />
      </>
    );

  return createPortal(
    <div
      role="toolbar"
      aria-label="Selection toolbar"
      data-testid="template-v2-selection-toolbar"
      data-template-v2-floating-toolbar="true"
      className="fixed z-[10000] flex max-w-[calc(100vw-16px)] items-center gap-2 overflow-x-auto rounded-lg border border-slate-600 bg-slate-950/95 p-2 text-slate-100 shadow-2xl backdrop-blur"
      style={{ left: position.left, top: position.top }}
      onMouseDown={(event) => event.stopPropagation()}
      onPointerDown={(event) => event.stopPropagation()}
      onClick={(event) => event.stopPropagation()}
    >
      {content}
    </div>,
    document.body
  );
}

export default TemplateV2SelectionToolbar;
