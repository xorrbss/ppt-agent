import { validateDeckMasterContract } from "./validation.ts";

export { validateDeckMasterContract } from "./validation.ts";

export const AUTHORED_VNEXT_MASTER_API_PREFIX =
  "/api/v1/ppt/authored-vnext/master" as const;

export const AUTHORED_VNEXT_MASTER_ENDPOINTS = {
  status: `${AUTHORED_VNEXT_MASTER_API_PREFIX}/status`,
  drafts: `${AUTHORED_VNEXT_MASTER_API_PREFIX}/drafts`,
  masters: `${AUTHORED_VNEXT_MASTER_API_PREFIX}/masters`,
  companyStandardDefault: `${AUTHORED_VNEXT_MASTER_API_PREFIX}/company-standard/default`,
  register: `${AUTHORED_VNEXT_MASTER_API_PREFIX}/masters/register`,
  published: (masterId: string) =>
    `${AUTHORED_VNEXT_MASTER_API_PREFIX}/masters/${encodeURIComponent(masterId)}/published`,
  logoAsset: `${AUTHORED_VNEXT_MASTER_API_PREFIX}/assets/logo`,
  asset: (assetId: string, digest: MasterDigest) =>
    `${AUTHORED_VNEXT_MASTER_API_PREFIX}/assets/${encodeURIComponent(assetId)}?${new URLSearchParams({ digest }).toString()}`,
  validate: (masterId: string) =>
    `${AUTHORED_VNEXT_MASTER_API_PREFIX}/drafts/${encodeURIComponent(masterId)}/validate`,
  publish: (masterId: string) =>
    `${AUTHORED_VNEXT_MASTER_API_PREFIX}/drafts/${encodeURIComponent(masterId)}/publish`,
  revision: (masterId: string, revision: number) =>
    `${AUTHORED_VNEXT_MASTER_API_PREFIX}/masters/${encodeURIComponent(masterId)}/revisions/${revision}`,
  head: (masterId: string) =>
    `${AUTHORED_VNEXT_MASTER_API_PREFIX}/masters/${encodeURIComponent(masterId)}/head`,
  applyToPresentation: (presentationId: string) =>
    `${AUTHORED_VNEXT_MASTER_API_PREFIX}/presentations/${encodeURIComponent(presentationId)}/apply`,
  preview: `${AUTHORED_VNEXT_MASTER_API_PREFIX}/preview`,
  decks: `${AUTHORED_VNEXT_MASTER_API_PREFIX}/decks`,
  regenerateBody: (deckId: string, slideId: string) =>
    `${AUTHORED_VNEXT_MASTER_API_PREFIX}/decks/${encodeURIComponent(deckId)}/slides/${encodeURIComponent(slideId)}/regenerate-body`,
  aiImportAnalyses: `${AUTHORED_VNEXT_MASTER_API_PREFIX}/ai-import/analyses`,
  aiImportAnalysis: (analysisId: string) =>
    `${AUTHORED_VNEXT_MASTER_API_PREFIX}/ai-import/analyses/${encodeURIComponent(analysisId)}`,
  aiImportValidate: (analysisId: string) =>
    `${AUTHORED_VNEXT_MASTER_API_PREFIX}/ai-import/analyses/${encodeURIComponent(analysisId)}/validate`,
  aiImportSave: (analysisId: string) =>
    `${AUTHORED_VNEXT_MASTER_API_PREFIX}/ai-import/analyses/${encodeURIComponent(analysisId)}/save`,
} as const;

