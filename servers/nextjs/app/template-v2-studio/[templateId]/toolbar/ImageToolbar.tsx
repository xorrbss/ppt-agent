"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";

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
  const t = useTranslations("studio");
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
        <ToolbarField label={t("toolbar.image.cropHorizontalFocus")}>
          <span aria-hidden="true">
            {t("toolbar.image.horizontalAxisShort")}
          </span>
          <input
            className="w-24 accent-violet-400"
            aria-label={t("toolbar.image.cropHorizontalFocus")}
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
        <ToolbarField label={t("toolbar.image.cropVerticalFocus")}>
          <span aria-hidden="true">
            {t("toolbar.image.verticalAxisShort")}
          </span>
          <input
            className="w-24 accent-violet-400"
            aria-label={t("toolbar.image.cropVerticalFocus")}
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
        <ToolbarField label={t("toolbar.image.cropScale")}>
          <span aria-hidden="true">{t("toolbar.image.scale")}</span>
          <input
            className={`${toolbarInputClass} w-20`}
            aria-label={t("toolbar.image.cropScale")}
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
          aria-label={t("toolbar.image.resetCrop")}
          onClick={() =>
            publishDraft({ focusX: 50, focusY: 50, cropScale: 1 })
          }
        >
          {t("toolbar.common.reset")}
        </ToolbarButton>
        <ToolbarButton
          aria-label={t("toolbar.image.cancelCrop")}
          onClick={closeCrop}
        >
          {t("toolbar.common.cancel")}
        </ToolbarButton>
        <ToolbarButton
          aria-label={t("toolbar.image.applyCrop")}
          className="border-violet-400 bg-violet-500/20"
          onClick={applyCrop}
        >
          {t("toolbar.common.apply")}
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
      <ToolbarField label={t("toolbar.image.fit")}>
        <ToolbarSelect
          aria-label={t("toolbar.image.fit")}
          value={fit}
          onChange={(event) =>
            onPatch([{ path: ["fit"], value: event.target.value }], {
              historyKey: "toolbar:image-fit",
            })
          }
        >
          <option value="cover">{t("toolbar.image.fitCover")}</option>
          <option value="contain">{t("toolbar.image.fitContain")}</option>
          <option value="fill">{t("toolbar.image.fitStretch")}</option>
        </ToolbarSelect>
      </ToolbarField>
      <ToolbarButton
        aria-label={t("toolbar.image.cropImage")}
        onClick={beginCrop}
      >
        {t("toolbar.image.crop")}
      </ToolbarButton>
      <ToolbarButton
        aria-label={t("toolbar.image.flipHorizontal")}
        aria-pressed={booleanValue(element.flip_h)}
        onClick={() =>
          onPatch(
            [{ path: ["flip_h"], value: !booleanValue(element.flip_h) }],
            { historyKey: "toolbar:image-flip-h" }
          )
        }
      >
        {t("toolbar.image.flipHorizontalShort")}
      </ToolbarButton>
      <ToolbarButton
        aria-label={t("toolbar.image.flipVertical")}
        aria-pressed={booleanValue(element.flip_v)}
        onClick={() =>
          onPatch(
            [{ path: ["flip_v"], value: !booleanValue(element.flip_v) }],
            { historyKey: "toolbar:image-flip-v" }
          )
        }
      >
        {t("toolbar.image.flipVerticalShort")}
      </ToolbarButton>
      <ToolbarField label={t("toolbar.image.cornerRadius")}>
        <span aria-hidden="true">{t("toolbar.image.radius")}</span>
        <input
          className={`${toolbarInputClass} w-16`}
          aria-label={t("toolbar.image.cornerRadius")}
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
      <ToolbarField label={t("toolbar.image.opacity")}>
        <span aria-hidden="true">{t("toolbar.common.opacity")}</span>
        <input
          className="w-20 accent-violet-400"
          aria-label={t("toolbar.image.opacity")}
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
        <ToolbarButton
          aria-label={t("toolbar.image.replaceImage")}
          onClick={onReplaceImage}
        >
          {t("toolbar.image.replace")}
        </ToolbarButton>
      )}
    </div>
  );
}
