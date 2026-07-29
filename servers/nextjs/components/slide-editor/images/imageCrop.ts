export const MIN_IMAGE_CROP_SCALE = 0.1;
export const MAX_IMAGE_CROP_SCALE = 6;

export type ImageFit = "cover" | "contain" | "fill";
export type ImageFocusPoint = { x: number; y: number };
export type ImageCropFrame = {
  left: number;
  top: number;
  width: number;
  height: number;
};
export type ImageCropDraft = ImageFocusPoint & { scale: number };

export type ImageEditorProperties = {
  initialObjectFit?: ImageFit;
  initialFocusPoint?: Partial<ImageFocusPoint> | null;
  crop_scale?: number | null;
  cropScale?: number | null;
  initialCropScale?: number | null;
};

const EMPTY_IMAGE_PROPERTIES: ImageEditorProperties = {};

const finiteNumber = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;

const clamp = (value: number, min: number, max: number) =>
  Math.min(max, Math.max(min, value));

export function normalizeImageCropScale(value: unknown): number {
  return clamp(
    finiteNumber(value) ?? 1,
    MIN_IMAGE_CROP_SCALE,
    MAX_IMAGE_CROP_SCALE
  );
}

export function normalizeImageFocusPoint(
  value: Partial<ImageFocusPoint> | null | undefined
): ImageFocusPoint {
  return {
    x: clamp(finiteNumber(value?.x) ?? 50, 0, 100),
    y: clamp(finiteNumber(value?.y) ?? 50, 0, 100),
  };
}

/**
 * V1 slides historically store editor properties in an index-keyed array/object,
 * while newer callers pass the selected image's property record directly.
 */
export function resolveImageEditorProperties(
  properties: unknown,
  imageIdx = 0
): ImageEditorProperties {
  if (!properties || typeof properties !== "object") {
    return EMPTY_IMAGE_PROPERTIES;
  }
  const candidate = properties as Record<string | number, unknown>;
  const directKeys = [
    "initialObjectFit",
    "initialFocusPoint",
    "crop_scale",
    "cropScale",
    "initialCropScale",
  ];
  if (directKeys.some((key) => key in candidate)) {
    return candidate as ImageEditorProperties;
  }
  const indexed = candidate[imageIdx];
  return indexed && typeof indexed === "object"
    ? (indexed as ImageEditorProperties)
    : EMPTY_IMAGE_PROPERTIES;
}

export function cropScaleFromProperties(
  properties: ImageEditorProperties | null | undefined
): number {
  return normalizeImageCropScale(
    properties?.crop_scale ??
      properties?.cropScale ??
      properties?.initialCropScale
  );
}

export function imageCropProperties(
  fit: ImageFit,
  focusPoint: ImageFocusPoint,
  cropScale: number
) {
  const normalizedScale = normalizeImageCropScale(cropScale);
  return {
    initialObjectFit: fit,
    initialFocusPoint: normalizeImageFocusPoint(focusPoint),
    crop_scale: Math.abs(normalizedScale - 1) > 0.01 ? normalizedScale : null,
  };
}

export function imageCropDomStyle(
  fit: ImageFit,
  focusPoint: ImageFocusPoint,
  cropScale: number
) {
  const focus = normalizeImageFocusPoint(focusPoint);
  const scale = normalizeImageCropScale(cropScale);
  const cropActive = fit === "cover" && Math.abs(scale - 1) > 0.01;
  return {
    objectFit: fit,
    objectPosition: `${focus.x}% ${focus.y}%`,
    transform: cropActive ? `scale(${scale})` : "",
    transformOrigin: cropActive ? `${focus.x}% ${focus.y}%` : "",
  };
}

function naturalRatio(
  naturalSize: { width: number; height: number } | null
) {
  return naturalSize && naturalSize.width > 0 && naturalSize.height > 0
    ? naturalSize.width / naturalSize.height
    : 1;
}

export function baseCoverImageSize(
  frame: ImageCropFrame,
  naturalSize: { width: number; height: number } | null
) {
  const imageRatio = naturalRatio(naturalSize);
  const frameRatio = frame.width / frame.height || 1;
  if (imageRatio > frameRatio) {
    return { width: frame.height * imageRatio, height: frame.height };
  }
  return { width: frame.width, height: frame.width / imageRatio };
}

/**
 * Matches upstream crop geometry for both zoomed-in and zoomed-out images.
 * Positive free space is intentional when scale is below 1.
 */
export function cropImageFrameForDraft(
  frame: ImageCropFrame,
  naturalSize: { width: number; height: number } | null,
  draft: ImageCropDraft
): ImageCropFrame {
  const baseSize = baseCoverImageSize(frame, naturalSize);
  const scale = normalizeImageCropScale(draft.scale);
  const width = baseSize.width * scale;
  const height = baseSize.height * scale;
  return {
    left: frame.left + (frame.width - width) * (clamp(draft.x, 0, 100) / 100),
    top: frame.top + (frame.height - height) * (clamp(draft.y, 0, 100) / 100),
    width,
    height,
  };
}

export function constrainCropImageFrame(
  cropFrame: ImageCropFrame,
  imageFrame: ImageCropFrame
): ImageCropFrame {
  const horizontalRange = cropFrame.width - imageFrame.width;
  const verticalRange = cropFrame.height - imageFrame.height;
  const minLeft =
    horizontalRange >= 0
      ? cropFrame.left
      : cropFrame.left + horizontalRange;
  const maxLeft =
    horizontalRange >= 0
      ? cropFrame.left + horizontalRange
      : cropFrame.left;
  const minTop =
    verticalRange >= 0 ? cropFrame.top : cropFrame.top + verticalRange;
  const maxTop =
    verticalRange >= 0
      ? cropFrame.top + verticalRange
      : cropFrame.top;
  return {
    ...imageFrame,
    left: clamp(imageFrame.left, minLeft, maxLeft),
    top: clamp(imageFrame.top, minTop, maxTop),
  };
}

export function cropDraftFromImageFrame(
  cropFrame: ImageCropFrame,
  imageFrame: ImageCropFrame,
  scale: number
): ImageCropDraft {
  const overflowX = Math.max(0, imageFrame.width - cropFrame.width);
  const overflowY = Math.max(0, imageFrame.height - cropFrame.height);
  const availableX = Math.max(0, cropFrame.width - imageFrame.width);
  const availableY = Math.max(0, cropFrame.height - imageFrame.height);
  return {
    x: clamp(
      overflowX > 0
        ? ((cropFrame.left - imageFrame.left) / overflowX) * 100
        : availableX > 0
          ? ((imageFrame.left - cropFrame.left) / availableX) * 100
          : 50,
      0,
      100
    ),
    y: clamp(
      overflowY > 0
        ? ((cropFrame.top - imageFrame.top) / overflowY) * 100
        : availableY > 0
          ? ((imageFrame.top - cropFrame.top) / availableY) * 100
          : 50,
      0,
      100
    ),
    scale: normalizeImageCropScale(scale),
  };
}
