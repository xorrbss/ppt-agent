export const AUTHORED_VNEXT_FLOW_SCHEMA_VERSION = "authored-vnext-flow/v1" as const;
export const AUTHORED_VNEXT_SLIDE_COUNT = 90 as const;

export type FlowStep =
  | "materials"
  | "master"
  | "visualDirection"
  | "outlineLayout"
  | "generating"
  | "workbench"
  | "exportRequest";

export type Sha256Digest = `sha256:${string}`;

export interface SourceMaterial {
  materialId: string;
  kind: "prompt" | "document" | "url" | "note";
  label: string;
  digest?: Sha256Digest;
}

export interface MasterDraftReference {
  masterId: string;
  revision: number;
  digest: Sha256Digest;
  validation: {
    valid: boolean;
    errorCodes: readonly string[];
  };
}

export interface PublishedMasterReference {
  masterId: string;
  revision: number;
  digest: Sha256Digest;
  status: "published";
}

/** Deck-specific art direction. It must never be folded into the immutable master. */
export interface VisualDirection {
  directionId: string;
  tone: readonly string[];
  imagery: readonly string[];
  density: "airy" | "balanced" | "dense";
  notes?: string;
}

export interface OutlineSlide {
  slideId: string;
  ordinal: number;
  title: string;
  purpose: string;
  layoutId: string;
}

export interface OutlineLayoutPlan {
  planId: string;
  revision: number;
  slides: readonly OutlineSlide[];
}

export type SlideGenerationStatus = "queued" | "generating" | "ready" | "failed";

export interface SlideGenerationProgress {
  slideId: string;
  ordinal: number;
  status: SlideGenerationStatus;
  percent: number;
  slideRevision?: number;
  errorCode?: string;
}

export interface GenerationRun {
  runId: string;
  deckId: string;
  expectedDeckRevision: number;
  expectedDeckDigest: Sha256Digest;
  slides: readonly SlideGenerationProgress[];
}

export interface ExportRequestState {
  requestId: string;
  format: "pptx" | "pdf";
  status: "requesting" | "queued" | "failed";
  errorCode?: string;
}

export interface FlowFault {
  code: string;
  message: string;
  retryable: boolean;
}

export interface FlowCheckpoint {
  step: FlowStep;
  materials: readonly SourceMaterial[];
  masterDraft?: MasterDraftReference;
  publishedMaster?: PublishedMasterReference;
  visualDirection?: VisualDirection;
  outlineLayout?: OutlineLayoutPlan;
  generation?: GenerationRun;
  exportRequest?: ExportRequestState;
}

export type FlowAvailability =
  | { status: "empty" }
  | { status: "loading"; lastKnownGood?: FlowCheckpoint }
  | { status: "ready"; refreshedAt: string; value: FlowCheckpoint }
  | {
      status: "stale";
      reason: string;
      detectedAt: string;
      value: FlowCheckpoint;
      lastKnownGood: FlowCheckpoint;
    }
  | { status: "error"; error: FlowFault; lastKnownGood?: FlowCheckpoint };

export interface AuthoredVnextFlowState extends FlowCheckpoint {
  schemaVersion: typeof AUTHORED_VNEXT_FLOW_SCHEMA_VERSION;
  mode: "authored-vnext";
  featureEnabled: true;
  availability: FlowAvailability;
}

export interface LegacyFlowState<TLegacy> {
  mode: "legacy";
  featureEnabled: false;
  legacy: TLegacy;
}

export type PresentationFlowState<TLegacy> = AuthoredVnextFlowState | LegacyFlowState<TLegacy>;

export type FlowEvent =
  | { type: "flow.loadRequested" }
  | { type: "flow.loaded"; checkpoint: FlowCheckpoint; refreshedAt: string }
  | { type: "flow.loadFailed"; error: FlowFault }
  | { type: "flow.markedStale"; reason: string; detectedAt: string }
  | { type: "flow.lastKnownGoodRestored"; refreshedAt: string }
  | { type: "materials.confirmed"; materials: readonly SourceMaterial[] }
  | { type: "master.draftSelected"; draft: MasterDraftReference }
  | { type: "master.publishRequested" }
  | { type: "master.publishSucceeded"; published: PublishedMasterReference }
  | { type: "master.publishFailed"; error: FlowFault }
  | { type: "visualDirection.confirmed"; visualDirection: VisualDirection }
  | { type: "outline.succeeded"; plan: OutlineLayoutPlan }
  | { type: "outline.failed"; error: FlowFault }
  | { type: "generation.requested"; runId: string; deckId: string; expectedDeckRevision: number; expectedDeckDigest: Sha256Digest }
  | { type: "generation.slideProgressed"; progress: SlideGenerationProgress }
  | { type: "generation.failed"; error: FlowFault }
  | { type: "workbench.opened" }
  | { type: "export.requested"; requestId: string; format: "pptx" | "pdf" }
  | { type: "export.queued"; requestId: string }
  | { type: "export.failed"; requestId: string; error: FlowFault };

export interface MasterPublishCommand {
  type: "master.publish";
  command: {
    masterId: string;
    expectedRevision: number;
    expectedDigest: Sha256Digest;
  };
}

export interface OutlineLayoutCommand {
  type: "outlineLayout.request";
  command: {
    materials: readonly SourceMaterial[];
    master: PublishedMasterReference;
    visualDirection: VisualDirection;
    slideCount: typeof AUTHORED_VNEXT_SLIDE_COUNT;
  };
}

export interface GenerateDeckCommand {
  type: "deck.generate";
  command: {
    runId: string;
    deckId: string;
    expectedDeckRevision: number;
    expectedDeckDigest: Sha256Digest;
    master: PublishedMasterReference;
    visualDirection: VisualDirection;
    outlineLayout: OutlineLayoutPlan;
  };
}

export interface ExportDeckCommand {
  type: "deck.exportRequest";
  command: {
    requestId: string;
    deckId: string;
    expectedDeckRevision: number;
    expectedDeckDigest: Sha256Digest;
    format: "pptx" | "pdf";
  };
}

export type FlowEffect =
  | { type: "flow.load" }
  | MasterPublishCommand
  | OutlineLayoutCommand
  | GenerateDeckCommand
  | ExportDeckCommand
  | { type: "flow.transitionRejected"; eventType: FlowEvent["type"]; step: FlowStep; reason: string };

export interface FlowTransition {
  state: AuthoredVnextFlowState;
  effects: readonly FlowEffect[];
}

export const AUTHORED_VNEXT_FLOW_SAFETY_CONTRACT = {
  wholeDeckAutosave: false,
  wildcardSlideCommand: false,
  canonicalHtmlWrite: false,
  generationSlideCount: AUTHORED_VNEXT_SLIDE_COUNT,
} as const;