export const MASTER_CONTRACT_KEY_MANIFEST = {
  contract: [
    "contractVersion",
    "masterId",
    "semanticVersion",
    "name",
    "canvas",
    "brandAssets",
    "tokens",
    "fontManifest",
    "furniture",
    "semanticStyles",
    "layouts",
    "customLayouts",
    "semanticElements",
    "aiEditZones",
    "fitPolicy",
    "titleStressCorpus",
    "digest",
  ],
  slideDraft: ["slideId", "layoutId", "title", "body"],
  createDraft: ["contract", "visualDirection", "expectedRevision", "expectedDigest"],
  validateOrPublish: ["expectedRevision", "expectedDigest"],
  preview: ["masterId", "masterRevision", "masterDigest", "slideSpec"],
  previewWire: ["masterId", "masterRevision", "masterDigest", "slide"],
  draftEnvelope: ["masterId", "revision", "digest", "status", "validation"],
  previewEnvelope: [
    "canonicalInputDigest",
    "html",
    "pngRenderRequest",
    "exportPlan",
    "validation",
  ],
} as const;

export type MasterDigest = `sha256:${string}`;
export type MasterStatus = "draft" | "published";
export const REQUIRED_LAYOUT_KINDS = [
  "cover",
  "section",
  "standard",
  "longTitle",
] as const;

export type LayoutKind = (typeof REQUIRED_LAYOUT_KINDS)[number];
export type StressCategory = "ko" | "en" | "mixed" | "numericParenthetical";

export interface Ownership {
  locked: boolean;
  modelWritable: boolean;
}

export interface Bounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

export type SemanticMasterElementType =
  | "text"
  | "image"
  | "shape"
  | "line"
  | "pageNumber"
  | "placeholder";

export type SemanticMasterElementVisibility =
  | "always"
  | "exceptCover"
  | "coverOnly"
  | "layoutOnly"
  | "hidden";

export type SemanticMasterElementScope =
  | { kind: "common" }
  | { kind: "layout"; layoutId: string };

