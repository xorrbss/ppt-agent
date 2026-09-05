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
 *   bounds. Without bounds spread is ignored;
 * - `rotWithShape="1"` so the effect rotates with the shape, matching CSS
 *   (a `box-shadow` rotates along with its element's `transform`).
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

/**
 * DrawingML percentage (100000 = 100%) that grows an extent by `spreadPx` per
 * side, or `null` when the spread is negative enough to collapse the extent
 * to zero or below. CSS erases a shadow entirely once its inflated box has no
 * area; clamping that case to the 10% floor would instead draw a small but
 * clearly visible blob, so callers must skip the effect for `null` rather
 * than render it.
 */
export function outerShadowScalePercent(spreadPx: number, extentPx: number): number | null {
  if (!(extentPx > 0.01)) return 100_000;
  const scaledExtent = extentPx + spreadPx * 2;
  if (scaledExtent <= 0) return null;
  const scaled = Math.round((scaledExtent / extentPx) * 100_000);
  return Math.max(MIN_SCALE_PERCENT, Math.min(MAX_SCALE_PERCENT, scaled));
}

/**
 * Whether an outer shadow's mapped effect can be trusted to paint the shape's
 * visible card rather than something else. CSS clips an outer `box-shadow` to
 * outside the element's border box; DrawingML's `outerShdw` does not, so a
 * translucent or missing fill would let the shadow show through a see-through
 * card, and a stroke-only shape would have PowerPoint draw the shadow of the
 * stroke path instead of the card.
 */
export function shapeHasOpaqueShadowCarrier(shape: AuthoredHybridShapePayload): boolean {
  return Boolean(shape.gradient) || Boolean(shape.fill && shape.fill.alpha >= 0.999);
}

/**
 * Whether a shape's `outerShadow` mapping may proceed at all: true as-is when
 * there is nothing to map. `hasBaseObject` is the caller's own
 * `shapeHasBaseObject` (fill/gradient/stroke) result -- passed in rather than
 * duplicated here, since `native-plan.ts` already needs it for unrelated
 * emission decisions regardless of any shadow. `shapeIsSafe` in
 * `native-plan.ts` calls this to refuse the whole mapping outright whenever a
 * base object would otherwise carry an unsafe or unrepresentable shadow; there
 * is no separate "shadow-only" rejection path.
 */
export function outerShadowMappingIsSafe(
  shape: AuthoredHybridShapePayload,
  hasBaseObject: boolean
): boolean {
  if (!shape.outerShadow) return true;
  if (!outerShadowIsSafe(shape.outerShadow)) return false;
  return !hasBaseObject || shapeHasOpaqueShadowCarrier(shape);
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
  let scale = "";
  if (bounds && shadow.spreadPx !== 0) {
    const sx = outerShadowScalePercent(shadow.spreadPx, bounds.width);
    const sy = outerShadowScalePercent(shadow.spreadPx, bounds.height);
    // The spread erases the shadow in CSS; emit no effect rather than a
    // clamped blob that was never visible in the source.
    if (sx === null || sy === null) return "";
    scale = ` sx="${sx}" sy="${sy}"`;
  }
  const alpha = Math.max(
    0,
    Math.min(100_000, Math.round(shadow.color.alpha * opacity * 100_000))
  );
  const color = `<a:srgbClr val="${shadow.color.hex}">${alpha < 100_000 ? `<a:alpha val="${alpha}"/>` : ""}</a:srgbClr>`;
  return `<a:effectLst><a:outerShdw blurRad="${blurRad}" dist="${dist}" dir="${dir}"${scale} algn="ctr" rotWithShape="1">${color}</a:outerShdw></a:effectLst>`;
}

/**
 * Whether backplate capture must drop the mapped shadow layer for a promoted
 * element. True only when the serialized native owner will actually carry
 * the effect: the element's own base-object shape, or the painted container
 * of a text root, with an opaque fill (or gradient) and a spread that does
 * not collapse the shadow to nothing. Only a base object (fill, gradient, or
 * stroke) ever gets an `<a:outerShdw>` in `native-plan.ts` -- border lines,
 * outline shapes, and residual shadow-layer shapes never carry one. So a
 * transparent card with only a CSS border and no base object at all can
 * still be promoted for its border, but never for a shadow it has no owner
 * for; claiming suppression there would delete the shadow with nothing left
 * on either layer to draw it. `shapeIsSafe` additionally refuses the whole
 * mapping outright when a base object *does* exist but is translucent or
 * stroke-only (see its `outerShadow` gate), which this same opaque-fill check
 * also catches. Reusing `outerShadowEffectXml` itself keeps this in lockstep
 * with what actually gets serialized rather than duplicating its rendering
 * logic.
 */
export function promotedElementEmitsOuterShadow(
  element: AuthoredHybridElement
): boolean {
  const container = "text" in element && element.text?.containerShape
    ? element.text.containerShape
    : undefined;
  const shape = container
    ? container.shape
    : "shape" in element
      ? element.shape
      : undefined;
  if (!shape?.outerShadow || !outerShadowIsSafe(shape.outerShadow)) return false;
  if (!shapeHasOpaqueShadowCarrier(shape)) return false;
  const bounds = container ? container.bounds.px : element.bounds.px;
  return outerShadowEffectXml(shape.outerShadow, element.opacity, bounds) !== "";
}
