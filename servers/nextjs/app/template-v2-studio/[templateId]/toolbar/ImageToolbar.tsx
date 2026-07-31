"use client";

import { useEffect, useState } from "react";

import {
  normalizeImageCropScale,
  normalizeImageFocusPoint,
  type ImageFit,
} from "@/components/slide-editor/images/imageCrop";
import type { TemplateV2ToolbarRecord } from "@/lib/template-v2-toolbar";
import {
  ToolbarButton,
  ToolbarField,
  ToolbarSelect,
  toolbarInputClass,
} from "./ToolbarPrimitives";
import type {
  TemplateV2ImageCropDraft,
  TemplateV2ToolbarPatch,
  TemplateV2ToolbarPath,
} from "./types";

const numberValue = (value: unknown, fallback: number) =>
  typeof value === "number" && Number.isFinite(value) ? value : fallback;

const booleanValue = (value: unknown) => value === true;

function cropDraftFromElement(
  element: TemplateV2ToolbarRecord
): TemplateV2ImageCropDraft {
  const focus = normalizeImageFocusPoint({
    x: numberValue(element.focus_x, 50),
    y: numberValue(element.focus_y, 50),
  });
  return {
    focusX: focus.x,
    focusY: focus.y,
    cropScale: normalizeImageCropScale(element.crop_scale),
  };
}

