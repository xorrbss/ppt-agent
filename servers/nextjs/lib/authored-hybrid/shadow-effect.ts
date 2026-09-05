/**
 * Native DrawingML outer-shadow mapping for authored-hybrid shapes.
 *
 * The browser (see `shadow-effect-browser.ts`) selects one simple outer CSS
 * `box-shadow` layer per shape and records it as `shape.outerShadow`. This
 * module turns that record into `<a:effectLst><a:outerShdw/></a:effectLst>`
 * and tells the backplate capture which promoted elements had their shadow
 * emitted natively, so the same shadow is never painted twice.
 *
 * Unit choices:
 * - lengths use the export's fixed 96 CSS px per inch (9525 EMU per px);
 * - `blurRad` is the CSS blur radius 1:1 (CSS blurs with sigma = radius / 2;
 *   PowerPoint's visible blur extent matches the same radius closely enough
 *   for slide decoration, and an exact match does not exist);
 * - `dist`/`dir` come from the offset vector, `dir` in 60000ths of a degree
 *   measured clockwise from +x in screen space (0 = right, 90 = down);
 * - CSS spread has no attribute of its own; it is approximated by scaling the
 *   shadow about its centre (`sx`/`sy` with `algn="ctr"`) using the shape
 *   bounds. Without bounds spread is ignored.
 */
import type {
  AuthoredHybridElement,
  AuthoredHybridRect,
  AuthoredHybridShapePayload,
} from "./schema.ts";

export type AuthoredHybridOuterShadow = NonNullable<
  AuthoredHybridShapePayload["outerShadow"]
>;

/** Same constant as `native-plan.ts`; duplicated to avoid a circular import. */
const EMU_PER_CSS_PX = 9_525;
const SIXTY_THOUSANDTHS_PER_DEGREE = 60_000;
const MAX_SHADOW_LENGTH_PX = 20_000;
const MIN_SCALE_PERCENT = 10_000;
const MAX_SCALE_PERCENT = 400_000;

function finiteWithin(value: number, limit: number): boolean {
  return Number.isFinite(value) && Math.abs(value) <= limit;
}

export function outerShadowIsSafe(value: unknown): value is AuthoredHybridOuterShadow {
  if (typeof value !== "object" || value === null) return false;
  const shadow = value as AuthoredHybridOuterShadow;
  return (
    finiteWithin(shadow.offsetXPx, MAX_SHADOW_LENGTH_PX) &&
    finiteWithin(shadow.offsetYPx, MAX_SHADOW_LENGTH_PX) &&
    finiteWithin(shadow.blurPx, MAX_SHADOW_LENGTH_PX) &&
    shadow.blurPx >= 0 &&
    finiteWithin(shadow.spreadPx, MAX_SHADOW_LENGTH_PX) &&
    typeof shadow.color === "object" &&
    shadow.color !== null &&
    /^[0-9A-F]{6}$/.test(shadow.color.hex) &&
    Number.isFinite(shadow.color.alpha) &&
    shadow.color.alpha >= 0 &&
    shadow.color.alpha <= 1
  );
}

export function outerShadowDirection(offsetXPx: number, offsetYPx: number): number {
  if (offsetXPx === 0 && offsetYPx === 0) return 0;
  const degrees = (Math.atan2(offsetYPx, offsetXPx) * 180) / Math.PI;
  const normalized = ((degrees % 360) + 360) % 360;
  return Math.round(normalized * SIXTY_THOUSANDTHS_PER_DEGREE) %
    (360 * SIXTY_THOUSANDTHS_PER_DEGREE);
}

/** DrawingML percentage (100000 = 100%) that grows an extent by `spreadPx` per side. */
export function outerShadowScalePercent(spreadPx: number, extentPx: number): number {
  if (!(extentPx > 0.01)) return 100_000;
  const scaled = Math.round(((extentPx + spreadPx * 2) / extentPx) * 100_000);
  return Math.max(MIN_SCALE_PERCENT, Math.min(MAX_SCALE_PERCENT, scaled));
}

export function outerShadowEffectXml(
  shadow: AuthoredHybridOuterShadow,
  opacity: number,
  bounds?: AuthoredHybridRect
): string {
  const emu = (value: number) => Math.max(0, Math.round(value * EMU_PER_CSS_PX));
  const blurRad = emu(shadow.blurPx);
  const dist = emu(Math.hypot(shadow.offsetXPx, shadow.offsetYPx));
  const dir = outerShadowDirection(shadow.offsetXPx, shadow.offsetYPx);
  const scale = bounds && shadow.spreadPx !== 0
    ? ` sx="${outerShadowScalePercent(shadow.spreadPx, bounds.width)}" sy="${outerShadowScalePercent(shadow.spreadPx, bounds.height)}"`
    : "";
  const alpha = Math.max(
    0,
    Math.min(100_000, Math.round(shadow.color.alpha * opacity * 100_000))
  );
  const color = `<a:srgbClr val="${shadow.color.hex}">${alpha < 100_000 ? `<a:alpha val="${alpha}"/>` : ""}</a:srgbClr>`;
  return `<a:effectLst><a:outerShdw blurRad="${blurRad}" dist="${dist}" dir="${dir}"${scale} algn="ctr" rotWithShape="0">${color}</a:outerShdw></a:effectLst>`;
}

/** Any CSS box-shadow paint recorded on the payload, native or residual. */
export function shapeHasShadowPaint(shape: AuthoredHybridShapePayload): boolean {
  return Boolean(
    shape.outerShadow ||
      (shape.backplateShadowLayers ?? 0) > 0 ||
      shape.shadowLayers?.length
  );
}

/**
 * Whether backplate capture must drop the mapped shadow layer for a promoted
 * element. True only when the serialized native owner carries the effect: the
 * element's own shape, or the painted container of a text root.
 */
export function promotedElementEmitsOuterShadow(
  element: AuthoredHybridElement
): boolean {
  const shape =
    "text" in element && element.text?.containerShape
      ? element.text.containerShape.shape
      : "shape" in element
        ? element.shape
        : undefined;
  return Boolean(shape?.outerShadow && outerShadowIsSafe(shape.outerShadow));
}
