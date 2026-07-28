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
  /** Preserve CSS nowrap/pre semantics so PowerPoint does not invent line breaks. */
  wrapMode?: "wrap" | "no-wrap";
}

export interface AuthoredHybridTextRun {
  text: string;
  bounds: AuthoredHybridBounds;
  fragments: AuthoredHybridBounds[];
  style: AuthoredHybridTextStyle;
  /**
   * Present only on an extracted newline run. Soft breaks are browser wrapping,
   * line breaks come from authored <br>/preserved source newlines, and paragraph
   * breaks separate block-flow text.
   */
  breakKind?: "soft" | "line" | "paragraph";
}

export interface AuthoredHybridTextBoxEdgesPx {
  top: number;
  right: number;
  bottom: number;
  left: number;
}

/**
 * Browser-computed text layout facts. All scalar lengths remain CSS pixels so
 * downstream PowerPoint mapping can apply one CSS-DPI conversion consistently.
 */
export interface AuthoredHybridTextLayout {
  /** CSS border box before editable text is narrowed to its content box. */
  boxBounds: AuthoredHybridBounds;
  /** CSS content box after border and padding. */
  contentBounds: AuthoredHybridBounds;
  /** Union of painted glyph fragments, or null when no glyph fragment exists. */
  paintedTextBounds: AuthoredHybridBounds | null;
  paddingPx: AuthoredHybridTextBoxEdgesPx;
  borderPx: AuthoredHybridTextBoxEdgesPx;
  marginPx: AuthoredHybridTextBoxEdgesPx;
  rowGapPx: number;
  columnGapPx: number;
  display: string;
  flexDirection: "row" | "row-reverse" | "column" | "column-reverse" | null;
  alignItems: string;
  justifyContent: string;
  /** Whether the computed text-align came from this box, an ancestor, or initial CSS. */
  textAlignSource: "self" | "inherited" | "default";
  /** Whether computed layout sized the text box explicitly or to its contents. */
  widthMode?: "fixed" | "content";
  lineCount: number;
  singleLine: boolean;
  paragraphSpacingPx: {
    before: number;
    after: number;
  };
}

export interface AuthoredHybridTextPayload {
  role: AuthoredHybridTextRole;
  plainText: string;
  paragraphs: string[];
  style: AuthoredHybridTextStyle;
  runs: AuthoredHybridTextRun[];
  /** Optional for backward compatibility with authored-hybrid/v1 observations. */
  layout?: AuthoredHybridTextLayout;
  /** A simple CSS fill/border owned by the text root, exported as one text shape. */
  containerShape?: {
    bounds: AuthoredHybridBounds;
    shape: AuthoredHybridShapePayload;
  };
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
  shape: "rectangle" | "round-rectangle" | "ellipse" | "line" | "freeform";
  fill: AuthoredHybridColor | null;
  /** Native PowerPoint linear-gradient fill reconstructed from CSS. */
  gradient?: {
    /** CSS angle: 0deg points upward, 90deg points right. */
    angleDeg: number;
    stops: Array<{
      color: AuthoredHybridColor;
      /** Normalized position from 0 through 1. Duplicate positions create hard stops. */
      position: number;
    }>;
  };
  stroke: AuthoredHybridColor | null;
  strokeWidthPt: number;
  /** PowerPoint preset dash reconstructed from a CSS border style. */
  dash?: "dash" | "dot";
  /** Rounded SVG stroke caps retained for faithful editable connector ends. */
  lineCap?: "round";
  /** Rounded SVG stroke joins retained for faithful sampled curves. */
  lineJoin?: "round";
  radiusPt: number;
  /** CSS sides that cannot be represented by a single uniform PowerPoint outline. */
  borderLines?: Array<{
    side: "top" | "right" | "bottom" | "left";
    color: AuthoredHybridColor;
    widthPt: number;
    dash?: "dash" | "dot";
  }>;
  /**
   * Solid, zero-blur CSS box-shadow layers reconstructed as independent
   * editable PowerPoint shapes underneath the owning shape.
   */
  shadowLayers?: Array<{
    offsetXPx: number;
    offsetYPx: number;
    spreadPx: number;
    color: AuthoredHybridColor;
  }>;
  /**
   * A simple CSS outline reconstructed as an independent editable PowerPoint
   * shape outside the owning shape. Unlike a border, an outline does not
   * participate in the element's CSS box dimensions.
   */
  outline?: {
    color: AuthoredHybridColor;
    widthPt: number;
    offsetPx: number;
    dash?: "dash" | "dot";
  };
  /** Arrowhead reconstructed from a CSS pseudo-element on a thin connector. */
  endArrow?: "triangle";
  /**
   * Normalized vertices for an editable SVG-derived freeform. Coordinates are
   * relative to the element bounds and must stay between 0 and 1.
   */
  points?: Array<{ x: number; y: number }>;
  /** Close an SVG-derived freeform so PowerPoint can preserve its native fill. */
  closed?: boolean;
  /** Suppress only this element's paint so child content remains in the backplate. */
  preserveContents?: boolean;
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
  /**
   * Text payloads survive conservative raster classification so the editable
   * export mode can promote the glyphs while keeping unsupported decoration in
   * the fidelity backplate.
   */
  text?: AuthoredHybridTextPayload;
  /** Safe PowerPoint approximations may also be promoted from raster fallback. */
  shape?: AuthoredHybridShapePayload;
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
  /** H2 may promote native items and extracted editable raster text/shapes. */
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