export default function ImageToolbar({
  element,
  selectionPath,
  onPatch,
  onCropModeChange,
  onCropDraftChange,
  onReplaceImage,
}: {
  element: TemplateV2ToolbarRecord;
  selectionPath: TemplateV2ToolbarPath | null;
  onPatch: (
    patches: TemplateV2ToolbarPatch[],
    options?: { historyKey?: string; continuous?: boolean }
  ) => void;
  onCropModeChange?: (path: TemplateV2ToolbarPath | null) => void;
  onCropDraftChange?: (draft: TemplateV2ImageCropDraft | null) => void;
  onReplaceImage?: () => void;
}) {
  const persistedDraft = cropDraftFromElement(element);
  const [cropping, setCropping] = useState(false);
  const [draft, setDraft] = useState(persistedDraft);
  const fit: ImageFit =
    element.fit === "contain" || element.fit === "fill"
      ? element.fit
      : "cover";

  useEffect(() => {
    if (!cropping) setDraft(cropDraftFromElement(element));
  }, [cropping, element]);

  useEffect(
    () => () => {
      onCropModeChange?.(null);
      onCropDraftChange?.(null);
    },
    [onCropDraftChange, onCropModeChange]
  );

  function publishDraft(next: TemplateV2ImageCropDraft) {
    setDraft(next);
    onCropDraftChange?.(next);
  }

  function beginCrop() {
    const next = cropDraftFromElement(element);
    setCropping(true);
    publishDraft(next);
    onCropModeChange?.(selectionPath);
  }

  function closeCrop() {
    setCropping(false);
    onCropDraftChange?.(null);
    onCropModeChange?.(null);
  }

  function applyCrop() {
    onPatch(
      [
        { path: ["fit"], value: "cover" },
        { path: ["focus_x"], value: draft.focusX },
        { path: ["focus_y"], value: draft.focusY },
        {
          path: ["crop_scale"],
          value: Math.abs(draft.cropScale - 1) > 0.01 ? draft.cropScale : null,
        },
      ],
      { historyKey: "toolbar:image-crop" }
    );
    closeCrop();
  }

  if (cropping) {
    return (
      <div
        className="flex items-center gap-2"
        data-testid="template-v2-image-crop-controls"
      >
        <ToolbarField label="Crop horizontal focus">
          <span aria-hidden="true">X</span>
          <input
            className="w-24 accent-violet-400"
            aria-label="Crop horizontal focus"
            type="range"
            min={0}
            max={100}
            step={1}
            value={draft.focusX}
            onChange={(event) =>
              publishDraft({
                ...draft,
                focusX: Number(event.target.value),
              })
            }
          />
        </ToolbarField>
        <ToolbarField label="Crop vertical focus">
          <span aria-hidden="true">Y</span>
          <input
            className="w-24 accent-violet-400"
            aria-label="Crop vertical focus"
            type="range"
            min={0}
            max={100}
            step={1}
            value={draft.focusY}
            onChange={(event) =>
              publishDraft({
                ...draft,
                focusY: Number(event.target.value),
              })
            }
          />
        </ToolbarField>
        <ToolbarField label="Crop scale">
          <span aria-hidden="true">Scale</span>
          <input
            className={`${toolbarInputClass} w-20`}
            aria-label="Crop scale"
            type="number"
            min={0.1}
            max={6}
            step={0.1}
            value={draft.cropScale}
            onChange={(event) =>
              publishDraft({
                ...draft,
                cropScale: normalizeImageCropScale(Number(event.target.value)),
              })
            }
          />
        </ToolbarField>
        <ToolbarButton
          aria-label="Reset image crop"
          onClick={() =>
            publishDraft({ focusX: 50, focusY: 50, cropScale: 1 })
          }
        >
          Reset
        </ToolbarButton>
        <ToolbarButton aria-label="Cancel image crop" onClick={closeCrop}>
          Cancel
        </ToolbarButton>
        <ToolbarButton
          aria-label="Apply image crop"
          className="border-violet-400 bg-violet-500/20"
          onClick={applyCrop}
        >
          Apply
        </ToolbarButton>
      </div>
    );
  }

  const radiusRecord =
    element.border_radius &&
    typeof element.border_radius === "object" &&
    !Array.isArray(element.border_radius)
      ? (element.border_radius as TemplateV2ToolbarRecord)
      : {};
  const radius = numberValue(radiusRecord.tl, 0);

  return (
    <div className="flex items-center gap-2">
      <ToolbarField label="Image fit">
        <ToolbarSelect
          aria-label="Image fit"
          value={fit}
          onChange={(event) =>
            onPatch([{ path: ["fit"], value: event.target.value }], {
              historyKey: "toolbar:image-fit",
            })
          }
        >
          <option value="cover">Cover</option>
          <option value="contain">Contain</option>
          <option value="fill">Stretch</option>
        </ToolbarSelect>
      </ToolbarField>
      <ToolbarButton aria-label="Crop image" onClick={beginCrop}>
        Crop
      </ToolbarButton>
      <ToolbarButton
        aria-label="Flip image horizontally"
        aria-pressed={booleanValue(element.flip_h)}
        onClick={() =>
          onPatch(
            [{ path: ["flip_h"], value: !booleanValue(element.flip_h) }],
            { historyKey: "toolbar:image-flip-h" }
          )
        }
      >
        Flip H
      </ToolbarButton>
      <ToolbarButton
        aria-label="Flip image vertically"
        aria-pressed={booleanValue(element.flip_v)}
        onClick={() =>
          onPatch(
            [{ path: ["flip_v"], value: !booleanValue(element.flip_v) }],
            { historyKey: "toolbar:image-flip-v" }
          )
        }
      >
        Flip V
      </ToolbarButton>
      <ToolbarField label="Image corner radius">
        <span aria-hidden="true">Radius</span>
        <input
          className={`${toolbarInputClass} w-16`}
          aria-label="Image corner radius"
          type="number"
          min={0}
          step={1}
          value={radius}
          onChange={(event) => {
            const value = Math.max(0, Number(event.target.value) || 0);
            onPatch(
              ["tl", "tr", "bl", "br"].map((corner) => ({
                path: ["border_radius", corner],
                value,
              })),
              {
                historyKey: "toolbar:image-radius",
                continuous: true,
              }
            );
          }}
        />
      </ToolbarField>
      <ToolbarField label="Image opacity">
        <span aria-hidden="true">Opacity</span>
        <input
          className="w-20 accent-violet-400"
          aria-label="Image opacity"
          type="range"
          min={0}
          max={1}
          step={0.01}
          value={numberValue(element.opacity, 1)}
          onChange={(event) =>
            onPatch(
              [{ path: ["opacity"], value: Number(event.target.value) }],
              {
                historyKey: "toolbar:image-opacity",
                continuous: true,
              }
            )
          }
        />
      </ToolbarField>
      {onReplaceImage && (
        <ToolbarButton aria-label="Replace image" onClick={onReplaceImage}>
          Replace
        </ToolbarButton>
      )}
    </div>
  );
}
