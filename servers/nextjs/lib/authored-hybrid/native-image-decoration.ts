import type { AuthoredHybridColor } from "./schema.ts";

/**
 * Plan step for browser-source.ts's `decorated-image`/`rounded-image` raster
 * fallback (browser-source.ts:1099-1123). Today ANY border-radius, border,
 * padding, or box-shadow on an `<img>` forces the whole element to raster.
 * This module computes, from the CSS decoration alone, whether the image can
 * instead stay a native `<p:pic>` (optionally paired with a plain native
 * `<p:sp>` rectangle for a border or a solid shadow) versus needing the
 * raster fallback.
 *
 * NOT YET WIRED IN: browser-source.ts still always raster-falls-back a
 * decorated/rounded image; this module is the planning primitive a future
 * change would call from there (after adding radius/border/shadow fields to
 * the image element payload in schema.ts/contract.ts). See this file's
 * accompanying node:tests for the exact three-way decision this makes.
 */

const MAX_RADIUS_ADJUSTMENT = 50_000;
const RADIUS_ADJUSTMENT_UNIT = 100_000;

export function nativeImageDecorationEnabled(): boolean {
  return process.env.AUTHORED_HYBRID_NATIVE_IMAGE_DECORATION !== "0";
}

export interface UniformCornerRadiiPx {
  readonly topLeft: number;
  readonly topRight: number;
  readonly bottomRight: number;
  readonly bottomLeft: number;
}

export interface NativeImageBorderPx {
  readonly widthPx: number;
  readonly color: AuthoredHybridColor;
}

export interface NativeImageBoxShadowPx {
  readonly offsetXPx: number;
  readonly offsetYPx: number;
  /** A positive blur radius always raster-falls-back; only 0 is admitted. */
  readonly blurPx: number;
  readonly spreadPx: number;
  readonly color: AuthoredHybridColor;
}

export interface ImageDecorationInputPx {
  readonly widthPx: number;
  readonly heightPx: number;
  readonly borderRadiusPx: UniformCornerRadiiPx;
  readonly border: NativeImageBorderPx | null;
  readonly boxShadow: NativeImageBoxShadowPx | null;
}

/** Ordered back-to-front: index 0 paints first (furthest back). */
export type NativeImageBackingShapeFactory = (nonVisualId: number) => string;

export type NativeImageDecorationPlan =
  | { readonly mode: "native-crop"; readonly pictureGeometryXml: string }
  | {
      readonly mode: "native-crop-with-backing";
      readonly pictureGeometryXml: string;
      readonly backingShapes: readonly NativeImageBackingShapeFactory[];
    }
  | { readonly mode: "raster-fallback"; readonly reason: string };

function uniformRadius(radii: UniformCornerRadiiPx): number | null {
  const { topLeft, topRight, bottomRight, bottomLeft } = radii;
  if (
    topLeft === topRight &&
    topRight === bottomRight &&
    bottomRight === bottomLeft
  ) {
    return topLeft;
  }
  return null;
}

/**
 * Same `adj` convention as native-plan.ts's `shapeGeometryXml`: the fraction
 * of the shorter side (0-50000, i.e. 0%-50%) that the corner radius covers.
 */
function roundRectGeometryXml(
  radiusPx: number,
  widthPx: number,
  heightPx: number
): string {
  if (radiusPx <= 0) {
    return '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>';
  }
  const shorterSide = Math.max(0.01, Math.min(widthPx, heightPx));
  const adjustment = Math.max(
    0,
    Math.min(
      MAX_RADIUS_ADJUSTMENT,
      Math.round((radiusPx / shorterSide) * RADIUS_ADJUSTMENT_UNIT)
    )
  );
  return `<a:prstGeom prst="roundRect"><a:avLst><a:gd name="adj" fmla="val ${adjustment}"/></a:avLst></a:prstGeom>`;
}

function colorXml(color: AuthoredHybridColor): string {
  const alphaMod =
    color.alpha < 1
      ? `<a:alpha val="${Math.max(0, Math.min(100_000, Math.round(color.alpha * 100_000)))}"/>`
      : "";
  return `<a:srgbClr val="${color.hex}">${alphaMod}</a:srgbClr>`;
}

function borderBackingShape(
  border: NativeImageBorderPx,
  geometryXml: string
): NativeImageBackingShapeFactory {
  const EMU_PER_CSS_PX = 9_525;
  const strokeWidthEmu = Math.max(1, Math.round(border.widthPx * EMU_PER_CSS_PX));
  // This factory emits geometry preset + paint only, matching the picture's
  // own local bounds; the caller wraps it with the shared <a:xfrm> transform
  // builder used for every other native shape/picture.
  return (nonVisualId: number) =>
    `<p:sp><p:nvSpPr><p:cNvPr id="${nonVisualId}" name="Presenton native image border"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr>${geometryXml}<a:noFill/><a:ln w="${strokeWidthEmu}"><a:solidFill>${colorXml(border.color)}</a:solidFill></a:ln></p:spPr></p:sp>`;
}

function shadowBackingShape(
  shadow: NativeImageBoxShadowPx,
  geometryXml: string
): NativeImageBackingShapeFactory {
  return (nonVisualId: number) =>
    `<p:sp><p:nvSpPr><p:cNvPr id="${nonVisualId}" name="Presenton native image shadow"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr>${geometryXml}<a:solidFill>${colorXml(shadow.color)}</a:solidFill><a:ln><a:noFill/></a:ln></p:spPr></p:sp>`;
}

/**
 * Decide whether an `<img>` with the given CSS decoration can stay native.
 * Geometry callers still own placement: this only returns preset/paint XML
 * fragments sized in the element's own local (0,0)-origin coordinate space,
 * to be combined with the shared `<a:xfrm>` transform builder used for every
 * other native shape/picture.
 */
export function planNativeImageDecoration(
  input: ImageDecorationInputPx
): NativeImageDecorationPlan {
  if (input.boxShadow && input.boxShadow.blurPx > 0) {
    return { mode: "raster-fallback", reason: "blurred-shadow" };
  }
  const radius = uniformRadius(input.borderRadiusPx);
  if (radius === null) {
    return { mode: "raster-fallback", reason: "asymmetric-radius" };
  }

  const pictureGeometryXml = roundRectGeometryXml(
    radius,
    input.widthPx,
    input.heightPx
  );

  if (!input.border && !input.boxShadow) {
    return { mode: "native-crop", pictureGeometryXml };
  }

  const backingShapes: NativeImageBackingShapeFactory[] = [];
  if (input.boxShadow) {
    // Solid (unblurred) shadow: a same-shape rectangle offset and grown by
    // spread, painted behind the picture.
    const spread = input.boxShadow.spreadPx;
    const shadowGeometryXml = roundRectGeometryXml(
      radius,
      input.widthPx + spread * 2,
      input.heightPx + spread * 2
    );
    backingShapes.push(shadowBackingShape(input.boxShadow, shadowGeometryXml));
  }
  if (input.border) {
    backingShapes.push(borderBackingShape(input.border, pictureGeometryXml));
  }

  return { mode: "native-crop-with-backing", pictureGeometryXml, backingShapes };
}
