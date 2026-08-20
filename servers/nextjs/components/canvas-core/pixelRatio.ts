/**
 * Shared Konva backing-store policy for the authored Master and Template V2
 * studio.  It keeps a readable minimum at fit scale while bounding GPU/heap
 * use on machines that advertise constrained CPU or memory capacity.
 */
export const SCENE_DEVICE_OVERSAMPLE = 1.35;
export const MIN_CONTENT_SCENE_PIXEL_RATIO = 2;
export const MAX_CONTENT_SCENE_PIXEL_RATIO = 4;
export const CONSTRAINED_SCENE_DEVICE_OVERSAMPLE = 1.15;
export const MIN_CONSTRAINED_CONTENT_SCENE_PIXEL_RATIO = 1.5;
export const MAX_CONSTRAINED_CONTENT_SCENE_PIXEL_RATIO = 3;

export function isConstrainedCanvasDevice({
  deviceMemory,
  hardwareConcurrency,
}: {
  deviceMemory?: number;
  hardwareConcurrency?: number;
}) {
  return (
    (Number.isFinite(deviceMemory) && deviceMemory! <= 4) ||
    (Number.isFinite(hardwareConcurrency) && hardwareConcurrency! <= 4)
  );
}

export function calculateCanvasPixelRatio({
  devicePixelRatio,
  displayScale,
  deviceMemory,
  hardwareConcurrency,
}: {
  devicePixelRatio: number;
  displayScale: number;
  deviceMemory?: number;
  hardwareConcurrency?: number;
}) {
  const constrained = isConstrainedCanvasDevice({
    deviceMemory,
    hardwareConcurrency,
  });
  const safeDevicePixelRatio = Number.isFinite(devicePixelRatio)
    ? Math.max(1, devicePixelRatio)
    : 1;
  const safeDisplayScale = Number.isFinite(displayScale)
    ? Math.max(0.1, Math.abs(displayScale))
    : 1;
  const minimum = constrained
    ? MIN_CONSTRAINED_CONTENT_SCENE_PIXEL_RATIO
    : MIN_CONTENT_SCENE_PIXEL_RATIO;
  const maximum = constrained
    ? MAX_CONSTRAINED_CONTENT_SCENE_PIXEL_RATIO
    : MAX_CONTENT_SCENE_PIXEL_RATIO;
  const oversample = constrained
    ? CONSTRAINED_SCENE_DEVICE_OVERSAMPLE
    : SCENE_DEVICE_OVERSAMPLE;

  return Math.min(
    maximum,
    Math.max(minimum, safeDevicePixelRatio * safeDisplayScale * oversample),
  );
}