export interface MasterImageCrop {
  /** Normalized source-image coordinates in the inclusive 0..1 range. */
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface SemanticMasterElementStyle {
  fontFamily?: string;
  fontSize?: number;
  fontWeight?: number;
  color?: string;
  textAlign?: "left" | "center" | "right" | "justify";
  verticalAlign?: "top" | "middle" | "bottom";
  fill?: string;
  stroke?: string;
  strokeWidth?: number;
  opacity?: number;
  shape?: "rect" | "ellipse";
  lineDash?: number[];
}

export interface SemanticMasterElementContent {
  text?: string;
  assetId?: string;
  digest?: MasterDigest;
  mediaType?: "image/png" | "image/jpeg" | "image/svg+xml";
  altText?: string;
  fit?: "contain" | "cover";
  crop?: MasterImageCrop;
  placeholderRole?: "title" | "body" | "content" | "image";
  placeholderIndex?: number;
  /** `brand-logo` remains an ordinary image with a semantic role. */
  role?: string;
  /**
   * A layout-scoped brand logo is materialized once per target native layout,
   * while all instances retain the same digest-pinned asset reference.
   * This group identifier is intentionally semantic-contract data (rather
   * than canvas state) so save/reentry and native export retain synchronization
   * intent.
   */
  sharedGroupId?: string;
  /** A layout may opt out of later shared logo position/style changes. */
  sharedOverride?: boolean;
  /**
   * Generation-time binding for an ordinary semantic text element. The
   * canonical master stores the instruction and optional design-time example,
   * never a generated deck value.
   */
  aiBinding?: MasterAITextBinding;
}

export interface MasterAITextBinding {
  instruction: string;
  exampleText?: string;
  emptyBehavior: "hidden" | "blank" | "fallback";
  fallbackText?: string;
  maxChars?: number;
}

export type AIEditZoneContentType =
  | "auto"
  | "text"
  | "cards"
  | "list"
  | "table"
  | "chart"
  | "mixed";

export type AIEditZonePattern =
  | "auto"
  | "numberedCards"
  | "bulletList"
  | "comparisonTable"
  | "metricChart"
  | "iconCards";

export type AIEditZoneSourcePolicy =
  | "documentOnly"
  | "promptAndDocument"
  | "promptOnly";

export type AIEditZoneEmptyBehavior = "hidden" | "blank" | "fallback";
export type AIEditZoneDensity = "sparse" | "balanced" | "dense";

/**
 * A template-facing semantic composition reservation.  Geometry-only v1
 * zones intentionally remain valid: all generation fields are optional so
 * saved masters can be reopened and enriched without a migration.
 */
export interface AIEditZone {
  id: string;
  scope: SemanticMasterElementScope;
  bounds: Bounds;
  /** Natural-language authoring intent. An omitted or blank value is legacy geometry-only. */
  instruction?: string;
  contentType?: AIEditZoneContentType;
  pattern?: AIEditZonePattern;
  sourcePolicy?: AIEditZoneSourcePolicy;
  emptyBehavior?: AIEditZoneEmptyBehavior;
  fallbackText?: string;
  maxChars?: number;
  maxItems?: number;
  density?: AIEditZoneDensity;
  /** Text alignment applied to generated content inside this zone. */
  textAlign?: "left" | "center" | "right";
  /** The renderer may use only the selected template's visual grammar. */
  stylePolicy?: "inheritTemplate";
}

/** Canonical semantic state; Konva node JSON is deliberately not persisted. */
export interface SemanticMasterElement {
  id: string;
  type: SemanticMasterElementType;
  scope: SemanticMasterElementScope;
  bounds: Bounds;
  rotation: number;
  zIndex: number;
  visibility: SemanticMasterElementVisibility;
  editableByUser: boolean;
  editableByAI: boolean;
  locked: boolean;
  style: SemanticMasterElementStyle;
  content: SemanticMasterElementContent;
}

export interface TextMargins {
  leftPt: number;
  rightPt: number;
  topPt: number;
  bottomPt: number;
}

export interface MasterFurniture {
  id: string;
  kind: "logo" | "text" | "line" | "rect" | "pageNumber";
  bounds: Bounds;
  ownership: Ownership;
  text?: string;
  assetId?: string;
  fill?: string;
  stroke?: string;
  visibility: "always" | "exceptCover" | "coverOnly" | "hidden";
}

export interface MasterSlot {
  id: string;
  role: "title" | "body" | "content";
  placeholderIndex: number;
  bounds: Bounds;
  ownership: Ownership;
  style: "title" | "body" | "note" | "rightCallout";
}

export interface MasterLayout {
  id: string;
  name: string;
  kind: LayoutKind;
  slots: MasterSlot[];
}

export interface MasterCustomLayout {
  id: string;
  name: string;
  kind: "custom";
  slots: MasterSlot[];
}

export interface FontManifest {
  primary: string;
  fallbacks: string[];
  windowsFallback: string;
  themeScripts: Record<"latin" | "hang" | "jpan" | "hans" | "hant", string>;
  files: Array<{
    style: string;
    path: string;
    version: string;
    sizeBytes: number;
    sha256: string;
  }>;
  digest: MasterDigest | null;
}

export interface SemanticStyle {
  fontToken: "latin" | "eastAsian" | "complexScript";
  colorToken: "textPrimary" | "textSecondary" | "accent" | "warning";
  fontPt: number;
  minFontPt: number;
  lineHeight: number;
  fontWeight: number;
  maxLines: number;
  margins: {
    leftPt: number;
    rightPt: number;
    topPt: number;
    bottomPt: number;
  };
}

export interface DeckMasterContractV1 {
  contractVersion: "deckmaster-contract/v1";
  masterId: `master:${string}`;
  semanticVersion: string;
  name: string;
  canvas: {
    widthPx: number;
    heightPx: number;
    locale: string;
    safeArea: Bounds;
  };
  brandAssets: {
    logo: BrandAsset | null;
    wordmark: BrandAsset | null;
  };
  tokens: {
    fonts: {
      latin: string;
      eastAsian: string;
      complexScript: string;
      fallbacks: string[];
    };
    colors: Record<
      | "background"
      | "surface"
      | "textPrimary"
      | "textSecondary"
      | "accent"
      | "divider"
      | "warning",
      string
    >;
  };
  fontManifest: FontManifest;
  furniture: Record<
    "logo" | "header" | "footer" | "divider" | "pageNumber",
    MasterFurniture
  >;
  semanticStyles: Record<
    | "eyebrow"
    | "title"
    | "body"
    | "note"
    | "rightCallout"
    | "warning"
    | "caption"
    | "source",
    SemanticStyle
  >;
  layouts: Record<LayoutKind, MasterLayout>;
  /** Optional WYSIWYG extensions keep all pre-editor v1 contracts valid. */
  customLayouts?: MasterCustomLayout[];
  semanticElements?: SemanticMasterElement[];
  /** Optional so v1 masters with legacy slots continue to round-trip intact. */
  aiEditZones?: AIEditZone[];
  fitPolicy: {
    preferredFontPt: number;
    minimumFontPt: number;
    standardMaxLines: number;
    longTitleMaxLines: number;
    promotionLayoutId: string;
    explicitLineBreaks: true;
    uax14Version: "17.0.0";
    tailoring: "ko-space-first-with-id-fallback-v1";
    kinsokuPolicy: "cjk-no-leading-closing-no-trailing-opening";
  };
  titleStressCorpus: TitleStressCase[];
  digest: MasterDigest | null;
}

export interface BrandAsset {
  assetId: string;
  digest: MasterDigest;
  altText: string;
}

export interface TitleStressCase {
  id: string;
  category: StressCategory;
  text: string;
  expectedLines: string[];
}

export interface TitleFitResult {
  engineVersion: "authored-vnext-title-fit/1";
  layoutId: string;
  explicitLines: string[];
  fontFamily: string;
  fontManifestDigest: MasterDigest;
  chosenFontPt: number;
  margins: TextMargins;
  clipped: boolean;
  violations: string[];
}

export interface ExplicitTitle {
  text: string;
  lines: string[];
  lineBreakPolicy: "canonical-explicit-uax14-v1";
  uax14Version: "17.0.0";
  tailoring: "ko-space-first-with-id-fallback-v1";
  kinsokuPolicy: "cjk-no-leading-closing-no-trailing-opening";
}

export interface SlideDraft {
  slideId: `slide:${string}`;
  layoutId: string;
  title: ExplicitTitle;
  body: string;
}

export interface SlideSpecV2 extends SlideDraft {
  schemaVersion: "slidespec/v2";
  revision: number;
  ownership: Ownership;
  masterDigest: MasterDigest;
  titleFit: TitleFitResult;
  contentDigest: MasterDigest;
  specDigest: MasterDigest;
}

export interface DeckSnapshot {
  schemaVersion: "deck-document/v1";
  deckId: `deck:${string}`;
  revision: number;
  digest: MasterDigest;
  masterId: string;
  masterRevision: number;
  masterDigest: MasterDigest;
  visualDirection?: VisualDirection;
  slides: SlideSpecV2[];
}

export interface VisualDirection {
  tone: string[];
  imagery?: string;
  composition?: string;
  notes?: string;
}

export interface ValidationIssue {
  code: string;
  path: string;
  message: string;
}

export interface MasterValidation {
  valid: boolean;
  errors: ValidationIssue[];
  furnitureBodyDuplicates: number;
  writableOverlaps: number;
  titleFitFailures: number;
}

export interface MasterRevisionEnvelope {
  masterId: string;
  revision: number;
  digest: MasterDigest;
  status: MasterStatus;
  validation: MasterValidation;
  contract?: DeckMasterContractV1;
  visualDirection?: VisualDirection;
}

export interface MasterPreviewEnvelope {
  canonicalInputDigest: MasterDigest;
  pngRenderRequest: Record<string, unknown>;
  exportPlan: Record<string, unknown>;
  validation: MasterValidation;
  titleFit?: TitleFitResult;
  slide?: SlideSpecV2;
}

export interface CreateDraftRequest {
  contract: DeckMasterContractV1;
  visualDirection: VisualDirection;
  expectedRevision?: number;
  expectedDigest?: MasterDigest;
}

export interface CompanyStandardDefault {
  schemaVersion: "company-standard-default/v1";
  source: "serverDefault";
  companyStandard: {
    masterId: string;
    revision: number;
    digest: MasterDigest;
  } | null;
  name: string | null;
}

/** Deliberately compact data used by the picker; never render transport pins in UI. */
export interface MasterCatalogSummary {
  masterId: string;
  name: string;
  semanticVersion: string;
  publishedRevision: number | null;
  publishedDigest: MasterDigest | null;
  hasDraft: boolean;
  logo: BrandAsset | null;
  primaryFont: string;
  layoutCount: number;
  updatedTime: string;
}

export interface MasterCatalogPage {
  items: MasterCatalogSummary[];
  nextCursor: string | null;
}

export interface RegisterMasterRequest {
  contract: DeckMasterContractV1;
  visualDirection?: VisualDirection;
}

/** User intent changes deterministic cleanup tolerances, never data sharing. */
export type AiMasterPurpose = "faithful" | "cleanup" | "rebrand";
export type AiMasterAnalysisStatus = "completed" | "partial";
export type AiMasterCandidateAction =
  | "pending"
  | "fixed"
  | "variable"
  | "aiZone"
  | "remove";

export interface AiMasterSource {
  filename: string;
  digest: MasterDigest;
  slideCount: number;
  widthPx: number;
  heightPx: number;
  assets: AiMasterSourceAsset[];
  assetRelationships: AiMasterSourceRelationship[];
}

export interface AiMasterSourceAsset {
  assetId: string;
  digest: MasterDigest;
  mediaType: string;
  byteLength: number;
  partName: string;
}

export interface AiMasterSourceRelationship {
  slideNumber: number;
  relationshipId: string;
  assetId: string;
  crop?: MasterImageCrop;
  fit: "contain" | "cover";
}

export interface AiMasterAnalysisSummary {
  designFamilies: number;
  layoutCandidates: number;
  fontCount: number;
  colorCount: number;
  repeatedElementCount: number;
  aiZoneCandidateCount: number;
  lowConfidenceCount: number;
}

export interface AiMasterTokenCount {
  count: number;
  family?: string;
  hex?: string;
  source?: "slide" | "theme" | "fallback";
}

export interface AiMasterEvidence {
  slideNumber: number;
  bounds: Bounds;
  rotation: number;
  zIndex: number;
  layoutPart?: string;
  masterPart?: string;
  themePart?: string;
}

export interface AiMasterCandidateElement {
  id: string;
  type: "text" | "image" | "shape" | "pageNumber";
  role: string;
  confidence: number;
  text?: string;
  assetId?: string;
  digest?: MasterDigest;
  mediaType?: string;
  crop?: MasterImageCrop;
  fit?: "contain" | "cover";
  differences: string[];
  aiZoneCandidate: boolean;
  evidence: AiMasterEvidence[];
}

export interface AiMasterLayoutDifference {
  code: string;
  message: string;
  severity?: "info" | "warning";
}

export interface AiMasterLayoutCandidate {
  id: string;
  name: string;
  kind: LayoutKind | "closing" | "custom";
  representativeSlide: number;
  slideNumbers: number[];
  confidence: number;
  differences: AiMasterLayoutDifference[];
  slots: Array<{
    id: string;
    role: "title" | "body" | "content" | "image";
    bounds: Bounds;
    placeholderType?: string;
    placeholderIndex?: number;
    fontFamily?: string;
    fontSize?: number;
    color?: string;
  }>;
  layoutPart?: string;
  masterPart?: string;
  themePart?: string;
  excluded: boolean;
}

export interface AiMasterCandidateDecision {
  candidateId: string;
  action: AiMasterCandidateAction;
  instruction?: string;
  exampleText?: string;
  emptyBehavior?: MasterAITextBinding["emptyBehavior"];
  fallbackText?: string;
  maxChars?: number;
  /** A document-scoped user value always wins over a resolved AI value. */
  overrideText?: string;
  variableName?: string;
}

export interface AiMasterLayoutAction {
  action: "merge" | "split" | "exclude" | "include" | "reanalyze";
  layoutIds: string[];
  slideNumbers: number[];
  name?: string;
}

export interface AiMasterReapplyWarning {
  code: string;
  message: string;
  slideNumber?: number;
  layoutId?: string;
  elementId?: string;
  severity: "info" | "warning" | "error";
}

export interface AiMasterReapplyValidation {
  valid: boolean;
  issues: AiMasterReapplyWarning[];
  checkedSlideCount: number;
}

export interface AiMasterAnalysis {
  analysisId: string;
  status: AiMasterAnalysisStatus;
  purpose: AiMasterPurpose;
  source: AiMasterSource;
  summary: AiMasterAnalysisSummary;
  fonts: AiMasterTokenCount[];
  colors: AiMasterTokenCount[];
  repeatedElements: AiMasterCandidateElement[];
  layoutCandidates: AiMasterLayoutCandidate[];
  decisions: AiMasterCandidateDecision[];
  layoutActions: AiMasterLayoutAction[];
  warnings: string[];
  lowConfidence: string[];
  tolerances: Record<string, number>;
  contractDraft: DeckMasterContractV1;
  validation?: AiMasterReapplyValidation;
  masterRevision?: MasterRevisionEnvelope;
}

export interface UpdateAiMasterAnalysisRequest {
  decisions?: AiMasterCandidateDecision[];
  layoutActions?: AiMasterLayoutAction[];
}

export interface SaveAiMasterAnalysisRequest {
  expectedRevision?: number;
  expectedDigest?: MasterDigest;
  masterId?: string;
  contract?: DeckMasterContractV1;
}

export interface RevisionCasRequest {
  expectedRevision: number;
  expectedDigest: MasterDigest;
}

export interface ApplyMasterToPresentationRequest {
  masterPin: {
    masterId: string;
    revision: number;
    digest: MasterDigest;
  };
  expectedPresentationRevision: number;
  expectedDeckRevision: number;
  expectedDeckDigest: MasterDigest;
  idempotencyKey: string;
}

export interface PreviewRequest {
  masterId: string;
  masterRevision: number;
  masterDigest: MasterDigest;
  slideSpec: SlideDraft;
}

export interface CreateDeckRequest {
  masterId: string;
  masterRevision: number;
  masterDigest: MasterDigest;
  visualDirection?: VisualDirection;
  slides: SlideDraft[];
}

export interface RegenerateBodyRequest {
  scope: "bodyOnly";
  expectedDeckRevision: number;
  expectedDeckDigest: MasterDigest;
  expectedMasterDigest: MasterDigest;
  expectedSlideRevision: number;
  layoutId: string;
  body: string;
}

export interface RegenerateBodyResponse {
  candidateDigest: MasterDigest;
  validation: MasterValidation;
  deck: DeckSnapshot;
}

export class MasterContractError extends Error {
  readonly issues: ValidationIssue[];

  constructor(issues: ValidationIssue[]) {
    super(issues.map((issue) => issue.message).join("; "));
    this.name = "MasterContractError";
    this.issues = issues;
  }
}

export function isAuthoredVnextMasterEnabled(
  value = process.env.NEXT_PUBLIC_AUTHORED_VNEXT_MASTER_ENABLED ?? "true",
): boolean {
  return value === "true";
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function isMasterDigest(value: unknown): value is MasterDigest {
  return typeof value === "string" && /^sha256:[0-9a-f]{64}$/.test(value);
}

export function assertDeckMasterContract(contract: DeckMasterContractV1): void {
  const validation = validateDeckMasterContract(contract);
  if (!validation.valid) throw new MasterContractError(validation.errors);
}
