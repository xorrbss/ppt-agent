export const AUTHORED_HYBRID_SCHEMA_VERSION =
  "presenton.authored-hybrid/v1" as const;

export const AUTHORED_SLIDE_WIDTH_PX = 1280 as const;
export const AUTHORED_SLIDE_HEIGHT_PX = 720 as const;
export const AUTHORED_CSS_DPI = 96 as const;

export type AuthoredHybridSchemaVersion =
  typeof AUTHORED_HYBRID_SCHEMA_VERSION;

export type AuthoredHybridNativeKind = "text" | "image" | "shape";
export type AuthoredHybridTextRole =
  | "title"
  | "body"
  | "numeric"
  | "caption";

export const AUTHORED_HYBRID_FALLBACK_REASONS = [
  "ambiguous-whitespace",
  "animated",
  "backdrop-filter",
  "background-clip-text",
  "clip-path",
  "complex-chart",
  "complex-content",
  "complex-diagram",
  "complex-table",
  "complex-transform",
  "css-columns",
  "decorated-image",
  "decorated-text",
  "extraction-error",
  "external-paint",
  "filter",
  "invalid-bounds",
  "mask",
  "mix-blend-mode",
  "occluded",
  "outside-slide",
  "overflow-clipped",
  "pseudo-element",
  "run-extraction-error",
  "rounded-image",
  "svg-text",
  "text-shadow",
  "transformed-ancestor",
  "unknown-z-order",
  "unsupported-background",
  "unsupported-color",
  "unsupported-direction",
  "unsupported-image-format",
  "unsupported-object-position",
  "unsupported-object-fit",
  "unsupported-opacity",
  "unsupported-role",
  "unsupported-shape",
  "vertical-writing",
] as const;

export type AuthoredHybridFallbackReason =
  (typeof AUTHORED_HYBRID_FALLBACK_REASONS)[number];

export interface AuthoredHybridRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface AuthoredHybridBounds {
  px: AuthoredHybridRect;
  inches: AuthoredHybridRect;
}

export interface AuthoredHybridColor {
  /** Six-digit, uppercase sRGB hex without a leading hash. */
  hex: string;
  /** Alpha encoded by the CSS color itself, independently of element opacity. */
  alpha: number;
}

export interface AuthoredHybridLineHeight {
  points: number;
  multiple: number;
  source: "computed" | "normal";
}

export interface AuthoredHybridTextStyle {
  fontFamily: string;
  fontFamilies: string[];
  cjkFallbackFamilies: string[];
  fontSizePt: number;
  fontWeight: number;
  bold: boolean;
  italic: boolean;
  underline: boolean;
  strike: boolean;
  color: AuthoredHybridColor;
  letterSpacingPt: number;
  lineHeight: AuthoredHybridLineHeight;
  horizontalAlignment: "left" | "center" | "right" | "justify";
  verticalAlignment: "top" | "middle" | "bottom";
  direction: "ltr" | "rtl";
}

export interface AuthoredHybridTextRun {
  text: string;
  bounds: AuthoredHybridBounds;
  fragments: AuthoredHybridBounds[];
  style: AuthoredHybridTextStyle;
}

export interface AuthoredHybridTextPayload {
  role: AuthoredHybridTextRole;
  plainText: string;
  paragraphs: string[];
  style: AuthoredHybridTextStyle;
  runs: AuthoredHybridTextRun[];
}

export interface AuthoredHybridImagePayload {
  src: string;
  alt: string;
  naturalWidth: number;
  naturalHeight: number;
  objectFit: "contain" | "cover" | "fill" | "none" | "scale-down";
  objectPosition: string;
  crop: {
    left: number;
    top: number;
    right: number;
    bottom: number;
  };
}

export interface AuthoredHybridShapePayload {
  shape: "rectangle" | "round-rectangle" | "ellipse" | "line";
  fill: AuthoredHybridColor | null;
  stroke: AuthoredHybridColor | null;
  strokeWidthPt: number;
  radiusPt: number;
}

export interface AuthoredHybridElementBase {
  id: string;
  domPath: string;
  tagName: string;
  sourceIndex: number;
  /** Stable DOM paint proxy; native promotion requires unambiguous stacking. */
  zOrder: number;
  /** The computed CSS z-index when it is an integer; null represents `auto`. */
  cssZIndex: number | null;
  bounds: AuthoredHybridBounds;
  rotationDeg: number;
  opacity: number;
}

export interface AuthoredHybridNativeTextElement
  extends AuthoredHybridElementBase {
  classification: {
    mode: "native";
    kind: "text";
    confidence: "safe";
  };
  text: AuthoredHybridTextPayload;
}

export interface AuthoredHybridNativeImageElement
  extends AuthoredHybridElementBase {
  classification: {
    mode: "native";
    kind: "image";
    confidence: "safe";
  };
  image: AuthoredHybridImagePayload;
}

export interface AuthoredHybridNativeShapeElement
  extends AuthoredHybridElementBase {
  classification: {
    mode: "native";
    kind: "shape";
    confidence: "safe";
  };
  shape: AuthoredHybridShapePayload;
}

export interface AuthoredHybridRasterElement extends AuthoredHybridElementBase {
  classification: {
    mode: "raster";
    candidateKind: AuthoredHybridNativeKind | "complex";
    reasons: AuthoredHybridFallbackReason[];
  };
}

export type AuthoredHybridElement =
  | AuthoredHybridNativeTextElement
  | AuthoredHybridNativeImageElement
  | AuthoredHybridNativeShapeElement
  | AuthoredHybridRasterElement;

export interface AuthoredHybridBackplatePlan {
  strategy: "hide-native-leaves";
  mediaType: "image/png";
  widthPx: typeof AUTHORED_SLIDE_WIDTH_PX;
  heightPx: typeof AUTHORED_SLIDE_HEIGHT_PX;
  /** Chrome captures with omitBackground so transparent CSS remains RGBA. */
  transparentBackground: true;
  /** H2 may promote any subset; only successfully assembled IDs are hidden. */
  eligibleElementIds: string[];
  rasterElementIds: string[];
}

/**
 * Stable H1 hand-off consumed by H2. It deliberately contains no DOM objects,
 * timestamps, environment paths, or PNG bytes, so JSON serialization is
 * deterministic for the same rendered slide.
 */
export interface AuthoredHybridSlideV1 {
  schemaVersion: AuthoredHybridSchemaVersion;
  source: {
    kind: "authored-html";
    htmlSha256: string;
    /** Asset resolution context; null means the temporary file URL is the base. */
    baseUrl: string | null;
    viewport: {
      widthPx: typeof AUTHORED_SLIDE_WIDTH_PX;
      heightPx: typeof AUTHORED_SLIDE_HEIGHT_PX;
      cssDpi: typeof AUTHORED_CSS_DPI;
      widthIn: number;
      heightIn: number;
    };
  };
  elements: AuthoredHybridElement[];
  backplate: AuthoredHybridBackplatePlan;
  warnings: string[];
}

export interface AuthoredHybridBackplateRenderResult {
  /** RGBA PNG with only identity-verified native elements suppressed. */
  backplatePng: Buffer;
  /** Requested elements whose current DOM identity still matched extraction. */
  appliedPromotedElementIds: string[];
  /** Requested elements left rasterised because their DOM identity drifted. */
  fallbackElementIds: string[];
}
