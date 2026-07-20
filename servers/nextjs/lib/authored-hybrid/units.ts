import type {
  AuthoredHybridBounds,
  AuthoredHybridRect,
} from "./schema.ts";

import {
  AUTHORED_CSS_DPI,
  AUTHORED_SLIDE_HEIGHT_PX,
  AUTHORED_SLIDE_WIDTH_PX,
} from "./schema.ts";

const OUTPUT_PRECISION = 1_000_000;

export function roundHybridNumber(value: number): number {
  return Math.round(value * OUTPUT_PRECISION) / OUTPUT_PRECISION;
}

export function pxToInches(px: number): number {
  return roundHybridNumber(px / AUTHORED_CSS_DPI);
}

export function pxToPoints(px: number): number {
  return roundHybridNumber(px * (72 / AUTHORED_CSS_DPI));
}

export function rectPxToBounds(rect: AuthoredHybridRect): AuthoredHybridBounds {
  const px = {
    x: roundHybridNumber(rect.x),
    y: roundHybridNumber(rect.y),
    width: roundHybridNumber(rect.width),
    height: roundHybridNumber(rect.height),
  };

  return {
    px,
    inches: {
      x: pxToInches(px.x),
      y: pxToInches(px.y),
      width: pxToInches(px.width),
      height: pxToInches(px.height),
    },
  };
}

export const AUTHORED_SLIDE_WIDTH_IN = pxToInches(AUTHORED_SLIDE_WIDTH_PX);
export const AUTHORED_SLIDE_HEIGHT_IN = pxToInches(AUTHORED_SLIDE_HEIGHT_PX);
