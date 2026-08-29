import type {
  AuthoredHybridColor,
  AuthoredHybridFallbackReason,
  AuthoredHybridImagePayload,
  AuthoredHybridNativeKind,
  AuthoredHybridRect,
  AuthoredHybridShapePayload,
  AuthoredHybridTextBoxEdgesPx,
  AuthoredHybridTextRole,
  AuthoredHybridTextScript,
  AuthoredHybridTextStyle,
  AuthoredHybridTableEvidence,
} from "./schema.ts";

export interface BrowserTextRunObservation {
  text: string;
  boundsPx: AuthoredHybridRect;
  fragmentRectsPx: AuthoredHybridRect[];
  style: AuthoredHybridTextStyle;
  script?: AuthoredHybridTextScript;
  breakKind?: "soft" | "line" | "paragraph";
  /** Zero-based Chromium visual-line owner for this run. */
  lineIndex?: number;
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
  lineRectsPx?: AuthoredHybridRect[];
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
  /** Declared slides use the authored data-qid byte-for-byte as identity. */
  identityAuthority?: "declared-qid" | "compatibility-ordinal";
  /** Nearest declared data-qid ancestor; DOM paths remain diagnostic only. */
  parentElementId?: string | null;
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
  /** Computed, normalized evidence for bounded declared-table admission. */
  tableEvidence?: AuthoredHybridTableEvidence;
}

/** Serializable identity token carried from extraction into backplate capture. */
export interface AuthoredHybridExpectedPromotedElement {
  id: string;
  identityAuthority?: "declared-qid" | "compatibility-ordinal";
  domPath: string;
  tagName: string;
  sourceIndex: number;
  candidateKind: AuthoredHybridNativeKind | "complex";
  boundsPx: AuthoredHybridRect;
  rotationDeg: number;
  opacity: number;
  contentKey: string;
  suppressWholeElement?: boolean;
  /** The text root's simple fill/border is emitted as a separate native underlay. */
  suppressContainerPaint?: boolean;
}

export interface BrowserRenderReadinessObservation {
  schemaVersion: "browser-render-readiness/v1";
  status: "ready";
  fontStatus: "loaded" | "unsupported";
  imageCount: number;
  decodedImageCount: number;
  unresolvedResources: string[];
  mutationFreeWindowMs: number;
  continuousPaintEpochs: number;
  captureEpoch: number;
  captureMode: "observation" | "hybrid-backplate" | "hybrid-paint-band" | "fidelity";
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
  /** Exact declared QIDs left visible in an isolated transparent paint band. */
  appliedPaintBandElementIds?: string[];
  renderReadiness?: BrowserRenderReadinessObservation;
}
