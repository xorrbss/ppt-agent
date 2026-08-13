import { validateDeckMasterContract } from "./validation.ts";

export { validateDeckMasterContract } from "./validation.ts";

export const AUTHORED_VNEXT_MASTER_API_PREFIX =
  "/api/v1/ppt/authored-vnext/master" as const;

export const AUTHORED_VNEXT_MASTER_ENDPOINTS = {
  status: `${AUTHORED_VNEXT_MASTER_API_PREFIX}/status`,
  drafts: `${AUTHORED_VNEXT_MASTER_API_PREFIX}/drafts`,
  logoAsset: `${AUTHORED_VNEXT_MASTER_API_PREFIX}/assets/logo`,
  validate: (masterId: string) =>
    `${AUTHORED_VNEXT_MASTER_API_PREFIX}/drafts/${encodeURIComponent(masterId)}/validate`,
  publish: (masterId: string) =>
    `${AUTHORED_VNEXT_MASTER_API_PREFIX}/drafts/${encodeURIComponent(masterId)}/publish`,
  revision: (masterId: string, revision: number) =>
    `${AUTHORED_VNEXT_MASTER_API_PREFIX}/masters/${encodeURIComponent(masterId)}/revisions/${revision}`,
  preview: `${AUTHORED_VNEXT_MASTER_API_PREFIX}/preview`,
  decks: `${AUTHORED_VNEXT_MASTER_API_PREFIX}/decks`,
  regenerateBody: (deckId: string, slideId: string) =>
    `${AUTHORED_VNEXT_MASTER_API_PREFIX}/decks/${encodeURIComponent(deckId)}/slides/${encodeURIComponent(slideId)}/regenerate-body`,
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
    "fitPolicy",
    "titleStressCorpus",
    "digest",
  ],
  slideDraft: ["slideId", "layoutId", "title", "body"],
  createDraft: ["contract", "visualDirection"],
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
  visibility: "always" | "exceptCover" | "coverOnly";
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
}

export interface RevisionCasRequest {
  expectedRevision: number;
  expectedDigest: MasterDigest;
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
  value = process.env.NEXT_PUBLIC_AUTHORED_VNEXT_MASTER_ENABLED,
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
