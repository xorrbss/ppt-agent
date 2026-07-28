import type {
  AuthoredHybridColor,
  AuthoredHybridFallbackReason,
  AuthoredHybridImagePayload,
  AuthoredHybridNativeKind,
  AuthoredHybridRect,
  AuthoredHybridShapePayload,
  AuthoredHybridTextBoxEdgesPx,
  AuthoredHybridTextRole,
  AuthoredHybridTextStyle,
} from "./schema.ts";

export interface BrowserTextRunObservation {
  text: string;
  boundsPx: AuthoredHybridRect;
  fragmentRectsPx: AuthoredHybridRect[];
  style: AuthoredHybridTextStyle;
  breakKind?: "soft" | "line" | "paragraph";
}

export interface BrowserTextLayoutObservation {
  boxBoundsPx: AuthoredHybridRect;
  contentBoundsPx: AuthoredHybridRect;
  paintedTextBoundsPx: AuthoredHybridRect | null;
  paddingPx: AuthoredHybridTextBoxEdgesPx;
  borderPx: AuthoredHybridTextBoxEdgesPx;
  marginPx: AuthoredHybridTextBoxEdgesPx;
  rowGapPx: number;
  columnGapPx: number;
  display: string;
  flexDirection: "row" | "row-reverse" | "column" | "column-reverse" | null;
  alignItems: string;
  justifyContent: string;
  textAlignSource: "self" | "inherited" | "default";
  lineCount: number;
  singleLine: boolean;
  paragraphSpacingPx: {
    before: number;
    after: number;
  };
}

export interface BrowserTextObservation {
  role: AuthoredHybridTextRole | "unsupported";
  plainText: string;
  paragraphs: string[];
  style: AuthoredHybridTextStyle;
  runs: BrowserTextRunObservation[];
  layout?: BrowserTextLayoutObservation;
  containerShape?: {
    boundsPx: AuthoredHybridRect;
    shape: BrowserShapeObservation;
  };
}

export interface BrowserImageObservation
  extends Omit<AuthoredHybridImagePayload, "crop"> {
  crop: AuthoredHybridImagePayload["crop"];
}

export interface BrowserShapeObservation
  extends Omit<AuthoredHybridShapePayload, "fill" | "stroke"> {
  fill: AuthoredHybridColor | null;
  stroke: AuthoredHybridColor | null;
}

export interface BrowserElementObservation {
  id: string;
  domPath: string;
  tagName: string;
  sourceIndex: number;
  cssZIndex: number | null;
  boundsPx: AuthoredHybridRect;
  rotationDeg: number;
  opacity: number;
  candidateKind: AuthoredHybridNativeKind | "complex";
  fallbackReasons: AuthoredHybridFallbackReason[];
  text?: BrowserTextObservation;
  image?: BrowserImageObservation;
  shape?: BrowserShapeObservation;
}

/** Serializable identity token carried from extraction into backplate capture. */
export interface AuthoredHybridExpectedPromotedElement {
  id: string;
  domPath: string;
  tagName: string;
  sourceIndex: number;
  candidateKind: AuthoredHybridNativeKind;
  boundsPx: AuthoredHybridRect;
  rotationDeg: number;
  opacity: number;
  contentKey: string;
  suppressWholeElement?: boolean;
  /** The text root's simple fill/border is emitted as a separate native underlay. */
  suppressContainerPaint?: boolean;
}

export interface BrowserAuthoredHybridObservation {
  viewport: {
    widthPx: number;
    heightPx: number;
    devicePixelRatio: number;
  };
  elements: BrowserElementObservation[];
  warnings: string[];
  appliedPromotedElementIds: string[];
  rejectedPromotedElementIds: string[];
}
