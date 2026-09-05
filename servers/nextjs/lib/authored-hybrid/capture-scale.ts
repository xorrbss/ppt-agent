/**
 * Single source of truth for the Chromium device-scale factor used while
 * capturing the authored hybrid backplate (and any raster island painted
 * inside it). Raising this scale only changes pixel density of the raster
 * output; it must never change CSS-pixel geometry, since every native OOXML
 * shape position/size is derived from `getBoundingClientRect()` values that
 * are already device-pixel-ratio independent.
 *
 * `AUTHORED_HYBRID_BACKPLATE_SCALE` accepts an integer 1-3. Values outside
 * that range, or non-numeric input, fall back to the default of 2 (96 CSS dpi
 * x 2 = 192 effective dpi backplate, matching a "retina" capture without an
 * unbounded PNG/PPTX size).
 */
import { AUTHORED_SLIDE_HEIGHT_PX, AUTHORED_SLIDE_WIDTH_PX } from "./schema.ts";

const DEFAULT_BACKPLATE_SCALE = 2;
const MIN_BACKPLATE_SCALE = 1;
const MAX_BACKPLATE_SCALE = 3;

function readBackplateScale(): number {
  const raw = process.env.AUTHORED_HYBRID_BACKPLATE_SCALE?.trim();
  if (!raw) return DEFAULT_BACKPLATE_SCALE;
  const parsed = Number.parseInt(raw, 10);
  if (
    !Number.isInteger(parsed) ||
    parsed < MIN_BACKPLATE_SCALE ||
    parsed > MAX_BACKPLATE_SCALE
  ) {
    return DEFAULT_BACKPLATE_SCALE;
  }
  return parsed;
}

/**
 * The Chromium `deviceScaleFactor`/`window.devicePixelRatio` used for every
 * authored hybrid capture (analysis and backplate alike). Read once per
 * process: production processes set the env var (or accept the default)
 * before the first capture, and tests that need a different scale must set
 * `process.env.AUTHORED_HYBRID_BACKPLATE_SCALE` before importing any module
 * that re-exports this constant.
 */
export const BACKPLATE_SCALE = readBackplateScale();

export interface BackplatePixelSize {
  readonly width: number;
  readonly height: number;
}

/** The exact backplate PNG pixel dimensions expected at {@link BACKPLATE_SCALE}. */
export function backplatePixelSize(): BackplatePixelSize {
  return {
    width: AUTHORED_SLIDE_WIDTH_PX * BACKPLATE_SCALE,
    height: AUTHORED_SLIDE_HEIGHT_PX * BACKPLATE_SCALE,
  };
}
